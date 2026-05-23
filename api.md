# Lean 热插拔 / RPC 化改造顺序清单

本文档按 [guideline.md](/file/share/data_jyz/trade/guideline.md) 的顺序排列，只回答两件事：

1. 哪些模块可以 RPC / 云化 / 热插拔
2. Lean 里负责计算年化、Sharpe、回撤、交易次数的真实模块链是什么

---

## Step 1：冻结任务定义

- 选股模型：`UniverseSelection`
- 券商模型：`Bitget`
- 策略链：`UniverseSelection -> Alpha -> PortfolioConstruction -> RiskManagement -> Execution`
- 结果统计链：`ResultHandler -> BaseResultsHandler -> StatisticsBuilder -> AlgorithmPerformance / PortfolioStatistics / TradeStatistics -> Report`
- 参考序列输入：人工仓位序列或其它对照序列

---

## Step 2：确认源码缺口

当前源码里：

- 没有 `BitgetBrokerageModel`
- `C#` 策略类默认是编译后装载
- `Python` 模块是启动时导入，不是运行中热切
- `IDataProvider / IMapFileProvider / IFactorFileProvider / IHistoryProvider / IDataFeed`
- `UniverseSelection`、`PortfolioConstruction`、`Execution`、`BrokerageModel` 是主链耦合点

---

## Step 3：搭 runtime / 模块宿主层

固定 runtime 负责：

- 调远程模块
- 做参数序列化
- 做状态装载/卸载
- 做模块健康检查

可热插拔模块负责：

- data provider / downloader / auto-fetch
- universe selection
- alpha
- portfolio
- risk
- execution
- brokerage

---

## Step 4：数据上传 / 下载 / 自动获取模块

这一层要把 Lean 的数据接口也做成可替换模块。

### 需要挂远程或 DLL 的接口

- `IDataProvider`
- `IMapFileProvider`
- `IFactorFileProvider`
- `IHistoryProvider`
- `IDataFeed`
- `IDataDownloader`

### 需要可组合 / 可替换的实现

- `DefaultDataProvider`
- `DownloaderDataProvider`
- `ApiDataProvider`
- `CompositeDataProvider`
- `HistoryProviderManager`

### 这一层允许做的事

- 自定义额外 `data provider`
- 自定义下载器
- 自定义自动抓取逻辑
- 自定义历史数据回放来源
- 自定义 map / factor file 来源

### 热插拔边界

- 数据源模块可以运行中替换
- 新增数据 provider 只需要实现接口并注册到宿主
- 如果多个 provider 同时存在，优先走 composite / fallback 语义

### 对 Lean 的影响

这层不应该绑死在单一 `DefaultDataProvider` 上。
Lean 里现有接口已经足够把：

- 本地文件
- 云对象
- RPC 数据服务
- 自定义下载服务

统一挂进来。

---

## Step 5：实现 `BitgetBrokerageModel`

这一层需要两部分：

### 本地宿主

- `IBrokerageModel` 本地实现
- 接 Lean 的 `Security` / `Order`
- 发 RPC 或加载 DLL

### 远程 / DLL 模块

负责：

- 费率
- 杠杆
- 买力
- 可提交订单规则
- 可执行订单规则
- 成交 / 滑点假设

### 热插拔边界

- `flat + no open orders` 时切换，或
- 冷切 session 重建

---

## Step 6：固定回测参数

统一配置：

- 起止时间
- 分辨率
- 初始资金
- 账户类型
- 是否做空
- 是否扩展时段

这些参数由固定 runtime 传给所有模块。

---

## Step 7：准备参考序列输入

参考序列不是策略模块，只是输入。

来源可以是：

- 本地 CSV
- 远程 RPC
- 云对象
- DLL 生成的数据源

后面会被结果统计链读取，用来和策略输出做对照。

---

## Step 8：写主算法壳子

主算法壳子只做：

- 初始化 Lean
- 装 host
- 管模块生命周期

不要把业务逻辑直接写进主算法类里。

---

## Step 9：写 `UniverseSelectionModel`

推荐做法：

- Lean 内部放 `UniverseSelectionHost`
- `UniverseSelectionHost` 通过 RPC / DLL 调远程选股模块

当前 `IUniverseSelectionModel` 已经够当边界。

`CompositeUniverseSelectionModel` 可以继续用，里面挂的改成多个 `UniverseSelectionHost` 即可。

这一层负责决定：

- 当前到底看哪些标的
- `NDX` 还是别的指数 / ETF / 股票池
- Universe 刷新节奏

`NDX` 只是当前示例标的池，不是未来固定前提。

---

## Step 10：写 `AlphaModel`

推荐做法：

- Lean 内部放 `AlphaHost`
- `AlphaHost` 通过 RPC / DLL 调远程 alpha

当前 `IAlphaModel` 已经够当边界。

`CompositeAlphaModel` 可以继续用，里面挂的改成多个 `AlphaHost` 即可。

---

## Step 11：写 `PortfolioConstructionModel`

这层是多模块合成仓位的核心。

要求：

- 不直接让多个模块各自吐最终 `IPortfolioTarget`
- 先吐统一的中间意图
- 再由固定 runtime 合并成最终目标

