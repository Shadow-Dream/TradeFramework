# 20 项基础指标、OHLCV v3 与首轮可视化接入报告

- 原始问题：

  > 可以都添加一下，对于volume数据，尝试找一些有volume的数据源来测试
  >
  > 你自己决定采用的subagent，第一次尝试较为开放性的任务可以用强agent，试了一遍没问题之后用廉价agent重复即可

- 问题时间：2026-08-27（America/New_York；会话接口未提供该消息的精确时分）
- 回答时间：2026-08-27 16:13:32 EDT（America/New_York，UTC-04:00）

## 已确认事实

本轮按“强 agent 建样板和审计边界 → 廉价 agent 批量重复 → 强 agent/主 agent 复核并修正”的顺序完成。强 agent 首先解决了多 Pane、可复用指标 Module 模板、OHLCV 数据契约和 `offsetBars`；廉价 agent 批量补指标、目录映射与 v3 骨架；强 agent 随后发现并阻断了 RSI/ATR/ROC 口径偏差、Supertrend/Parabolic SAR 状态机、EODHD downloader 交叉接错、v3 仍走 v2 adapter、Web 端 VWMA 端口名错误等问题。最终交付不是直接接受批量生成结果，而是经过真实安装定义、编译器和确定性 fixture 的二次验证。

