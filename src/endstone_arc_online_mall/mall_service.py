"""网上商城服务层：聚合按钮/木牌商店商品、运费与手续费计算、防负债下单。

跨插件约定（全部鸭子类型，缺谁降级谁）：
- arc_button_shop / arc_sign_shop: api_get_all_active_shops() / api_purchase_from_shop(shop_id, buyer_xuid, qty)
- arc_core: 余额查询/判断/加减钱（按玩家名）
- arc_market_economy: api_get_final_price（官方动态价，可选）
"""

import json
import math
import re

from . import config

# 可上网购的商店类型：sell=玩家出售店, both=官方出售+回收。
# buy=收购店、barter=以物易物需要玩家本人在场，不进商城。
SELLABLE_SHOP_TYPES = ("sell", "both")

# 内置主世界/下界/末地的中文名；自定义维度不在表内，
# 默认原样显示维度 ID，服主可在 DIMENSION_ALIASES 里起中文名。
DIMENSION_LABELS = {
    "overworld": "主世界",
    "nether": "下界",
    "the_nether": "下界",
    "the_end": "末地",
    "end": "末地",
}


def parse_dimension_aliases(raw: str) -> dict[str, str]:
    """解析 DIMENSION_ALIASES 配置：`维度ID=中文名`，分号/换行分隔。"""
    aliases: dict[str, str] = {}
    for part in re.split(r"[;；\n,，]", raw or ""):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            aliases[key] = value
    return aliases


def dimension_label(name: str, aliases: dict[str, str] | None = None) -> str:
    text = str(name or "").strip()
    if not text:
        return "未知维度"
    key = text.lower()
    if key in DIMENSION_LABELS:
        return DIMENSION_LABELS[key]
    if aliases and key in aliases:
        return aliases[key]
    return text


