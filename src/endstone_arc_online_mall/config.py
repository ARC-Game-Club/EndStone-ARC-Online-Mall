"""弧光网上商城 默认配置。

所有带 [settings] 标注的项都会在首次启动时写入
plugins/ARCOnlineMall/mall_setting.yml，可在文件中直接修改，
OP 用 /om reload 热重载。未标注的项改默认值请直接改本文件。
"""

# ---------- 网上商城 ----------

DELIVERY_FEE_PER_KM = 100.0          # [settings] 每计价段配送费（元）
DELIVERY_UNIT_METERS = 1000          # [settings] 配送费计价段长（米），按段向上取整
CROSS_DIMENSION_FEE = 2000.0         # [settings] 跨维度固定加收费用（元），叠加在距离费之上
PLATFORM_FEE_RATE = 0.05             # [settings] 网购平台手续费率（按商品原价收取）
PLATFORM_FEE_ENABLED = True          # [settings] 是否收取平台手续费

# ---------- 拍卖行 ----------

AUCTION_MIN_START_PRICE = 1.0        # [settings] 起拍价下限（元）
AUCTION_MIN_INCREMENT_FLOOR = 1.0    # [settings] 卖家可设置的"每次最低加价"下限（元）
AUCTION_DEFAULT_INCREMENT = 1000.0   # [settings] 发起拍卖时表单默认的最低加价（元）
AUCTION_MIN_DURATION_MINUTES = 5     # [settings] 拍卖最短时长（分钟）
AUCTION_MAX_DURATION_MINUTES = 4320  # [settings] 拍卖最长时长（分钟），默认 3 天
AUCTION_MAX_ACTIVE_PER_PLAYER = 5    # [settings] 单人同时进行的拍卖数量上限

# 发起拍卖表单里提供的时长快捷选项（分钟）
AUCTION_DURATION_CHOICES = [10, 30, 60, 360, 1440]

# ---------- 通用 ----------

PAGE_SIZE = 15                       # 商城/拍卖列表每页条数
MALL_COMMAND = "om"                  # 主命令
MENU_BUTTON_PRIORITY = 6             # 弧光核心主菜单按钮排序，越小越靠前
