# Lean 如何实现量化交易建模

这份文档只按 Lean 的**实际实现**来总结。

不是先假设一个外部分析框架，再把 Lean 往里套。

这里的主线完全来自源码里的真实对象关系和调用顺序，重点回答：

1. Lean 把一个量化算法实际实现成了哪些对象
2. 哪些对象是核心默认结构，哪些是上层可选框架
3. 它怎样把“研究范围、观点、仓位、风控、执行、交易规则”拆开
4. 每类对象在自然语言里到底代表什么

---

## 1. Lean 里最核心的对象不是策略函数，而是 `QCAlgorithm`

如果只看实现，Lean 对量化算法的第一个核心建模，不是 `AlphaModel`，甚至不是 `OnData()`，而是：

- `QCAlgorithm` 是算法运行时的**总根对象**

见：

- [QCAlgorithm.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.cs:70)

它在构造函数里直接把一整套量化交易所需的核心对象 new 出来，见：

- [QCAlgorithm.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.cs:174)

按源码顺序，`QCAlgorithm` 默认持有这些东西：

- `TimeKeeper`
  - 这是算法内部的“时钟总管”。Lean 不是简单用一个 `DateTime.Now` 跑完全局，而是需要同时处理算法时区、交易所时区、UTC 时间。`TimeKeeper` 负责维护这些时间视角，让系统知道“现在从纽约股票市场看是几点、从算法本地时区看是几点、从全局 UTC 看又是几点”。
- `SubscriptionManager`
  - 这是算法侧的“订阅登记簿”。你加了什么标的、要什么分辨率、要不要盘前盘后、要不要填充缺口，都会先登记在这里。它更像“我要看哪些数据”的声明处，不是实际喂数器。
- `SecurityManager`
  - 这是算法当前所有交易标的的总表。你加进来的 `SPY`、`NVDA`、期货、期权，最后都会变成一个个 `Security` 对象放在这里。可以把它理解成“算法眼里的市场对象仓库”。
- `SecurityTransactionManager`
  - 这是算法发订单时最先碰到的对象。策略说“买 100 股”时，不是直接去找券商，而是先进入这里。它负责把订单请求整理好、分配订单 id、转交给后面的执行系统。
- `SecurityPortfolioManager`
  - 这是“账户资产负债表”。它管理你当前持有什么仓位、现金有多少、总资产是多少、浮盈浮亏是多少。策略里看到的组合价值、仓位状态，基本都由它统一维护。
- `SignalExportManager`
  - 这是一个“信号对外输出器”。如果算法不是只在 Lean 内部跑，而是想把自己的观点或信号导出到外部系统，这个对象就是出口。平时用不到时它存在感不强，但从架构上看，它说明 Lean 允许“信号生成”和“信号消费”分离。
- `BrokerageModel`
  - 这是 Lean 对“交易制度”的抽象，不是网络连接器。它定义的是：这个账户默认杠杆多少、手续费怎么收、订单怎么成交、保证金怎么计算。可以把它理解成“你在什么交易规则下玩这场游戏”。
- `RiskFreeInterestRateModel`
  - 这是一个提供无风险利率的对象。很多地方未必每天都显式用到它，但一旦涉及估值、贴现、某些金融模型，系统就需要一个统一的“基准利率来源”。
- `NotificationManager`
  - 这是算法往外发消息的统一入口，比如发提醒、日志型通知、运行状态消息。它不是核心交易逻辑，但属于“算法与人/外部系统沟通”的通道。
- `UniverseManager`
  - 这是股票池/合约池的总表。Lean 里不是直接保存“当前有哪些股票”，而是先保存“这些股票来自哪些 universe 规则”。`UniverseManager` 管的是这些 universe 对象本身，以及它们当前带来的标的集合。
- `UniverseDefinitions`
  - 这是一个“造 universe 的工具入口”。它让你能更方便地定义股票池、ETF 成分股池、期权链、期货链等。可以把它理解成“股票池构建语法”的统一门面。
- `UniverseSettings`
  - 这是 universe 的默认参数包，比如默认分辨率、默认杠杆、是否填充缺口、调仓节奏等。它相当于“以后新建股票池时，如果你不特别说明，就按这套默认规则来”。
- `ScheduleManager`
  - 这是算法里的“定时任务系统”。它负责表达像“每天开盘后 10 分钟做一次再平衡”“每周五收盘前检查风控”这类事情。它的意义是把“定时触发的逻辑”和“数据触发的逻辑”分开。
