# EndStone-ARC-Online-Mall 弧光网上商城

Endstone 插件：**网上商城 + 全服拍卖行**。商城商品直接对接 [弧光按钮商店](https://github.com/ARC-Game-Club/EndstoneMC-ARC-Button-Shop-Plugin) 与 [弧光木牌商店](https://github.com/ARC-Game-Club/EndstoneMC-ARC-Sign-Shop-Plugin) 的全部出售向商店，按距离收取配送费；拍卖行支持全服播报、阶梯加价与**强制扣款（允许负债）**。

## 玩法

玩家输入 `/om` 或点击弧光核心主菜单的「网上商城」按钮：

- **浏览/搜索商品**：聚合按钮商店 + 木牌商店的所有出售向商品（`sell` / `both`），显示单价与距离。
- **商品详情**：显示商店**维度与坐标**、单价、库存、店主、配送费、手续费、合计——嫌运费贵可以记下坐标自己去买。
- **购买**：输入数量下单，商品立即送货上门。

**拍卖行**：玩家选背包物品发起拍卖（起拍价、每次最低加价、时长），全服播报；其他玩家点进拍卖页出价，输入框默认就是最低加价额，点确认即 +最低加价，也可手动输入更大金额（低于最低加价无效）。截止时按最高价强制收款。

## 费用规则

| 项目 | 规则 | 默认 |
|---|---|---|
| 配送费 | `ceil(距离 ÷ 计价段长) × 段费`，例：5350 米 → 6 段 → 600 元 | 1000 米 100 元 |
| 跨维度费 | 距离费之外**额外**加收固定费用 | 2000 元 |
| 平台手续费 | 按商品原价收取，运费不计 | 5% |

以上全部可在 `plugins/ARCOnlineMall/mall_setting.yml` 中修改，OP 用 `/om reload` 热重载。

**负债规则**：网上商城购物**不允许负债**（余额不足直接拒绝）；拍卖则是全服唯一**允许负债**的玩法——截止时对最高出价者不校验余额、直接扣款，余额不足扣成负数（欠银行），并在成交播报中公示欠款金额。

## 拍卖细节

- 发起即**托管**：拍品从背包扣下存入商城，流拍/取消时退还；有玩家出价后不可取消。
- 出价 = 基准价（当前最高价，无人出价时为起拍价）+ 加价额；卖家不能竞拍自己的拍卖，当前最高出价者不能给自己抬价。
- 结算每秒扫描一次：流拍退还拍品并全服播报；成交则扣款赢家（可负债）、货款全给卖家（拍卖不收手续费）、拍品发给赢家。
- 赢家/流拍退还时若不在线，进入补发队列，重新进服自动到账；背包满也会暂存补发。
- 服务器重启不丢拍卖：全部落库，停服期间到期的开服即结算。

## 配置项（mall_setting.yml）

```ini
DELIVERY_FEE_PER_KM=100.0          # 每计价段配送费（元）
DELIVERY_UNIT_METERS=1000          # 计价段长（米），向上取整
CROSS_DIMENSION_FEE=2000.0         # 跨维度固定加收（元）
PLATFORM_FEE_RATE=0.05             # 平台手续费率
PLATFORM_FEE_ENABLED=true          # 是否收取手续费
AUCTION_MIN_START_PRICE=1.0        # 起拍价下限
AUCTION_MIN_INCREMENT_FLOOR=1.0    # 最低加价下限
AUCTION_DEFAULT_INCREMENT=1000.0   # 发起表单默认加价额
AUCTION_MIN_DURATION_MINUTES=5     # 拍卖最短时长
AUCTION_MAX_DURATION_MINUTES=4320  # 拍卖最长时长
AUCTION_MAX_ACTIVE_PER_PLAYER=5    # 单人同时进行拍卖上限
```

## 依赖

全部为软依赖（缺哪个降级哪个，主菜单按钮在弧光核心加载后自动补注册）：

| 插件 | 用途 |
|---|---|
| `arc_core` | 经济结算、主菜单按钮（结算必需） |
| `arc_button_shop` / `arc_sign_shop` | 商品源与代购下单 |
| `arc_inventory` | 拍卖托管与发货 |
| `arc_market_economy` | 官方动态价商品实时报价（可选） |

注意：商店自身交易税（按钮/木牌商店的 `trade_tax_rate`）按商店插件规则照常收取，与本插件的平台手续费相互独立。

## 安装

```bash
pip install dist/endstone_arc_online_mall-<版本>-py2.py3-none-any.whl
```

## 命令

| 命令 | 权限 | 说明 |
|---|---|---|
| `/om` | 全员 | 打开网上商城 |
| `/om reload` | OP | 热重载配置 |

## 离线测试

```bash
python scripts\smoke_test_online_mall.py
```

覆盖：配送费向上取整（5350m→600 元）、跨维度加价、手续费、防负债拦截、下单失败退款、拍卖托管/出价校验/强制扣款负债结算/流拍退还/离线补发队列/取消规则/UI 渲染/主菜单按钮三件套。

## License

MIT
