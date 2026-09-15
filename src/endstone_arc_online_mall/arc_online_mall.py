import os
import time

from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerJoinEvent, PluginEnableEvent
from endstone.plugin import Plugin

from . import config
from .auction_manager import AuctionManager
from .auction_menus import AuctionMenus
from .mall_menus import MallMenus
from .mall_service import MallService
from .SettingManager import SettingManager


class ARCOnlineMallPlugin(Plugin, MallMenus, AuctionMenus):
    """弧光网上商城：对接按钮/木牌商店的网购 + 全服拍卖行。

    软依赖（鸭子类型按需取用，缺谁降级谁）：
    - arc_core: 经济结算、主菜单按钮
    - arc_button_shop / arc_sign_shop: 商品源与代购下单
    - arc_inventory: 拍卖托管与发货
    - arc_market_economy: 官方动态价（可选）

    MallMenus / AuctionMenus 为表单 UI Mixin，方法直接挂在本类上。
    """

    prefix = "ARCOnlineMall"
    api_version = "0.10"
    load = "POSTWORLD"

    ARC_CORE_MENU_BUTTON_ID = "arc_online_mall:menu"
    ARC_CORE_MENU_TEXT = "网上商城"
    ARC_CORE_MENU_PRIORITY = config.MENU_BUTTON_PRIORITY

    commands = {
        "om": {
            "description": "打开弧光网上商城；管理员可用 /om reload",
            "usages": ["/om", "/om reload"],
            "permissions": ["arc_online_mall.command.common"],
        },
    }

    permissions = {
        "arc_online_mall.command.common": {
            "description": "全员打开网上商城", "default": True,
        },
        "arc_online_mall.command.admin": {
            "description": "/om reload 热重载配置", "default": "op",
        },
    }

    def __init__(self):
        super().__init__()
        self.db = None
        self.settings: SettingManager | None = None
        self.mall: MallService | None = None
        self.auction: AuctionManager | None = None
        self._plugin_cache: dict = {}
        self._arc_core_menu_registered = False

    # ---------- 生命周期 ----------

    def on_load(self) -> None:
        self.logger.info("[ARCOnlineMall] on_load is called!")
        os.makedirs(self.data_folder, exist_ok=True)
        from .DatabaseManager import DatabaseManager

        self.db = DatabaseManager(os.path.join(self.data_folder, "online_mall.db"))
        self._create_tables()
        self.settings = SettingManager(
            base_path=str(self.data_folder), filename="mall_setting.yml")
        self._seed_default_settings()
        self.mall = MallService(self)
        self.auction = AuctionManager(self)

    def _create_tables(self) -> None:
        self.db.create_table("auctions", {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "item_type": "TEXT NOT NULL",
            "item_data": "TEXT NOT NULL",
            "quantity": "INTEGER NOT NULL",
            "seller_xuid": "TEXT NOT NULL",
            "seller_name": "TEXT NOT NULL",
            "start_price": "REAL NOT NULL",
            "min_increment": "REAL NOT NULL",
            "current_price": "REAL",
            "current_bidder_xuid": "TEXT",
            "current_bidder_name": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "created_time": "INTEGER NOT NULL",
            "end_time": "INTEGER NOT NULL",
            "settle_note": "TEXT",
        })
        self.db.create_table("auction_bids", {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "auction_id": "INTEGER NOT NULL",
            "bidder_xuid": "TEXT NOT NULL",
            "bidder_name": "TEXT NOT NULL",
            "increment": "REAL NOT NULL",
            "amount": "REAL NOT NULL",
            "bid_time": "INTEGER NOT NULL",
        })
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auctions_due ON auctions(status, end_time)")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bids_auction ON auction_bids(auction_id)")
        self.db.create_table("pending_deliveries", {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "xuid": "TEXT NOT NULL",
            "player_name": "TEXT NOT NULL",
            "item_data": "TEXT NOT NULL",
            "quantity": "INTEGER NOT NULL",
            "created_time": "INTEGER NOT NULL",
        })
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_xuid ON pending_deliveries(xuid)")

    _SETTING_DEFAULTS = {
        "DELIVERY_FEE_PER_KM": config.DELIVERY_FEE_PER_KM,
        "DELIVERY_UNIT_METERS": config.DELIVERY_UNIT_METERS,
        "CROSS_DIMENSION_FEE": config.CROSS_DIMENSION_FEE,
        "PLATFORM_FEE_RATE": config.PLATFORM_FEE_RATE,
        "PLATFORM_FEE_ENABLED": str(config.PLATFORM_FEE_ENABLED).lower(),
        "AUCTION_MIN_START_PRICE": config.AUCTION_MIN_START_PRICE,
        "AUCTION_MIN_INCREMENT_FLOOR": config.AUCTION_MIN_INCREMENT_FLOOR,
        "AUCTION_DEFAULT_INCREMENT": config.AUCTION_DEFAULT_INCREMENT,
        "AUCTION_MIN_DURATION_MINUTES": config.AUCTION_MIN_DURATION_MINUTES,
        "AUCTION_MAX_DURATION_MINUTES": config.AUCTION_MAX_DURATION_MINUTES,
        "AUCTION_MAX_ACTIVE_PER_PLAYER": config.AUCTION_MAX_ACTIVE_PER_PLAYER,
    }

    def _seed_default_settings(self) -> None:
        """首次启动把默认配置写进 mall_setting.yml，方便服主直接改文件。"""
        for key, value in self._SETTING_DEFAULTS.items():
            if self.settings.get_existing(key) is None:
                self.settings.SetSetting(key, value)

    def on_enable(self) -> None:
        self.logger.info("[ARCOnlineMall] on_enable is called!")
        self.register_events(self)
        try:
            self._register_arc_core_menu()
        except Exception:
            pass
        # 拍卖结算循环：每秒扫一次到期拍卖；停服期间到期的开服即结算
        try:
            self.server.scheduler.run_task(self, self.auction.settle_due, delay=20, period=20)
        except Exception as e:
            self.logger.error(f"[ARCOnlineMall] 启动拍卖结算循环失败: {e}")
        # 重载场景：给已在线玩家补发队列物品
        try:
            for p in self.server.online_players or []:
                self.auction.deliver_pending(p)
        except Exception:
            pass

    def on_disable(self) -> None:
        self.logger.info("[ARCOnlineMall] on_disable is called!")
        try:
            core = self.core_plugin()
            if core is not None and callable(getattr(core, "api_unregister_main_menu_button", None)):
                core.api_unregister_main_menu_button(self.ARC_CORE_MENU_BUTTON_ID)
        except Exception:
            pass
        self._plugin_cache.clear()
        if self.db is not None:
            self.db.close()

    # ---------- 软依赖插件 ----------

    def _get_plugin(self, name: str, probe: tuple, required: bool = False):
        """按名字取插件实例并鸭子探测能力；带缓存，缺失时只警告一次。"""
        cached = self._plugin_cache.get(name)
        if cached is not None:
            return cached
        try:
            plug = self.server.plugin_manager.get_plugin(name)
        except Exception:
            plug = None
        if plug is not None and all(callable(getattr(plug, attr, None)) for attr in probe):
            self._plugin_cache[name] = plug
            self.logger.info(f"[ARCOnlineMall] 已接入 {name}")
            return plug
        if plug is not None:
            self.logger.warning(f"[ARCOnlineMall] {name} 缺少接口 {probe}，按未安装处理")
            plug = None
        if plug is None and required:
            self.logger.warning(f"[ARCOnlineMall] 未找到 {name}，相关功能不可用")
        return None

    def core_plugin(self):
        return self._get_plugin("arc_core", (
            "api_register_main_menu_button",
            "decrease_player_money_by_name",
            "increase_player_money_by_name",
            "api_get_player_money",
            "judge_if_player_has_enough_money_by_name",
        ))

    def shop_plugin(self, name: str):
        return self._get_plugin(name, ("api_get_all_active_shops", "api_purchase_from_shop"))

    def inventory_plugin(self):
        return self._get_plugin("arc_inventory", (
            "api_get_inventory_items", "api_remove_item", "api_give_item_count",
        ))

    def market_economy_plugin(self):
        return self._get_plugin("arc_market_economy", ("api_get_final_price",))

    # ---------- 弧光核心主菜单按钮 ----------

    def _register_arc_core_menu(self) -> bool:
        if self._arc_core_menu_registered:
            return True
        core = self.core_plugin()
        if core is None:
            return False
        try:
            ok = bool(core.api_register_main_menu_button(
                self.ARC_CORE_MENU_BUTTON_ID,
                self.ARC_CORE_MENU_TEXT,
                self.open_mall_main,
                priority=self.ARC_CORE_MENU_PRIORITY,
            ))
        except Exception as e:
            self.logger.warning(f"[ARCOnlineMall] 注册弧光核心主菜单按钮失败: {e}")
            return False
        if ok:
            self._arc_core_menu_registered = True
            self.logger.info("[ARCOnlineMall] 已注册弧光核心主菜单按钮")
        return ok

    # ---------- 配置读取 ----------

    def setting_value(self, key: str) -> str | None:
        return self.settings.get_existing(key) if self.settings is not None else None

    def setting_int(self, key: str, default: int) -> int:
        raw = self.setting_value(key)
        try:
            return int(float(raw)) if raw not in (None, "") else int(default)
        except (TypeError, ValueError):
            return int(default)

    def setting_float(self, key: str, default: float) -> float:
        raw = self.setting_value(key)
        try:
            return float(raw) if raw not in (None, "") else float(default)
        except (TypeError, ValueError):
            return float(default)

    def setting_bool(self, key: str, default: bool) -> bool:
        raw = self.setting_value(key)
        if raw in (None, ""):
            return bool(default)
        return str(raw).strip().lower() in ("true", "1", "yes", "on")

    def reload_settings(self) -> None:
        self.settings.Reload()
        self._seed_default_settings()

    # ---------- 通用工具 ----------

    @staticmethod
    def xuid_of(player) -> str:
        try:
            return str(
                getattr(player, "xuid", None)
                or getattr(player, "uuid", None)
                or getattr(player, "name", "")
            )
        except Exception:
            return ""

    def player_by_xuid(self, xuid: str):
        try:
            for p in self.server.online_players or []:
                if self.xuid_of(p) == xuid:
                    return p
        except Exception:
            return None
        return None

    def core_money(self, player) -> float | None:
        core = self.core_plugin()
        if core is None:
            return None
        try:
            return float(core.api_get_player_money(str(getattr(player, "name", "")) or ""))
        except Exception:
            return None

    def toast(self, player, title: str, content: str) -> None:
        try:
            player.send_toast(title, content)
        except Exception:
            try:
                player.send_message(f"[{title}] {content}")
            except Exception:
                pass

    def broadcast(self, message: str) -> None:
        try:
            self.server.broadcast_message(message)
        except Exception:
            for p in getattr(self.server, "online_players", []) or []:
                try:
                    p.send_message(message)
                except Exception:
                    pass

    # ---------- 事件 ----------

    @event_handler()
    def on_player_join(self, event: PlayerJoinEvent):
        try:
            self.auction.deliver_pending(event.player)
        except Exception as e:
            self.logger.error(f"[ARCOnlineMall] 补发离线物品失败: {e}")

    @event_handler()
    def on_plugin_enable(self, event: PluginEnableEvent):
        """弧光核心晚于本插件加载时，补注册主菜单按钮。"""
        try:
            if str(getattr(getattr(event, "plugin", None), "name", "")) == "arc_core":
                self._register_arc_core_menu()
        except Exception:
            pass

    # ---------- 命令 ----------

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != config.MALL_COMMAND:
            return True
        if args and str(args[0]).lower() == "reload":
            if not getattr(sender, "is_op", False):
                sender.send_message("[ARCOnlineMall] 需要 OP 权限")
                return True
            self.reload_settings()
            sender.send_message("[ARCOnlineMall] 配置已重载：配送费、手续费、拍卖参数即时生效")
            return True
        if hasattr(sender, "send_form"):
            self.open_mall_main(sender)
        else:
            sender.send_message("[ARCOnlineMall] 用法: /om（仅玩家）")
        return True