- `TradeBuilder`
  - 这是“交易结果整理器”。订单成交后，系统并不只关心单笔成交，还关心完整的一笔交易是怎么开仓、怎么平仓、最终赚了还是亏了。`TradeBuilder` 就负责把零碎成交拼成完整交易记录。
- `SecurityInitializer`
  - 这是“标的装配工”。新加一个标的时，Lean 不会只给你一个 symbol，还要给它装上手续费模型、成交模型、保证金模型、滑点模型等。`SecurityInitializer` 就负责把这些规则模型挂到具体标的上。
- `CandlestickPatterns`
  - 这是 Lean 内置的一套 K 线形态分析工具集合，方便策略直接用蜡烛图形态做判断。它不是运行时骨架的一部分，更像是给策略层准备的技术分析工具箱。
- `TradingCalendar`
  - 这是“市场日程表”。它知道哪些天开市、哪些天休市、哪些天半日交易。它存在的意义是让策略可以基于真实交易日而不是自然日来思考问题。
- `OptionChainProvider`
  - 这是“期权合约目录查询器”。策略想知道某个标的当前有哪些行权价、哪些到期日、有哪些期权合约时，会从这里拿。可以把它理解成“期权市场的合约清单入口”。
- `FutureChainProvider`
  - 这是“期货合约目录查询器”。当你研究期货时，系统需要知道某个品种现在有哪些可交易合约、主力和近月有哪些选择，这个对象就是那个入口。
- `HistoryRequestFactory`
  - 这是“历史数据请求模板器”。策略里调用历史数据接口时，真正发给引擎的不是一句自然语言，而是一个规范化请求对象。这个工厂负责把你的需求整理成引擎能执行的历史查询请求。

这个事实非常重要：

- Lean 不是先有“框架模型”，再有算法对象
- 而是先有一个巨大的 `QCAlgorithm` 运行时对象
- 框架模型是后来挂在这个对象上的

所以从实现上看，Lean 的根建模是：

- **一个统一算法上下文对象**

不是：

- 一组完全对等、彼此独立的策略组件

---

## 2. Framework 是挂在 `QCAlgorithm` 上的一层可选上层抽象

Lean 的第二层建模，才是大家常说的 Algorithm Framework。

Framework 的相关属性不在 `QCAlgorithm.cs` 主文件里，而在：

- [QCAlgorithm.Framework.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.Framework.cs:31)

这里可以直接看到，Framework 只是 `QCAlgorithm` 上的几组属性：

- `UniverseSelection`
- `Alpha`
- `Insights`
- `PortfolioConstruction`
- `Execution`
- `RiskManagement`

见：

- [QCAlgorithm.Framework.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.Framework.cs:45)

更关键的是，`QCAlgorithm` 构造函数里给这些属性设置的默认值是：

- `SetAlpha(new NullAlphaModel())`
- `SetPortfolioConstruction(new NullPortfolioConstructionModel())`
- `SetExecution(new ImmediateExecutionModel())`
- `SetRiskManagement(new NullRiskManagementModel())`
- `SetUniverseSelection(new NullUniverseSelectionModel())`

见：

- [QCAlgorithm.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.cs:247)

这说明：

- Framework 在 Lean 里不是底层内核
- 它是**挂在算法根对象上的一套可选规范化策略流程**

换句话说：

- Lean 核心先解决“算法对象如何运行”
- Framework 再解决“算法逻辑如何规范拆分”

这和很多人直觉里的“Lean 就是五段式框架”不完全一样。

更准确的说法应该是：

- Lean 核心是 `QCAlgorithm`
- 五段式是 `QCAlgorithm` 的一个上层逻辑组织方式

---

## 3. Framework 在实现里真正是怎样跑起来的

如果只按源码调用链看，Framework 的核心入口有两个：

- `FrameworkPostInitialize()`
- `OnFrameworkData(Slice slice)`

见：

- [QCAlgorithm.Framework.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.Framework.cs:81)
- [QCAlgorithm.Framework.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.Framework.cs:101)

### 3.1 `FrameworkPostInitialize()`

这个阶段做的事情很简单：

- 调 `UniverseSelection.CreateUniverses(this)`
- 把产生的 universes `AddUniverse(...)`

也就是说，在实现上：

- universe selection 是 Framework 最先介入的地方

它不是先出信号，而是先定义研究和订阅范围。

### 3.2 `OnFrameworkData(Slice slice)`

这是 Framework 的核心流转函数。

它按源码顺序做这些事：