class MallService:
    def __init__(self, plugin):
        self.plugin = plugin

    # ---------- 商店聚合 ----------

    def list_sell_shops(self) -> list[dict]:
        """合并两个商店插件的活跃商店，只保留可网购的出售向条目。

        每条附带 source 字段（'arc_button_shop' / 'arc_sign_shop'），item_data 已解析为 dict。
        """
        rows: list[dict] = []
        for name in ("arc_button_shop", "arc_sign_shop"):
            plug = self.plugin.shop_plugin(name)
            if plug is None:
                continue
            try:
                raw = plug.api_get_all_active_shops() or []
            except Exception as e:
                self.plugin.logger.warning(f"[ARCOnlineMall] 读取 {name} 商店失败: {e}")
                continue
            for row in raw:
                try:
                    shop = dict(row)
                except Exception:
                    continue
                if str(shop.get("shop_type") or "") not in SELLABLE_SHOP_TYPES:
                    continue
                if not int(shop.get("is_active") or 0):
                    continue
                if not int(shop.get("is_infinite") or 0) and int(shop.get("stock") or 0) <= 0:
                    continue
                try:
                    shop["item_info"] = json.loads(shop.get("item_data") or "{}")
                except Exception:
                    shop["item_info"] = {}
                shop["source"] = name
                rows.append(shop)
        rows.sort(key=lambda s: (self.unit_price(s), str(s.get("item_type") or "")))
        return rows

    def find_shop(self, source: str, shop_id: int) -> dict | None:
        """按来源插件 + 商店 id 精确取店（避免列表中途变化的错位）。"""
        plug = self.plugin.shop_plugin(str(source))
        if plug is None:
            return None
        try:
            shops = plug.api_get_all_active_shops() or []
        except Exception:
            return None
        for row in shops:
            if int(row.get("id") or 0) == int(shop_id):
                shop = dict(row)
                try:
                    shop["item_info"] = json.loads(shop.get("item_data") or "{}")
                except Exception:
                    shop["item_info"] = {}
                shop["source"] = source
                return shop
        return None

    # ---------- 价格与费用 ----------

    def unit_price(self, shop: dict) -> float:
        """当前单件售价。官方动态价店实时询问 arc_market_economy，失败回落库内单价。"""
        if str(shop.get("pricing_mode") or "") != "official":
            return round(float(shop.get("unit_price") or 0), 2)
        market = self.plugin.market_economy_plugin()
        if market is None:
            return round(float(shop.get("unit_price") or 0), 2)
        try:
            price = market.api_get_final_price(
                str(shop.get("item_type") or ""),
                "sell",
                float(shop.get("discount_percent") or 0),
            )
            return round(float(price), 2)
        except Exception:
            return round(float(shop.get("unit_price") or 0), 2)

    def stock_of(self, shop: dict) -> int:
        if int(shop.get("is_infinite") or 0):
            return 2_147_483_647
        return max(0, int(shop.get("stock") or 0))

    def distance_info(self, shop: dict, player) -> tuple[float, bool]:
        """返回 (玩家到商店的三维直线距离/米, 是否同维度)。"""
        try:
            loc = player.location
            px, py, pz = float(loc.x), float(loc.y), float(loc.z)
            dim = str(getattr(getattr(loc, "dimension", None), "name", "") or "")
        except Exception:
            px = py = pz = 0.0
            dim = ""
        same_dim = dim.lower() == str(shop.get("dimension") or "").lower()
        dx = float(shop.get("x") or 0) - px
        dy = float(shop.get("y") or 0) - py
        dz = float(shop.get("z") or 0) - pz
        return math.sqrt(dx * dx + dy * dy + dz * dz), same_dim

    def delivery_fee(self, shop: dict, player) -> tuple[float, bool]:
        """配送费 = ceil(距离/段长) × 段费；跨维度额外加固定费。返回 (费用, 是否跨维度)。

        例：距离 5350 米、段长 1000、段费 100 → 6 段 → 600 元。
        """
        distance, same_dim = self.distance_info(shop, player)
        unit = max(1, self.plugin.setting_int("DELIVERY_UNIT_METERS", config.DELIVERY_UNIT_METERS))
        per_unit = self.plugin.setting_float("DELIVERY_FEE_PER_KM", config.DELIVERY_FEE_PER_KM)
        fee = math.ceil(distance / unit) * per_unit
        cross = not same_dim
        if cross:
            fee += self.plugin.setting_float("CROSS_DIMENSION_FEE", config.CROSS_DIMENSION_FEE)
        return round(fee, 2), cross

    def platform_fee(self, goods_price: float) -> float:
        if not self.plugin.setting_bool("PLATFORM_FEE_ENABLED", config.PLATFORM_FEE_ENABLED):
            return 0.0
        rate = self.plugin.setting_float("PLATFORM_FEE_RATE", config.PLATFORM_FEE_RATE)
        return round(max(0.0, float(goods_price)) * rate, 2)

    def quote(self, shop: dict, player, quantity: int) -> dict:
        """一次性算清购买报价：商品款、手续费、配送费、总价、距离与跨维度标记。"""
        quantity = max(1, int(quantity))
        unit = self.unit_price(shop)
        goods = round(unit * quantity, 2)
        fee = self.platform_fee(goods)
        delivery, cross = self.delivery_fee(shop, player)
        return {
            "unit_price": unit,
            "quantity": quantity,
            "goods": goods,
            "platform_fee": fee,
            "delivery_fee": delivery,
            "cross_dimension": cross,
            "total": round(goods + fee + delivery, 2),
        }

    # ---------- 下单（防负债：先足额校验，失败退款，绝不扣成负数） ----------

    def purchase(self, player, source: str, shop_id: int, quantity: int) -> tuple[bool, str]:
        quantity = int(quantity)
        if quantity < 1:
            return False, "至少购买 1 件"

        shop = self.find_shop(source, shop_id)
        if shop is None:
            return False, "商店不存在或已关闭"
        if quantity > self.stock_of(shop):
            return False, f"库存不足（剩余 {self.stock_of(shop)} 件）"

        core = self.plugin.core_plugin()
        if core is None:
            return False, "弧光核心未加载，无法结算"

        buyer_xuid = self.plugin.xuid_of(player)
        buyer_name = str(getattr(player, "name", "") or "")
        bill = self.quote(shop, player, quantity)

        # 1) 足额校验：商品款 + 手续费 + 配送费
        try:
            affordable = bool(core.judge_if_player_has_enough_money_by_name(buyer_name, bill["total"]))
        except Exception:
            affordable = False
        if not affordable:
            return False, f"余额不足，需 {bill['total']:.2f} 元（含手续费与配送费）"

        # 2) 先扣平台侧费用（手续费 + 配送费），货款由商店插件自行收取
        surcharge = round(bill["platform_fee"] + bill["delivery_fee"], 2)
        if surcharge > 0:
            try:
                ok = bool(core.decrease_player_money_by_name(buyer_name, surcharge, notify=False))
            except Exception as e:
                ok = False
                self.plugin.logger.warning(f"[ARCOnlineMall] 收取平台费用失败: {e}")
            if not ok:
                return False, "扣款失败，请稍后再试"

        # 3) 商店插件完成发货、收货款、店主分成、回滚
        plug = self.plugin.shop_plugin(source)
        try:
            ok, msg = plug.api_purchase_from_shop(int(shop_id), buyer_xuid, quantity)
        except Exception as e:
            ok, msg = False, str(e)
        if not ok:
            if surcharge > 0:
                try:
                    core.increase_player_money_by_name(buyer_name, surcharge, notify=False)
                except Exception:
                    self.plugin.logger.error(
                        f"[ARCOnlineMall] 购买失败且退款失败！玩家={buyer_name} 金额={surcharge}"
                    )
            return False, f"购买失败：{msg or '商店拒绝交易'}"

        return True, bill

    # ---------- 文案 ----------

    @staticmethod
    def item_display(shop: dict) -> str:
        info = shop.get("item_info") or {}
        name = str(info.get("name") or shop.get("item_type") or "未知物品")
        return name

    @staticmethod
    def distance_text(distance_m: float, cross: bool) -> str:
        if cross:
            return f"跨维度（直线 {distance_m:.0f} 米）"
        if distance_m >= 1000:
            return f"{distance_m / 1000:.1f} 公里"
        return f"{distance_m:.0f} 米"