Basic workspace 现在只有一个集中式 `Indicators` 入口，目录项是可参数化类型而不是固定周期变体。同一个 SMA 条目可以添加多次并分别配置 20、50 等 Length；参数默认值、类型、上下界和枚举来自已安装 Module 的 JSON Schema。该形态与 TradingView 官方文档所述的集中指标入口以及 Source/Length 参数化输入一致：[Indicators 入口](https://www.tradingview.com/pine-script-docs/primer/first-steps/)、[Inputs 机制](https://www.tradingview.com/pine-script-docs/concepts/inputs/)。

目录共有 20 项，覆盖 9 个主图叠加项和 11 个独立副图项：

| 位置 | 指标 | 主要输入 | 可调参数 |
|---|---|---|---|
| 主图 | SMA、EMA、WMA | close | Length |
| 主图 | VWMA | close、volume | Length |
| 主图 | Bollinger Bands | close | Length、StdDev |
| 主图 | Supertrend | high、low、close | ATR length、Multiplier |
| 主图 | Parabolic SAR | high、low、close | Start、Increment、Maximum |
| 主图 | Anchored VWAP | HLC3、volume、显式 reset | Anchor：dataset/week/month/year |
| 主图 | Ichimoku Cloud | high、low、close | Conversion、Base、Span B |
| 副图 | RSI、ROC、CCI、Williams %R | close、HLC3 或 HLC | Length |
| 副图 | MACD | close | Fast、Slow、Signal |
| 副图 | ATR | high、low、close | Length |
| 副图 | Stochastic | high、low、close | Length、%K smoothing、%D length |
| 副图 | DMI/ADX | high、low、close | DI length、ADX smoothing |
| 副图 | OBV、Volume | close/volume 或 volume | 无固定周期变体 |
| 副图 | MFI | high、low、close、volume | Length |

其中 RSI 和 ATR 已改为 Wilder RMA，ROC 改为百分数且默认周期 9，Stochastic 改为 14/3/3 结构。CCI、Williams %R、DMI/ADX、Supertrend、MFI、Parabolic SAR、Anchored VWAP 和 Ichimoku 均有独立 BuiltIn Module 与严格配置 Schema。公式审查以 TradingView 的 [RSI](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/)、[ATR](https://www.tradingview.com/support/solutions/43000734653-how-are-adr-and-atr-calculated/)、[CCI](https://www.tradingview.com/support/solutions/43000502001-commodity-channel-index-cci/)、[DMI/ADX](https://www.tradingview.com/support/solutions/43000589099-average-directional-index-adx/)、[Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/)、[MFI](https://www.tradingview.com/support/solutions/43000502348-money-flow-mfi/) 和 [VWAP](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/) 说明为主要依据。

可视化层现在按 `placement + series[]` 描述指标：overlay 进入价格主图，oscillator 每实例建立独立 Pane；MACD 使用 histogram 加两条 line，Volume 使用 histogram，Parabolic SAR 使用 scatter。各 Pane 用真实 visible time range 双向同步，画线控制器仍只绑定主图。Ichimoku 的领先/滞后线通过显式整数 `offsetBars` 投影到已有交易时间键，不用固定毫秒数伪造周末或休市日期。

真实浏览器首跑还发现并修复了一个仅靠编译测试无法发现的问题：指标目录的可用性检查仍调用已删除的 `requireBasicLineDefinition()`，ReferenceError 被 UI 捕获后使 20 个按钮全部 disabled。现已改为按每个 descriptor 校验 line/histogram/scatter 的 `requireBasicSeriesDefinitions(indicator)`，并加入永久回归断言。

Volume 测试源采用 EODHD 官方公开 demo EOD API，当前 Web 明确选择 `eodhd-demo-us`，只开放 AAPL、TSLA、VTI、AMZN 的日线实验。官方接口返回 OHLCV，并说明 OHLC 为未调整值、`adjusted_close` 另列、volume 为拆分调整口径，美国 EOD 数据通常在收盘后 15 分钟内更新：[EODHD EOD API](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes/)。本轮没有把真实响应写入 Git，也没有把 token 写入浏览器、Dataset 元数据或日志。

为避免日线完整成交量在收盘时刻被提前使用，新增的 v3 CSV 精确字段为 `time,eventTime,open,close,high,low,volume`：`eventTime` 表示 regular close，`time` 表示保守的可用时间 `eventTime + 15m`。v3 有独立 conformance、Dataset adapter、OHLCV sampler 和 `basic-price-bar-selector`；旧 v2 adapter、五列 Dataset、sampler 与 close selector 均保留，可继续复现旧资源。Engine 执行语义没有因本轮改变。

## 确定性计算

全量目录实验从仓库实际安装的 Module definitions 出发，依次添加 20/20 项后得到：

| 计算项 | 结果 |
|---|---:|
| 指标实例 | 20 |
| 主图叠加指标 | 9 |
| 独立 oscillator Pane | 11 |
| 总 Pane 数（含主图） | 12 |
| 指标输出 DataKey | 33 |
| 共享 bar selector 输出 | 11 |
| 编译后的 Visualization contracts | 89 |
| Ichimoku 默认偏移 | 0、0、+26、+26、-26 |
| VWAP reset 映射 | dataset、week、month、year 四种均精确绑定 |

旧工作区兼容实验先创建旧 `basic-price-close-selector` + SMA，再添加 EMA：旧 SMA 保留，共享 source 原位迁移为 `basic-price-bar-selector`，输出精确扩为 11 个；删除最后一个指标后共享 source 才移除。非法 VWAP anchor、Supertrend multiplier=0、保留 DataKey 被外部 Module 占用，均在写入前 fail closed。

2026-08-27 的实际 EODHD demo AAPL 请求（31 个自然日有界窗口）得到 23 根唯一、严格升序日线，日期范围 2026-07-27 至 2026-08-26；volume 最小 25,869,800，最大 132,489,100；原始响应 SHA-256 为 `499a91b8c28711c85da6090e7b2b061f3f98b912a1e113d9da4548421c09f963`。解析器验证了 finite positive OHLC、OHLC bounds、finite non-negative volume、日期唯一性和严格升序；该原始响应仅在临时空间中使用。

本轮最终验证结果如下。各组有覆盖交叉，因此不把它们相加伪装成“唯一测试总数”：

| 验证组 | 结果 |
|---|---:|
| 技术指标公式、warm-up、零分母、非法 bar、SDK 生命周期 | 22/22 通过 |
| EODHD/v3 Dataset、Sampler、API、协议、E2E、资源边界 | 69/69 通过 |
| Visualizer contracts 与 `offsetBars` 完整集 | 30/30 通过 |
| 全 20 项目录真实合同编译与 Pane/legacy 回归 | 4/4 通过 |
| Basic Web 子系统 | 23/23 通过，1 条 Python escape deprecation warning |
| Chart 稀疏点/最小权限 smoke | 通过 |
| 三个核心 JavaScript 文件 `node --check` | 通过 |
| 预览 HTTP | 直连 `/basic-workflow` 返回 303 到登录页，符合受保护页面预期 |
| 隔离 Chrome 功能 smoke | 20 项可用；6 个实例、4 个 Pane、canvas 与最终 spec 通过；严格零 console 断言因 1 条未归因通用 404 未通过 |

隔离 Chrome 使用真实 Engine handler、临时认证、120 根带 volume 的 OHLCV fixture。修复后页面成功添加 SMA20、SMA50、Ichimoku(9/26/52)、RSI14、MACD(12/26/9) 和 Volume；DOM 测得 4 个 Pane、3 个 oscillator Pane、0 个空 Pane，最终 spec 包含 2 个 SMA Module、其余 4 个指标 Module、共享 bar selector，以及 Ichimoku 偏移 `0,0,26,26,-26`。`pageErrors=[]`。两张临时截图为 `/tmp/trade-basic-indicators-catalog.png` 与 `/tmp/trade-basic-indicators-panes.png`，已人工确认目录无裁切、主图与三个副图均有实际图形。

## 解释、反证与不确定性

“20 项已接入”表示 Module 契约、参数目录、图表 series、真实编译和数据绑定均已完成，不表示逐像素或所有初始化细节与 TradingView 私有实现完全一致。当前确定性 fixture 覆盖主要公式和边界，但 EMA/MACD 的起始种子等跨平台细节仍可能造成早期样本差异；因此页面不应宣称是 TradingView 的数值副本。

Ichimoku 的 `offsetBars=+N` 只投影到 Dataset 已存在的未来交易时间键。最后 N 根 leading span 因没有未来交易日键而被丢弃；这避免伪造周末，但也意味着当前图表不会在数据尾部向未来留白绘制云层。要显示尾部未来云，需要由交易所日历提供明确未来 session，而不是自行加 24 小时。

23 根 demo 数据足以验证 Volume、OBV、短周期 VWMA/MFI/VWAP 的接线，却不足以让 52 日 Ichimoku Span B 等长 warm-up 指标稳定出值。该样本是连通性测试，不是策略有效性测试，也不是 BUY/SELL 建议。

EODHD 只提供本次下载时的 current vintage，不能重建某个历史时点当时可见的 revision；当前实现也只保守拒绝已知美国休市/典型早收盘日，不声称拥有完整交易所历史日历。另一个重要口径是 raw OHLC 与 split-adjusted volume 的 provider-native 混合；跨拆分做 VWMA 时必须显式选择一致的价格调整策略，不能静默混合。现有 v3 因此适合首轮可视化和计算接线，不足以宣称 point-in-time 严格回测。

EODHD 条款允许相应订阅范围内的私有分析，但限制再分发和面向他人展示；团队、公司或对外产品应取得匹配的商业授权。本轮公开 demo 只用于受控测试，不能据此推导生产数据授权：[EODHD Terms](https://eodhd.com/financial-apis/terms-conditions/)。现有 Nasdaq 网页 endpoint 未作为新 Web 默认自动源，因为 Nasdaq 当前法律条款禁止自动或手工捕获其站点数据：[Nasdaq Legal](https://www.nasdaq.com/legal)。

仓库原有浏览器 smoke 硬编码 `nasdaq-us-snapshot` 和 v2 五列 fixture，已经不能作为 v3/volume 验收证据；本轮没有修改该共享脚本来隐藏这种版本不匹配，而是在 `/tmp` 使用隔离 v3 驱动完成首轮视觉审阅。该驱动的最后一个“零 console error”断言仍因 Chromium 输出的一条不带 URL 的 `Failed to load resource: ... 404` 未通过；因断言顺序，应用 origin HTTP ≥400 的空数组断言尚未执行。仓库正式浏览器 smoke 把相同通用 404 作为 favicon 噪声处理，因此 `/favicon.ico` 是有力推断，但本轮没有 URL 证据，不能说 100% 确定，也不能宣称严格 smoke 全绿。20 项目录中实际点击了 6 项，其余 14 项由真实合同编译覆盖，仍应在后续完整浏览器矩阵中逐项点击。

## 单一下一实验或 Proposal

只做一个有界的真实浏览器实验：在具备合规授权、至少一年日线的 AAPL 或 VTI Dataset 上，同时加载 SMA(20)、SMA(50)、VWMA(20)、Volume、OBV、MFI(14)、Anchored VWAP(month)、RSI(14)、MACD(12/26/9) 和 Ichimoku(9/26/52)，记录每条 series 的首个非空时间、末值、Pane/scale 与独立参考实现的差异；接受标准是数据因果时间不倒置、volume 口径一致、同类多实例身份独立、所有副图时间范围同步，且差异能由初始化规则明确解释。该实验一次只验证可视化与计算一致性，不同时引入交易策略或绩效结论。