1. 如果到了刷新时间：
   - 重新执行 `UniverseSelection.CreateUniverses(this)`
   - 删除旧 universe
   - 添加新 universe
2. `Insights.Step(UtcTime)`
   - 推进 insight 生命周期
3. 如果这次 `slice` 没有数据，直接返回
4. 调 `Alpha.Update(this, slice)`
   - 生成 insights
5. 调 `ProcessInsights(insights)`

见：

- [QCAlgorithm.Framework.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.Framework.cs:106)

### 3.3 `ProcessInsights(...)`

这是五段式里真正串起来的地方。

源码顺序是：

1. `PortfolioConstruction.CreateTargets(this, insights)`
2. 把 targets 写进 `security.Holdings.Target`
3. `RiskManagement.ManageRisk(this, targets)`
4. 把风控覆盖后的 targets 再写回 `security.Holdings.Target`
5. `Execution.Execute(this, riskAdjustedTargets)`

见：

- [QCAlgorithm.Framework.cs](/file/share/data_jyz/trade/Lean/Algorithm/QCAlgorithm.Framework.cs:176)

所以从实现角度讲，Framework 不是抽象概念，而是非常具体的一条链：

- UniverseSelection
- Alpha
- PortfolioConstruction
- RiskManagement
- Execution

而且它们的输入输出很清楚：

- `UniverseSelection` 输出 `Universe`
- `Alpha` 输出 `Insight`
- `PortfolioConstruction` 输出 `IPortfolioTarget`
- `RiskManagement` 输出修正后的 `IPortfolioTarget`
- `Execution` 消费 `IPortfolioTarget`

这才是 Lean 在源码里真正实现出来的策略逻辑规范化。

---

## 4. 这五类模型在 Lean 里的真实含义

下面不按外部理论定义，而按 Lean 的接口和调用方式解释。

### 4.1 `IUniverseSelectionModel`

接口：

- [IUniverseSelectionModel.cs](/file/share/data_jyz/trade/Lean/Algorithm/Selection/IUniverseSelectionModel.cs:17)

它在 Lean 里的真实职责是：

- 告诉算法当前应该拥有哪些 `Universe`
- 决定什么时候刷新这些 `Universe`

它不是直接“选股票名单”那么简单，而是：

- 先创建 universe 对象
- 再由 universe 机制驱动后续订阅、筛选和增删标的

通俗理解：

- 这层不是最终股票列表
- 而是“研究范围规则”的定义器

### 4.2 `IAlphaModel`

接口：

- [IAlphaModel.cs](/file/share/data_jyz/trade/Lean/Algorithm/Alphas/IAlphaModel.cs:17)

它在 Lean 里的真实职责是：

- 输入 `Slice`
- 输出 `Insight`

Lean 不是让信号层直接下单，而是要求它先输出一个标准化观点对象。

所以在 Lean 里，`Alpha` 更接近：

- 观点生成器
- 预测生成器

而不是：

- 交易执行器

常见实现包括：

- `ConstantAlphaModel`
- `EmaCrossAlphaModel`
- `MacdAlphaModel`
- `RsiAlphaModel`
- `HistoricalReturnsAlphaModel`
- `PearsonCorrelationPairsTradingAlphaModel`
- `CompositeAlphaModel`

这些名字本身已经说明 Lean 把它理解成：

- 任何能够产出“标准化市场观点”的组件

### 4.3 `IPortfolioConstructionModel`

接口：

- [IPortfolioConstructionModel.cs](/file/share/data_jyz/trade/Lean/Algorithm/Portfolio/IPortfolioConstructionModel.cs:17)

它在 Lean 里的真实职责是：

- 把 `Insight[]` 变成 `IPortfolioTarget[]`

这意味着，在 Lean 看来：

- 观点不等于仓位

这个拆分非常关键。

因为你完全可以：

- 用同一个 alpha
- 配不同的 portfolio construction

也就是：

- 看法不变
- 配仓方式变

基类 [PortfolioConstructionModel.cs](/file/share/data_jyz/trade/Lean/Algorithm/Portfolio/PortfolioConstructionModel.cs:30) 还实现了很多 Lean 自己定义的组合构造语义，比如：

- 是否因为新 insight 再平衡
- 是否因为 security changes 再平衡
- insight 过期如何处理

所以这层不只是“优化器”，而是：

- Lean 对目标仓位生成过程的统一抽象

### 4.4 `IRiskManagementModel`

接口：