这里需要明确：

- 同 symbol 冲突怎么合
- 是覆盖、叠加、优先级、还是分桶
- 哪些模块有 veto 权限

这层是最需要改 composite 语义的地方。

---

## Step 12：写 `RiskManagementModel`

Lean 已经有 `CompositeRiskManagementModel`。

这一层建议：

- `RiskHost` 远程化
- 多 risk 模块顺序执行
- 先沿用现有“后者覆盖前者同 symbol target”的语义

---

## Step 13：写 `ExecutionModel`

Execution 不是纯函数，包含：

- target
- 执行状态
- 未完成执行计划
- 订单事件回流

所以不能只做简单 RPC 调用，要做：

- 固定 execution 协调器
- 可替换执行子模块
- 统一订单 ownership
- 统一订单事件分发

---

## Step 14：组装主算法

主算法里只挂：

- alpha host
- universe host
- portfolio host / merger
- risk host
- execution coordinator
- brokerage host

不要挂具体业务类。

---

## Step 15：加入运行状态输出

记录：

- 模块版本
- 模块类型（RPC / DLL / 本地）
- 切换时间
- 调用失败
- 超时

---

## Step 16：短窗口冒烟回测

先验证：

- 模块能加载
- 切换能工作
- runtime 不崩
- 订单和事件还能回流

---

## Step 17：修正结构性问题

重点看：

- alpha 切换是否保留旧状态
- portfolio 合并语义是否正确
- risk 覆盖顺序是否稳定
- execution 是否丢单
- brokerage 切换是否破坏 security 初始化

---

## Step 18：正式策略回测

正式回测要记录：

- alpha 模块版本
- portfolio 模块版本
- risk 模块版本
- execution 模块版本
- brokerage 模块版本

---

## Step 19：结果统计链

Lean 里真正负责年化、Sharpe、回撤、交易次数的模块链是：

1. `BacktestingResultHandler` / `LiveTradingResultHandler`
2. `BaseResultsHandler`
3. `StatisticsBuilder`
4. `AlgorithmPerformance`
5. `PortfolioStatistics`
6. `TradeStatistics`
7. `Statistics`
8. `Report`

### 关键位置

- `BaseResultsHandler.GenerateStatisticsResults(...)`
- `StatisticsBuilder.Generate(...)`
- `StatisticsBuilder.GetSummary(...)`
- `PortfolioStatistics`
- `Statistics.CalculateDrawdownMetrics(...)`
- `Report/Report.cs`
- `Report/ReportElements/*`

### 输出指标对应位置

- 年化收益率：`PortfolioStatistics.CompoundingAnnualReturn`
- 夏普率：`PortfolioStatistics.SharpeRatio`
- 最大回撤：`PortfolioStatistics.Drawdown`
- 交易次数：`StatisticsBuilder.GetSummary(...)` 里的 `Total Orders`

---

## Step 20：统计 / 报告插件位

可插拔的位置不是“人工仓位回放”，而是统计与报告层：

- `BaseResultsHandler.SummaryStatistic(...)`
- `IAlgorithm.RuntimeStatistics`
- `StatisticsResults.AddCustomSummaryStatistics(...)`
- `ReportElements/*`

这层适合 RPC / DLL 的是：

- 自定义 summary 指标
- 自定义 report element
- 自定义结果汇总规则

---

## Step 21：跑结果统计

跑完策略后，结果处理器会收集：

- equity
- benchmark
- trades
- orders
- runtime statistics

然后走统计链生成：

- summary
- rolling statistics
- report data

---

## Step 22：做仓位对照

这里对照的是：

- 策略仓位路径
- 参考序列输入

输出：

- 每日误差
- 平均绝对误差
- 方向一致率

这部分是结果统计链的输入源，不是策略主链。

---

## Step 23：做绩效对比

对比输出：

- 年化收益率
- 夏普率
- 最大回撤
- 交易次数

这些都来自 Lean 的结果统计链，不是单独一个“评价模块”。

---

## Step 24：差异归因

差异归因分两类：

1. 策略层
   - alpha
   - portfolio
   - risk
   - execution
2. 结果链层
   - 统计口径
   - 报告元素
   - 自定义 summary
   - 参考序列输入

---

# 优先级

## 第一优先级

- `DataProvider`
- `UniverseSelection`
- `Alpha`
- `RiskManagement`

## 第二优先级

- `PortfolioConstruction`

## 第三优先级

- `Execution`

## 最后

- `BrokerageModel`

---

# 结论

如果要把 guideline 里的代码改造都做成 RPC / 云模块 / 热插拔：

- 数据层优先改 `DataProvider`
- 策略层优先改 `UniverseSelection`、`Alpha`、`Risk`
- 合成层必须改 `PortfolioConstruction`
- 状态层必须改 `Execution`
- 券商层必须改 `BrokerageModel`
- 年化 / Sharpe / 回撤 / 交易次数由 `BacktestingResultHandler -> BaseResultsHandler -> StatisticsBuilder -> PortfolioStatistics / Statistics / TradeStatistics -> Report` 这条结果统计链负责
