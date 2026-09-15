# -*- coding: utf-8 -*-
"""arc_online_mall 冒烟测试：网购费用链路 + 拍卖全生命周期（stub endstone + 假插件）。

运行：python scripts\\smoke_test_online_mall.py（无需安装 endstone）
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _endstone_stub as stub
from _endstone_stub import Location
stub.add_src_to_path()

from endstone_arc_online_mall import config
from endstone_arc_online_mall.DatabaseManager import DatabaseManager
from endstone_arc_online_mall.SettingManager import SettingManager
from endstone_arc_online_mall.mall_service import MallService
from endstone_arc_online_mall.auction_manager import AuctionManager
from endstone_arc_online_mall.mall_menus import MallMenus
from endstone_arc_online_mall.auction_menus import AuctionMenus
from endstone_arc_online_mall.arc_online_mall import ARCOnlineMallPlugin

print("== 0) 模块 import 成功")
assert ARCOnlineMallPlugin.commands["om"]


# ---------- Fake 对象 ----------

class FakeLogger:
    def __init__(self):
        self.errors, self.warnings = [], []

    def info(self, m): pass
    def warning(self, m): self.warnings.append(m)
    def error(self, m): self.errors.append(m)


class FakeDimension:
    def __init__(self, name):
        self.name = name


class FakeLocation(Location):
    pass


class FakePlayer:
    def __init__(self, name, xuid, x=0.0, y=64.0, z=0.0, dim_name="overworld", bag=None):
        self.name = name
        self.xuid = xuid
        self.location = FakeLocation(FakeDimension(dim_name), x, y, z)
        self.bag = list(bag or [])          # item_info dict 列表
        self.toasts = []
        self.forms = []
        self.messages = []

    def send_toast(self, title, content):
        self.toasts.append((title, content))

    def send_message(self, msg):
        self.messages.append(msg)

    def send_form(self, form):
        self.forms.append(form)


class FakeArcCore:
    """余额按玩家名记账；decrease 不校验余额（可扣成负数），与真核心一致。"""
    name = "arc_core"

    def __init__(self, money=None):
        self.money = dict(money or {})
        self.registered_buttons = {}
        self.unregistered = []

    def api_register_main_menu_button(self, button_id, text, on_click, priority=6, icon=None):
        self.registered_buttons[str(button_id)] = (text, on_click, priority)
        return True

    def api_unregister_main_menu_button(self, button_id):
        self.unregistered.append(str(button_id))
        self.registered_buttons.pop(str(button_id), None)
        return True

    def api_get_player_money(self, player_name="", xuid=""):
        return round(float(self.money.get(player_name, 0.0)), 2)

    def judge_if_player_has_enough_money_by_name(self, player_name, amount):
        return self.money.get(player_name, 0.0) >= amount

    def decrease_player_money_by_name(self, player_name, amount, notify=True):
        self.money[player_name] = round(self.money.get(player_name, 0.0) - amount, 2)
        return True

    def increase_player_money_by_name(self, player_name, amount, notify=True, **kw):
        self.money[player_name] = round(self.money.get(player_name, 0.0) + amount, 2)
        return True


class FakeShopPlugin:
    def __init__(self, name, shops, core=None, xuid_to_name=None):
        self.name = name
        self.shops = shops
        self.purchases = []
        self.core = core                      # 模拟真实行为：成交时商店插件自行扣买家货款
        self.xuid_to_name = xuid_to_name or {}

    def api_get_all_active_shops(self):
        return [dict(s) for s in self.shops]

    def api_purchase_from_shop(self, shop_id, buyer_xuid, quantity):
        self.purchases.append((shop_id, buyer_xuid, quantity))
        for s in self.shops:
            if s["id"] == int(shop_id):
                if s.get("stock", 0) < quantity:
                    return False, "库存不足"
                s["stock"] -= quantity
                if self.core is not None:
                    name = self.xuid_to_name.get(buyer_xuid, buyer_xuid)
                    self.core.decrease_player_money_by_name(
                        name, round(float(s.get("unit_price") or 0) * quantity, 2))
                return True, "ok"
        return False, "商店不存在"


class FakeInventoryPlugin:
    def __init__(self):
        self.given = []

    def api_get_inventory_items(self, player):
        return [dict(i) for i in player.bag]

    def api_remove_item(self, player, item_info):
        want_type = item_info.get("type")
        removed = 0
        rest = []
        for it in player.bag:
            if it.get("type") == want_type and removed < item_info.get("count", 0):
                take = min(int(it.get("count", 0)), item_info.get("count", 0) - removed)
                removed += take
                if int(it.get("count", 0)) - take > 0:
                    rest.append({**it, "count": int(it["count"]) - take})
            else:
                rest.append(it)
        player.bag = rest
        return removed

    def api_give_item_count(self, player, item_info):
        self.given.append((player.name, dict(item_info)))
        player.bag.append(dict(item_info))
        return int(item_info.get("count", 1))


class FakeMarketEconomy:
    def __init__(self, prices):
        self.prices = prices

    def api_get_final_price(self, item_type, side, discount_percent=0):
        return self.prices.get(item_type, 0)


class FakePluginManager:
    def __init__(self, plugins):
        self._plugins = plugins

    def get_plugin(self, name):
        return self._plugins.get(name)


class FakeScheduler:
    def __init__(self):
        self.tasks = []

    def run_task(self, plugin, fn, delay=0, period=0):
        self.tasks.append((fn, delay, period))

    def run_all(self):
        tasks, self.tasks = self.tasks, []
        for fn, _d, _p in tasks:
            fn()


class FakeServer:
    def __init__(self, plugins, players=()):
        self.plugin_manager = FakePluginManager(plugins)
        self.online_players = list(players)
        self.broadcasts = []
        self.scheduler = FakeScheduler()

    def broadcast_message(self, msg):
        self.broadcasts.append(msg)


def make_plugin(plugins, players=()):
    SettingManager.setting_dict.clear()
    plug = object.__new__(ARCOnlineMallPlugin)
    plug.logger = FakeLogger()
    tmp = tempfile.mkdtemp()
    plug.db = DatabaseManager(os.path.join(tmp, "mall.db"))
    plug._create_tables()
    plug.settings = SettingManager(base_path=tmp, filename="mall_setting.yml")
    plug._seed_default_settings()
    plug.mall = MallService(plug)
    plug.auction = AuctionManager(plug)
    plug._plugin_cache = {}
    plug._arc_core_menu_registered = False
    plug.server = FakeServer(plugins, players)
    return plug


def diamond_shop(shop_id=1, **kw):
    row = {
        "id": shop_id, "shop_uuid": f"u{shop_id}", "owner_xuid": "xowner",
        "owner_name": "店主张三", "shop_type": "sell",
        "x": 0, "y": 64, "z": 0, "dimension": "overworld",
        "item_type": "minecraft:diamond",
        "item_data": '{"type":"minecraft:diamond","name":"钻石","count":1,"data":0,"enchants":{},"lore":[]}',
        "quantity": 1, "unit_price": 100.0, "stock": 64,
        "is_active": 1, "is_infinite": 0, "pricing_mode": "manual", "discount_percent": 0,
    }
    row.update(kw)
    return row


DIA = {"type": "minecraft:diamond", "name": "钻石", "count": 8, "data": 0, "enchants": {}, "lore": []}
SWORD = {"type": "minecraft:diamond_sword", "name": "钻石剑", "count": 1, "data": 0,
         "enchants": {}, "lore": [], "nbt_b64": "abc=="}

print("\n== 1) 配置种子写入 + 读取")
core = FakeArcCore({"Steve": 100000.0, "Alex": 0.0})
inv = FakeInventoryPlugin()
btn = FakeShopPlugin("arc_button_shop", [diamond_shop()], core=core, xuid_to_name={"x1": "Steve"})
plugin = make_plugin({"arc_core": core, "arc_button_shop": btn, "arc_inventory": inv})
assert plugin.setting_value("DELIVERY_FEE_PER_KM") == "100.0"
assert plugin.setting_float("DELIVERY_FEE_PER_KM", 0) == 100.0
assert plugin.setting_bool("PLATFORM_FEE_ENABLED", False) is True
assert os.path.exists(plugin.settings.setting_file_path), "mall_setting.yml 未生成"
print("   mall_setting.yml 自动生成，默认值 ok")

print("\n== 2) 配送费：5350 米向上取整收 6 公里")
steve = FakePlayer("Steve", "x1", x=0, z=0)
shop = diamond_shop(x=5350)   # 同维度，距离 5350 米
fee, cross = plugin.mall.delivery_fee(shop, steve)
assert fee == 600.0 and not cross, (fee, cross)
print("   5350m -> 600 元 ok")

print("\n== 3) 跨维度：距离费 + 2000 固定费")
shop_nether = diamond_shop(dimension="nether")
fee, cross = plugin.mall.delivery_fee(shop_nether, steve)  # 玩家在主世界(0,64,0)，商店在(0,64,0)坐标差0
assert cross and fee == 2000.0, (cross, fee)
shop_nether = diamond_shop(dimension="nether", x=5350)
fee, cross = plugin.mall.delivery_fee(shop_nether, steve)
assert cross and fee == 2600.0, (cross, fee)
print("   跨维度 2000 + 距离费叠加 ok")

print("\n== 3.5) 自定义维度：ID 原样显示 + 别名配置 + 跨维度费")
from endstone_arc_online_mall.mall_service import dimension_label, parse_dimension_aliases
assert dimension_label("myaddon:skyland") == "myaddon:skyland"          # 未配置别名 → 完整 ID
assert dimension_label("overworld") == "主世界"
aliases = parse_dimension_aliases("myaddon:skyland=天空岛; the_end=末地")
assert dimension_label("MyAddon:Skyland", aliases) == "天空岛"           # 别名大小写不敏感
assert dimension_label("overworld", aliases) == "主世界"                 # 内置名优先级不受影响
sky_plugin = make_plugin({"arc_core": core, "arc_button_shop": btn, "arc_inventory": inv})
sky_plugin.settings.SetSetting("DIMENSION_ALIASES", "myaddon:skyland=天空岛")
sky_shop = diamond_shop(dimension="myaddon:skyland", x=5350)
fee, cross = sky_plugin.mall.delivery_fee(sky_shop, steve)              # 主世界 → 自定义维度 = 跨维度
assert cross and fee == 2600.0, (cross, fee)
assert sky_plugin.dimension_aliases() == {"myaddon:skyland": "天空岛"}
print("   自定义维度显示与计费 ok")

print("\n== 4) 平台手续费 5% 按商品价")
bill = plugin.mall.quote(shop, steve, 1)          # 商品 100 元
assert bill["goods"] == 100.0 and bill["platform_fee"] == 5.0, bill
bill10 = plugin.mall.quote(shop, steve, 10)       # 10 件 1000 元，运费只收一次
assert bill10["goods"] == 1000.0 and bill10["platform_fee"] == 50.0
assert bill10["delivery_fee"] == 600.0 and bill10["total"] == 1650.0, bill10
print("   手续费/整单运费 ok")

print("\n== 5) 商品聚合：sell 进列表，buy/无库存剔除，官方动态价生效")
sign = FakeShopPlugin("arc_sign_shop", [
    diamond_shop(2, shop_type="buy"),
    diamond_shop(3, stock=0),
    diamond_shop(4, shop_type="both", pricing_mode="official",
                 item_type="minecraft:emerald", item_data='{"type":"minecraft:emerald","name":"绿宝石"}'),
])
plugin2 = make_plugin({"arc_core": core, "arc_button_shop": btn, "arc_sign_shop": sign,
                       "arc_inventory": inv,
                       "arc_market_economy": FakeMarketEconomy({"minecraft:emerald": 77.0})})
rows = plugin2.mall.list_sell_shops()
ids = [r["id"] for r in rows]
assert 1 in ids and 4 in ids and 2 not in ids and 3 not in ids, ids
em = next(r for r in rows if r["id"] == 4)
assert plugin2.mall.unit_price(em) == 77.0, "官方动态价未生效"
print(f"   聚合 {ids}，动态价 77 ok")

print("\n== 6) 下单防负债：余额不足拒绝且分文不动")
poor = FakePlayer("Alex", "x2", x=0, z=0)   # Alex 余额 0
ok, msg = plugin2.mall.purchase(poor, "arc_button_shop", 1, 1)
assert not ok and "余额不足" in msg, (ok, msg)
assert core.money["Alex"] == 0.0 and btn.purchases == [], "拒绝时不应扣钱/不应下单"
print("   余额不足拦截 ok")

print("\n== 7) 下单成功：先扣平台费，商店收货款；失败自动退平台费")
ok, bill = plugin2.mall.purchase(steve, "arc_button_shop", 1, 1)   # 商品100+手续费5+近距运费0=105
assert ok, bill
assert core.money["Steve"] == round(100000 - bill["total"], 2), core.money["Steve"]
assert btn.purchases[-1] == (1, "x1", 1)
total_before = core.money["Steve"]
ok, msg = plugin2.mall.purchase(steve, "arc_button_shop", 1, 65)   # 库存64 → 商店拒绝
assert not ok and "库存不足" in msg, msg
assert core.money["Steve"] == total_before, "失败后平台费未退回"
print(f"   成功扣款 {bill['total']} 元、失败退款 ok")

print("\n== 8) 发起拍卖：托管扣物 + 全服播报 + 入库")
seller = FakePlayer("Steve", "x1", bag=[dict(SWORD)])
plugin3 = make_plugin({"arc_core": core, "arc_button_shop": btn, "arc_inventory": inv}, players=[seller])
ok, msg = plugin3.auction.create_auction(seller, dict(SWORD), 1, 10000, 1000, 60)
assert ok, msg
assert seller.bag == [], "托管后背包应扣空"
assert len(plugin3.server.broadcasts) == 1 and "起拍价 10000.00" in plugin3.server.broadcasts[0]
row = plugin3.db.query_one("SELECT * FROM auctions")
assert row["status"] == "active" and row["start_price"] == 10000 and row["min_increment"] == 1000
aid = row["id"]
ok, msg = plugin3.auction.create_auction(seller, {"type": "minecraft:dirt", "count": 1}, 1, 10, 0.5, 60)
assert not ok and "最低加价" in msg, msg   # 加价下限校验
print(f"   拍卖 #{aid} 托管+播报 ok")

print("\n== 9) 出价校验：低于最低加价无效、卖家禁拍、领先者禁自抬")
bidder = FakePlayer("Alex", "x2")
core.money["Alex"] = 0.0   # 故意 0 余额：拍卖允许
ok, msg = plugin3.auction.place_bid(bidder, aid, 500)
assert not ok and "低于最低加价" in msg, msg
ok, msg = plugin3.auction.place_bid(seller, aid, 1000)
assert not ok and "自己" in msg, msg
ok, amount = plugin3.auction.place_bid(bidder, aid, 1000)
assert ok and amount == 11000.0, (ok, amount)      # 基准=起拍价10000 + 1000
ok, msg = plugin3.auction.place_bid(bidder, aid, 2000)
assert not ok and "最高出价者" in msg, msg
ok, amount = plugin3.auction.place_bid(FakePlayer("Bob", "x3"), aid, 5000)
assert ok and amount == 16000.0, (ok, amount)      # 手动加价 5000 >= 1000 有效
bids = plugin3.db.query_all("SELECT * FROM auction_bids WHERE auction_id=?", (aid,))
assert len(bids) == 2, bids
print("   加价规则全链 ok")

print("\n== 10) 到期结算：强制扣款可负债 + 卖家收款 + 拍品发货")
seller_balance_before = core.money["Steve"]
plugin3.db.update("auctions", {"end_time": int(time.time()) - 5}, "id=?", (aid,))
plugin3.auction.settle_due()
row = plugin3.db.query_one("SELECT * FROM auctions WHERE id=?", (aid,))
assert row["status"] == "settled", row
assert core.money["Bob"] == -16000.0, core.money      # 0 余额强扣成负数=欠银行
assert core.money["Steve"] == round(seller_balance_before + 16000.0, 2), core.money
final = plugin3.server.broadcasts[-1]
assert "拍卖成交" in final and "欠银行" in final, final
print(f"   播报：{final}")

print("\n== 11) 流拍：退托管物品")
loser = FakePlayer("Steve", "x1", bag=[dict(DIA)])
plugin3.server.online_players = [loser]
plugin3.server.broadcasts.clear()
ok, msg = plugin3.auction.create_auction(loser, dict(DIA), 8, 100, 10, 10)
assert ok, msg
aid2 = plugin3.db.query_one("SELECT id FROM auctions ORDER BY id DESC")["id"]
plugin3.db.update("auctions", {"end_time": int(time.time()) - 1}, "id=?", (aid2,))
plugin3.auction.settle_due()
row = plugin3.db.query_one("SELECT * FROM auctions WHERE id=?", (aid2,))
assert row["status"] == "settled"
assert "流拍" in plugin3.server.broadcasts[-1], plugin3.server.broadcasts[-1]
assert any(i["type"] == "minecraft:diamond" for i in loser.bag), "流拍未退还拍品"
print("   流拍退还 ok")

print("\n== 12) 离线赢家：进补发队列，上线自动补发")
loser.bag.append(dict(SWORD))   # 再拍一把剑
ok, msg = plugin3.auction.create_auction(loser, dict(SWORD), 1, 50, 5, 10)
assert ok, msg
aid3 = plugin3.db.query_one("SELECT id FROM auctions ORDER BY id DESC")["id"]
plugin3.auction.place_bid(FakePlayer("Hero", "x9"), aid3, 100)   # Hero 不在 online_players
plugin3.db.update("auctions", {"end_time": int(time.time()) - 1}, "id=?", (aid3,))
plugin3.auction.settle_due()
pend = plugin3.db.query_all("SELECT * FROM pending_deliveries WHERE xuid='x9'")
assert len(pend) == 1 and pend[0]["quantity"] == 1, pend
hero = FakePlayer("Hero", "x9")
plugin3.auction.deliver_pending(hero)
assert any(i["type"] == "minecraft:diamond_sword" for i in hero.bag)
assert plugin3.db.query_all("SELECT * FROM pending_deliveries WHERE xuid='x9'") == []
print("   离线补发队列 ok")

print("\n== 13) 取消拍卖：无出价可取消退物，有出价拒绝")
ok, msg = plugin3.auction.create_auction(loser, dict(DIA), 4, 100, 10, 10)
aid4 = plugin3.db.query_one("SELECT id FROM auctions ORDER BY id DESC")["id"]
ok, msg = plugin3.auction.cancel_auction(bidder, aid4)
assert not ok and "只有卖家" in msg, msg
bag_before = len(loser.bag)
ok, msg = plugin3.auction.cancel_auction(loser, aid4)
assert ok and len(loser.bag) == bag_before + 1, msg
ok, _ = plugin3.auction.create_auction(loser, dict(DIA), 4, 100, 10, 10)
aid5 = plugin3.db.query_one("SELECT id FROM auctions ORDER BY id DESC")["id"]
plugin3.auction.place_bid(bidder, aid5, 100)
ok, msg = plugin3.auction.cancel_auction(loser, aid5)
assert not ok and "不可取消" in msg, msg
print("   取消规则 ok")

print("\n== 14) 表单 UI 冒烟：主菜单/列表/详情/出价框默认值")
plugin3.server.online_players = [loser]
loser.forms.clear()
plugin3.open_mall_main(loser)
assert loser.forms and loser.forms[-1].buttons, "主菜单无按钮"
plugin3.open_auction_main(loser)
plugin3.show_auction_list(loser, 0)
plugin3.show_shop_detail(loser, "arc_button_shop", 1)
plugin3.show_bid_form(loser, aid5)
forms = loser.forms
# 出价输入框默认值 = 该拍卖的最低加价（aid5 最低加价 10）
modal = [f for f in forms if type(f).__name__ == "ModalForm"][-1]
assert modal.controls[1].default_value == "10", modal.controls[1].default_value
print(f"   共渲染 {len(forms)} 个表单 ok")

print("\n== 15) 弧光核心主菜单按钮注册/注销三件套")
assert plugin3._register_arc_core_menu() is True
assert "arc_online_mall:menu" in core.registered_buttons
plugin3.on_disable()
assert "arc_online_mall:menu" in core.unregistered
print("   注册/注销 ok")

print("\nALL ARC-ONLINE-MALL SMOKE TESTS PASSED")