- [IRiskManagementModel.cs](/file/share/data_jyz/trade/Lean/Algorithm/Risk/IRiskManagementModel.cs:17)

它在 Lean 里的真实职责是：

- 输入当前目标仓位
- 返回要覆盖或修正的目标仓位

注意这里不是返回布尔值，不是返回 warning。

而是直接返回**新的 portfolio target**。

所以从实现语义上讲：

- Lean 的风控不是旁路提醒
- 是一个有权改写目标仓位的层

这是非常强的建模选择。

### 4.5 `IExecutionModel`

接口：

- [IExecutionModel.cs](/file/share/data_jyz/trade/Lean/Algorithm/Execution/IExecutionModel.cs:17)

它在 Lean 里的真实职责是：

- 消费 `IPortfolioTarget[]`
- 自己决定要不要立即下单、分批下单、延迟下单
- 还负责处理 `OrderEvent`

所以在 Lean 里，Execution 不是一个纯函数，而是一个有状态执行器。

它不是简单“把 target 变成 market order”，而是：

- 一个订单推进器

---

## 5. Lean 更底层的核心领域其实是 `Security`

如果说 `QCAlgorithm` 是算法根对象，那么 `Security` 就是交易对象根抽象。

见：

- [Security.cs](/file/share/data_jyz/trade/Lean/Common/Securities/Security.cs:47)

源码注释里说得很直接：

- `Security object is intended to hold properties of the specific security asset`

也就是说，Lean 不是把交易标的理解成一条价格曲线，而是理解成：

- 一个携带大量交易属性和制度属性的对象

从字段和属性看，一个 `Security` 至少包含这些层次：

### 5.1 标识与静态属性

- `Symbol`
- `QuoteCurrency`
- `SymbolProperties`
- `SecurityType`
- `Subscriptions`

这些回答的是：

- 它是谁
- 用什么报价货币
- 合约乘数、最小跳动等静态属性是什么
- 它目前挂了哪些订阅

### 5.2 行情与时间状态

- `Cache`
- `Exchange`
- `LocalTime`
- `HasData`

这些回答的是：

- 当前市场价格状态是什么
- 市场此刻是否开盘
- 标的本地时间是什么

### 5.3 持仓与组合连接

- `Holdings`
- `PortfolioModel`

这些回答的是：

- 当前持仓状态如何
- 这个标的怎样影响组合

### 5.4 交易制度模型

这里是 `Security` 最关键的部分：

- `FeeModel`
- `FillModel`
- `SlippageModel`
- `BuyingPowerModel`
- `MarginInterestRateModel`
- `SettlementModel`
- `VolatilityModel`
- `DataFilter`
- `PriceVariationModel`

见：

- [Security.cs](/file/share/data_jyz/trade/Lean/Common/Securities/Security.cs:220)

这说明 Lean 真正的建模方式是：

- 每个标的对象自己携带一整套交易规则模型

所以不是“策略统一决定所有交易规则”，而是：

- 策略给出交易意图
- 标的对象决定这笔交易在自己身上应如何被解释

---

## 6. `Security` 下面这些模型在 Lean 里分别是什么

这部分完全按实现里的属性名来解释。

### `FeeModel`

含义：

- 订单费用怎么计算

常见实现：

- `InteractiveBrokersFeeModel`
- `BinanceFeeModel`
- `CoinbaseFeeModel`
- `ConstantFeeModel`

自然语言理解：

- 同一笔买卖，不同券商、不同市场、不同品种，费用不一样

### `FillModel`

含义：

- 订单怎样被撮合、以什么价格成交

常见实现：

- `EquityFillModel`
- `FutureFillModel`
- `FutureOptionFillModel`
- `ImmediateFillModel`

自然语言理解：

- 它定义“成交行为”
- 而不是“下单意图”

### `SlippageModel`

含义：

- 实际成交价相对理想价格偏了多少

常见实现：

- `NullSlippageModel`
- `ConstantSlippageModel`
- `VolumeShareSlippageModel`
- `MarketImpactSlippageModel`

自然语言理解：

- 这是成交摩擦模型

### `BuyingPowerModel`

含义：

- 这笔单会占多少购买力 / 保证金，账户允不允许下

常见实现：

- `CashBuyingPowerModel`
- `SecurityMarginModel`
- `FutureMarginModel`
- `OptionMarginModel`

自然语言理解：

- 这是账户约束模型

### `SettlementModel`

含义：

- 钱和资产什么时候结算完成

常见实现：

- `ImmediateSettlementModel`
- `DelayedSettlementModel`
- `FutureSettlementModel`

自然语言理解：

- 这是资金可用性模型

### `MarginInterestRateModel`

含义：

- 融资、空头、杠杆头寸的资金成本模型

自然语言理解：

- 这是资本占用成本模型

### `VolatilityModel`

含义：

- 给标的提供波动率估计

常见实现：

- `IndicatorVolatilityModel`
- `RelativeStandardDeviationVolatilityModel`
- `StandardDeviationOfReturnsVolatilityModel`

自然语言理解：

- 这是风险尺度模型

### `DataFilter`

含义：

- 过滤异常行情和不可信数据

常见实现：

- `EquityDataFilter`
- `ForexDataFilter`
- `OptionDataFilter`

自然语言理解：

- 这是行情清洗模型

### `PriceVariationModel`

含义：

- 最小价格变动单位怎么定义

常见实现：

- `SecurityPriceVariationModel`
- `EquityPriceVariationModel`
- `AdjustedPriceVariationModel`

自然语言理解：

- 这是 tick size 规则模型

---

## 7. 资产类型在 Lean 里不是标签，而是具体子类

Lean 不是一个 `Security` 加一个 `Type` 就结束。

它确实为很多资产类型提供了专门子类：

- `Equity`
- `Forex`
- `Future`
- `Option`
- `Crypto`
- `Index`

这些子类不是只多几个字段，而是直接在构造函数里塞进不同默认模型。

### 7.1 `Equity`

见：

- [Equity.cs](/file/share/data_jyz/trade/Lean/Common/Securities/Equity/Equity.cs:28)

`Equity` 默认就带：

- `SecurityPortfolioModel()`
- `EquityFillModel()`
- `InteractiveBrokersFeeModel()`
- `NullSlippageModel.Instance`
- `ImmediateSettlementModel()`
- `VolatilityModel.Null`
- `SecurityMarginModel(2m)`
- `EquityDataFilter()`
- `AdjustedPriceVariationModel()`

这说明：

- Lean 对股票的默认理解，本来就是一整套制度组合

### 7.2 `Option`

见：

- [Option.cs](/file/share/data_jyz/trade/Lean/Common/Securities/Option/Option.cs:38)

`Option` 默认就带：

- `OptionPortfolioModel()`
- `ImmediateFillModel()`
- `InteractiveBrokersFeeModel()`
- `NullSlippageModel.Instance`
- `ImmediateSettlementModel()`
- `VolatilityModel.Null`
- `OptionMarginModel()`
- `OptionDataFilter()`
- `SecurityPriceVariationModel()`
- `PriceModel`
- `OptionAssignmentModel`

这个设计说明得很明确：

- Lean 认为期权不是股票的附属品
- 而是另一类交易制度完全不同的标的

特别是期权还额外拆出了：

- `IOptionPriceModel`
- `IOptionAssignmentModel`

这两个在别的资产里通常没有。

所以从实现视角看，Lean 对期权的建模是更“厚”的。

---

## 8. `BrokerageModel` 在 Lean 里的真实位置

很多人把 `BrokerageModel` 理解成券商适配器。

但从实现上看，它更像：

- 交易制度工厂

接口：

- [IBrokerageModel.cs](/file/share/data_jyz/trade/Lean/Common/Brokerages/IBrokerageModel.cs:21)

它不直接负责网络连接，而是统一决定：

- `CanSubmitOrder`
- `CanUpdateOrder`
- `CanExecuteOrder`
- `GetLeverage`
- `GetFillModel`
- `GetFeeModel`
- `GetSlippageModel`
- `GetSettlementModel`
- `GetMarginInterestRateModel`
- `GetBuyingPowerModel`
- `GetShortableProvider`

所以 `BrokerageModel` 的真实含义是：

- **在某个券商 / 账户制度下，这个标的的交易规则应该是什么**

默认实现：

- [DefaultBrokerageModel.cs](/file/share/data_jyz/trade/Lean/Common/Brokerages/DefaultBrokerageModel.cs:39)

从这个默认实现能直接看出 Lean 的制度建模方式：

- 默认市场映射按 `SecurityType` 决定
- 杠杆按 `SecurityType` 决定
- Fill 模型按 `SecurityType` 决定
- Fee 模型按 `SecurityType` 决定
- Settlement 模型按 `SecurityType` 和 `AccountType` 决定

也就是说，Lean 把大量“市场制度差异”集中放在了这一层。

---

## 9. `SecurityInitializer` 负责把制度灌进标的对象

接口：

- [ISecurityInitializer.cs](/file/share/data_jyz/trade/Lean/Common/Securities/ISecurityInitializer.cs:22)

默认实现：

- [BrokerageModelSecurityInitializer.cs](/file/share/data_jyz/trade/Lean/Common/Securities/BrokerageModelSecurityInitializer.cs:26)

这个类的职责很直白：

- 从 `IBrokerageModel` 拿出各种模型
- 塞到 `Security` 实例上

它会设置：

- `security.FillModel`
- `security.FeeModel`
- `security.SlippageModel`
- `security.SettlementModel`
- `security.BuyingPowerModel`
- `security.MarginInterestRateModel`
- leverage
- shortable provider

所以它的实现语义是：

- `BrokerageModel` 提供规则模板
- `SecurityInitializer` 把模板应用到具体标的

这就是 Lean 里“制度层”和“标的层”的连接点。

---

## 10. 订单流在 Lean 里怎样落地

如果继续按实际实现看，订单系统也不是从 `ExecutionModel` 直接打到券商。

它中间还有一个清晰分层：

### 10.1 算法侧入口

- `SecurityTransactionManager`

见：

- [SecurityTransactionManager.cs](/file/share/data_jyz/trade/Lean/Common/Securities/SecurityTransactionManager.cs:30)

它做的是：

- 接收算法的下单请求
- 分配 order id
- 把请求交给 `_orderProcessor`

也就是说：

- 它是算法侧订单入口
- 不是最终成交执行器

### 10.2 引擎侧订单处理器

- `ITransactionHandler`
- `BrokerageTransactionHandler`

见：

- [BrokerageTransactionHandler.cs](/file/share/data_jyz/trade/Lean/Engine/TransactionHandlers/BrokerageTransactionHandler.cs:43)

它做的是：

- 持有订单队列
- 管 open orders / tickets / order events
- 订阅 brokerage 的各种回报事件
- 更新算法侧订单状态

所以从实现上讲：

- 算法对象不直接管所有订单生命周期
- 引擎有专门的订单流控制器

---

## 11. 数据流在 Lean 里怎样落地

同样，数据流也不是“数据直接进策略”。

主要分层是：

### 11.1 算法侧订阅登记

- `SubscriptionManager`

它表达：

- 算法想订阅什么

### 11.2 引擎侧订阅总控

- `DataManager`

见：

- [DataManager.cs](/file/share/data_jyz/trade/Lean/Engine/DataFeeds/DataManager.cs:34)

它是真正把 universe、subscription、datafeed 串起来的枢纽。

### 11.3 数据喂入器

- `IDataFeed`
- 默认本地回测实现是 `FileSystemDataFeed`

见：

- [FileSystemDataFeed.cs](/file/share/data_jyz/trade/Lean/Engine/DataFeeds/FileSystemDataFeed.cs:40)

### 11.4 策略看到的统一时间片

- `Slice`

所以 Lean 的数据建模不是：

- “某只股票吐几根 bar 给策略”

而是：

- “引擎维持一整套订阅系统，再把同一时间点的结果整理成统一的 `Slice`”

---

## 12. 把 Lean 的真实实现压成一句话

如果完全顺着源码写，而不是顺着外部分析框架写，Lean 对量化交易的实现可以概括成：

1. 先构造一个总算法上下文 `QCAlgorithm`
2. 再给它挂：
   - 数据管理对象
   - 标的管理对象
   - 组合管理对象
   - 订单入口对象
   - 调度对象
   - universe 管理对象
3. 再在它上面可选地挂 Framework 五段式模型：
   - UniverseSelection
   - Alpha
   - PortfolioConstruction
   - RiskManagement
   - Execution
4. 然后用 `Security` 作为交易对象的统一抽象
5. 再给每个 `Security` 注入：
   - 成交规则
   - 手续费规则
   - 滑点规则
   - 保证金规则
   - 结算规则
   - 波动率规则
   - 数据过滤规则
6. 最后由订单系统和数据系统把策略逻辑真正落地

所以 Lean 最本质的实现思想不是：

- “把量化算法拆成 Data/Securities/Orders 三块”

虽然它确实有这几大领域。

更准确的说法是：

- Lean 先实现了一个**统一算法运行时对象**
- 再围绕它组织出：
  - 可选的策略逻辑框架
  - 可插拔的标的交易规则
  - 可替换的券商制度模型

这才是它源码里真正的建模顺序和优先级。
