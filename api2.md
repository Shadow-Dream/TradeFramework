# Lean RPC / 热插拔改造蓝图

本文档是按 [guideline.md](/file/share/data_jyz/trade/guideline.md) 的执行顺序重写的一份自洽版本，目标只有一个：

> 把整条策略开发、回测、结果统计流程里所有需要写代码的部分，设计成可热插拔模块；模块接入方式可以是 RPC，也可以是上传脚本后编译成 DLL，由 runtime 统一管理。

约束条件：

1. 固定有一个常驻的 Lean runtime
2. 业务逻辑模块尽量不直接编进 runtime
3. 模块接入方式支持 RPC 与 DLL 两种并列形态
4. runtime 负责统一管理这两种接入方式的生命周期与热插拔
5. 最终需要支持热插拔，但不同模块允许的热插边界不同

本文档不讨论实现代码，只定义：

- 每一步对应的模块
- 现有 Lean 哪个接口/模块是挂载点
- 是否适合热插
- 是否必须补控制面/协调器
- 现有 composite 能不能直接复用

---

## Step 1：冻结系统分层

这一阶段先把整套系统分层，不写代码。

本次系统分成 8 层：

1. 数据层
2. 选股层
3. Alpha 层
4. 仓位构造层
5. 风险层
6. 执行层
7. 券商制度层
8. 结果统计层

对应到 Lean 当前模块，大致是：

- 数据层：
  - `IDataProvider`
  - `IMapFileProvider`
  - `IFactorFileProvider`
  - `IHistoryProvider`
  - `IDataFeed`
  - `IDataDownloader`
- 选股层：
  - `IUniverseSelectionModel`
- Alpha 层：
  - `IAlphaModel`
- 仓位构造层：
  - `IPortfolioConstructionModel`
- 风险层：
  - `IRiskManagementModel`
- 执行层：
  - `IExecutionModel`
- 券商制度层：
  - `IBrokerageModel`
- 结果统计层：
  - `BacktestingResultHandler / LiveTradingResultHandler`
  - `BaseResultsHandler`
  - `StatisticsBuilder`
  - `AlgorithmPerformance`
  - `PortfolioStatistics`
  - `TradeStatistics`
  - `Statistics`
  - `Report`

本次当前示例标的是 `NDX`，但这只是示例，不是未来固定前提。  
因此选股层必须可上传、可替换、可热插。

---

## Step 2：确认哪些层适合直接 RPC，哪些层必须先有控制面

### 适合直接 RPC 的层

这些层输入输出边界清楚，可以直接做远程代理：

1. 数据层中的：
   - `IDataProvider`
   - `IDataDownloader`
   - `IHistoryProvider`
2. 选股层：
   - `IUniverseSelectionModel`
3. Alpha 层：
   - `IAlphaModel`
4. 风险层：
   - `IRiskManagementModel`

这些层的特点是：

- 输出清晰
- 副作用少
- 不直接持有订单真实状态

### 必须先有控制面/协调器的层

这些层不能直接把远程模块硬接进来，否则运行时状态会乱。

这里要求的不是“必须本地”，而是必须先有：

- 状态所有权
- 生命周期管理
- 暂停 / 续断
- snapshot / 持久化
- 事件路由
- 失败降级

这个控制面可以在 Lean 进程内，也可以在外部服务里。

涉及的层包括：

1. 仓位构造层：
   - `IPortfolioConstructionModel`
2. 执行层：
   - `IExecutionModel`
3. 券商制度层：
   - `IBrokerageModel`
4. 结果统计层：
   - `ResultHandler / StatisticsBuilder / Report`

这些层要么：

- 负责全局合并
- 要么处理订单事件回流
- 要么会渗透进 `Security` 初始化
- 要么要统一输出统计结果

所以它们必须先由一个固定控制面来托管，外部模块只负责业务逻辑。

---

## Step 3：先定义固定 runtime 的角色

固定 runtime 不再等同于“具体策略算法类”，而是常驻系统。

它负责 6 件事：

1. 初始化 Lean
2. 维护模块注册表
3. 维护模块生命周期
4. 做本地对象到远程协议的转换
5. 处理热插拔切换
6. 处理失败降级

固定 runtime 或外部控制平面必须有以下角色：

1. 数据控制面
2. UniverseSelection 控制面
3. Alpha 控制面
4. Portfolio 控制面
5. Risk 控制面
6. Execution 协调器
7. Brokerage 控制面
8. 结果统计控制面

从这一步开始，业务逻辑模块不应再直接挂进主算法类。

### Pipeline 组织方式

这一步还必须额外规定：

**整个 pipeline 的组装本身也必须可编辑，不应写死在代码里。**

也就是说，不应该要求用户在主算法或 runtime 里写：

- `new AlphaHost(...)`
- `new UniverseSelectionHost(...)`
- `new RpcRiskHost(...)`
- `new ExecutionCoordinator(...)`

来完成模块拼装。

正确做法应是：

- runtime 固定
- pipeline 通过配置文件、manifest、数据库记录或远程控制面定义
- 运行时按配置装配模块图

### Pipeline 配置至少要描述的内容

1. 当前启用哪些数据模块
2. 当前启用哪些选股模块
3. 当前启用哪些 alpha 模块
4. 当前启用哪些 portfolio 模块
5. 当前启用哪些 risk 模块
6. 当前启用哪些 execution 模块
7. 当前启用哪个 brokerage 模块
8. 当前启用哪些结果统计 / 报告插件
9. 模块顺序
10. composite / merge 规则
11. fallback 规则
12. 热插边界
13. pause / resume 规则
14. snapshot / restore 后端

### Pipeline 配置应覆盖的三条链

#### 1. 策略链

- `UniverseSelection -> Alpha -> PortfolioConstruction -> RiskManagement -> Execution`

#### 2. 数据链

- `IDataProvider / IDataDownloader / IDataFeed / IHistoryProvider / map / factor`

#### 3. 结果统计链

- `ResultHandler -> BaseResultsHandler -> StatisticsBuilder -> PortfolioStatistics / TradeStatistics / Report`

### Pipeline 配置的运行语义

固定 runtime 启动后应执行：

1. 读取 pipeline 配置
2. 解析每个模块的类型与版本
3. 装配控制面和插件
4. 建立依赖关系
5. 校验热插规则
6. 进入运行态

之后如果用户上传新的模块，或者编辑配置：

1. runtime 先校验该修改是否合法
2. 如果需要，触发 pause
3. 做 snapshot / 持久化
4. 卸载旧模块
5. 装载新模块
6. 恢复状态
7. resume

### 对 Lean 的影响

Lean 当前的 `Launcher/config.json` 只覆盖：

- 算法入口
- 顶层 handler
- 数据/结果处理器类型

它不够表达整条策略 pipeline 的热插拔配置。

所以这个方案需要新增一套**独立于当前 Launcher 配置的 pipeline manifest**。

这个 manifest 应由固定 runtime 读取，而不是让策略代码自己写死模块实例化过程。

---

## Step 4：数据上传 / 下载 / 自动获取层

这是必须单独抽出来的一层，不能混进 alpha 或策略逻辑。

### 当前 Lean 的真实挂载点

数据层现有接口和实现包括：

- `IDataProvider`
- `IMapFileProvider`
- `IFactorFileProvider`
- `IHistoryProvider`
- `IDataFeed`
- `IDataDownloader`

以及典型实现：

- `DefaultDataProvider`
- `DownloaderDataProvider`
- `ApiDataProvider`
- `CompositeDataProvider`
- `HistoryProviderManager`

### 这层应该支持的热插拔能力

1. 新增额外 `data provider`
2. 新增额外下载器
3. 新增自动抓取模块
4. 替换 map/factor 文件来源
5. 替换历史数据来源
6. 组合多个 provider 做 fallback / 优先级切换

### 这层最适合的设计

这层应使用“控制面 + provider 插件”模式：

- 固定数据控制面负责：
  - 统一请求入口
  - provider 注册
  - provider fallback
  - provider 失败降级
  - snapshot / 恢复
- 外部插件负责：
  - 实际取数
  - 上传
  - 下载
  - 自动抓取
  - 数据变换

### 这层现有 composite 是否够用

部分够用：

- `CompositeDataProvider` 可以直接复用，适合作为多 provider fallback 的第一版骨架
- `HistoryProviderManager` 本身也是一个多 provider 管理器

### 这层还缺什么

缺少的是：

- 统一的远程 provider 控制面
- provider 动态注册/卸载机制
- provider 健康检查机制
- 运行中的 provider 切换策略

### 热插边界

这层通常可以运行中热切。

因为：

- 数据请求是请求式的
- 不直接碰订单状态
- 不直接改变持仓

这是最适合最早实现热插拔的一层。

---

## Step 5：券商制度层：`BitgetBrokerageModel`

这是整套系统里最难安全热插拔的一层。

### 当前 Lean 的真实挂载点

券商制度层的根接口是：

- `IBrokerageModel`

它会通过：

- `BrokerageModelSecurityInitializer`

写进每个 `Security`：

- `FillModel`
- `FeeModel`
- `SlippageModel`
- `SettlementModel`
- `BuyingPowerModel`
- `MarginInterestRateModel`
- `Leverage`
- `ShortableProvider`

### 为什么这一层不能只做简单 RPC

因为它不是单次计算接口，而是：

- 会影响后续新增 security 的初始化
- 会影响已有 security 的交易制度
- 会影响已有挂单是否还合法
- 会影响现有持仓的保证金语义

### 这层应该怎么设计

必须分成两层：

1. 固定 `BrokerageHost` 或等价的外部控制面
   - 在 Lean 内部实现 `IBrokerageModel`，或由外部控制面代理到 Lean
   - 管理当前活跃券商插件
   - 执行重初始化策略
2. 外部 `Bitget` 插件
   - 通过 RPC 或 DLL 提供：
     - fee
     - leverage
     - buying power
     - can-submit
     - can-execute
     - fill/slippage assumptions

### 这层现有 composite 是否可用

不建议做 `CompositeBrokerageModel`。

原因：

- 券商制度不是天然可并列组合的
- 不同券商模型同时作用于同一 `Security` 会产生语义冲突

这里应该保持：

- 单活跃券商模型
- 由控制面负责切换

### 热插边界

这层只能：

- 空仓且无未成交订单时热切
或
- 冷切 session 重建

如果后续补齐：

- pause / resume
- snapshot / restore
- 订单与持仓状态重放

则可以把这层从“空仓切换”推进到“受控热切换”，但前提是控制面拥有完整状态所有权。

---

## Step 6：固定实验参数

这一步本身不是热插拔层，但它决定后面每一层的统一参数协议。

固定 runtime 或外部控制平面应把这些参数统一变成配置对象并传给所有相关角色：

- 起止时间
- 分辨率
- 初始资金
- 账户类型
- 是否做空
- 是否扩展时段
- benchmark

这一步的产物不是业务逻辑，而是模块统一入参规范。

---

## Step 7：准备参考序列输入

这一步对应的不是策略模块，而是结果对照输入层。

当前参考序列可以是：

- 人工仓位序列
- 其它外部基线
- 研究系统导出的目标仓位

### 这层应该怎么设计

固定 runtime 不直接绑死某一个 CSV 文件，而应只知道“参考序列输入源”。

输入源可以是：

- 本地 CSV
- RPC 服务
- 云对象
- 脚本编译成 DLL 的本地插件

### 热插边界

这一层通常可运行中替换。  
因为它只是结果对照输入，不直接影响策略主链交易状态。

---

## Step 8：主算法壳子

主算法壳子不再代表具体策略，而代表固定系统 runtime。

主算法壳子只做：

1. 初始化 Lean
2. 安装各类控制面/协调器
3. 管模块生命周期
4. 暴露切换入口

在这一步里，策略逻辑不应直接写在算法类里。

主算法壳子也不应直接在代码里写模块装配逻辑。  
它应该只做：

1. 读取 pipeline manifest
2. 让 runtime 根据配置实例化控制面和插件
3. 把解析后的 pipeline 挂到 Lean 主链上

---

## Step 9：选股层：`UniverseSelection`

选股层必须可上传、可替换、可热插，因为 `NDX` 不是未来固定前提。

### 当前 Lean 的真实挂载点

- `IUniverseSelectionModel`
- `CompositeUniverseSelectionModel`

### 这层应该怎么设计

- 固定 `UniverseSelectionHost` 或等价控制面
- 远程 / DLL 选股模块

### 这层负责什么

- 当前看哪些标的
- Universe 刷新节奏
- 股票池扩展 / 收缩

### 这层现有 composite 是否可用

可用。

`CompositeUniverseSelectionModel` 适合作为第一版多选股模块并存的框架。

### 热插边界

这层一般可以运行中热切。  
但切换后会直接影响订阅集合，所以要和数据层联动。

---

## Step 10：Alpha 层

### 当前 Lean 的真实挂载点

- `IAlphaModel`
- `CompositeAlphaModel`

### 这层应该怎么设计

- 固定 `AlphaHost` 或等价控制面
- 远程 / DLL alpha 模块

### 这层现有 composite 是否可用

可用。

`CompositeAlphaModel` 可以继续复用，里面挂多个 `AlphaHost` 即可。

### 热插边界

这层通常可以运行中热切。  
如果 alpha 内部有自己的 rolling state，则：

- 要么状态完全保留在主 runtime
- 要么插件必须支持状态导出/导入

---

## Step 11：仓位构造层：`PortfolioConstruction`

这是整套系统里最需要补新合并语义的一层。

### 当前 Lean 的真实挂载点

- `IPortfolioConstructionModel`

### 当前 Lean 的问题

当前 Lean 默认假设：

- 只有一个 `PortfolioConstruction`
- 它直接吃 insight
- 它直接吐最终 `IPortfolioTarget`

这对多模块热插不够。

### 这层应该怎么设计

必须加一层固定的合并控制面：

- 多 portfolio / processor 模块不要直接输出最终 target
- 先输出统一的“仓位意图”
- 再由本地合并层统一变成最终 target

### 这层现有 composite 是否可用

**不可直接复用。**

Lean 当前没有现成的 `CompositePortfolioConstructionModel`。  
这一层需要新增自己的合并语义。

### 这层必须明确的规则

1. 同 symbol 冲突如何合并
2. 叠加 / 覆盖 / 优先级 / 分桶的规则
3. 哪些模块有 veto 权限
4. 哪些模块只有增仓权限，哪些只有减仓权限

### 热插边界

这层可以运行中热切，但前提是：

- 合并语义固定在控制面
- 外部模块只负责吐意图

---

## Step 12：风险层：`RiskManagement`

### 当前 Lean 的真实挂载点

- `IRiskManagementModel`
- `CompositeRiskManagementModel`

### 这层应该怎么设计

- 固定 `RiskHost` 或等价控制面
- 远程 / DLL 风险模块

### 这层现有 composite 是否可用

可用。

当前 Lean 的 `CompositeRiskManagementModel` 语义是：

- 多个风险模块顺序执行
- 后一个模块的同 symbol target 可以覆盖前一个

第一版可以直接沿用。

### 热插边界

这层一般可运行中热切。

---

## Step 13：执行层：`Execution`

这是第二个必须加协调器的层。

### 当前 Lean 的真实挂载点

- `IExecutionModel`
- 订单事件回流到单一 execution model

### 当前 Lean 的问题

Execution 不是纯函数。它持有：

- target
- 执行状态
- 未完成执行计划
- 订单事件回流

### 这层应该怎么设计

必须改成：

- 固定 `ExecutionCoordinator` 或等价执行控制面
- 可替换的远程 / DLL 执行子模块

### 这层现有 composite 是否可用

**不建议直接做并列 `CompositeExecutionModel`。**

这里更适合：

- 单一协调器
- 多执行子模块
- 统一 order ownership
- 统一 order event 分发

### 热插边界

如果执行模块无内部状态，可以运行中热切。  
如果执行模块有内部状态，则必须支持：

- 状态迁移
或
- 空仓时切换

如果补齐：

- pause / resume
- snapshot / restore
- 未完成执行计划持久化
- 订单事件回放

那么执行层也可以在持仓中做受控热切换。

---

## Step 14：组装主算法

主算法里挂的应该是：

- Data control plane
- UniverseSelection control plane
- Alpha control plane
- Portfolio merge control plane
- Risk control plane
- Execution coordinator
- Brokerage control plane
- 结果统计扩展控制面

不要挂具体业务类。

---

## Step 15：运行状态输出

所有宿主都要输出：

- 模块版本
- 模块类型（RPC / DLL / 本地）
- 切换时间
- 调用失败
- 超时

如果模块支持状态迁移，还要输出：

- 导出状态摘要
- 导入状态摘要

---

## Step 16：短窗口冒烟回测

这一阶段要验证的是 runtime，不只是策略。

最少验证：

1. 数据模块能加载
2. 选股模块能加载
3. alpha 模块能加载
4. 风险模块能加载
5. portfolio 模块能加载
6. execution 模块能加载
7. brokerage 模块能加载
8. 运行中切换后不崩

建议热切测试顺序：

1. `DataProvider`
2. `UniverseSelection`
3. `Alpha`
4. `Risk`
5. `PortfolioConstruction`
6. `Execution`
7. `BrokerageModel`

---

## Step 17：修正结构性问题

重点修：

- 数据源切换后订阅是否失效
- 选股切换后 universe 是否正确刷新
- alpha 切换后状态是否串味
- portfolio 合并语义是否稳定
- risk 覆盖顺序是否稳定
- execution 是否丢单
- brokerage 切换是否破坏 security 初始化

---

## Step 18：正式策略回测

正式回测要记录完整模块版本：

- data provider 模块版本
- universe 模块版本
- alpha 模块版本
- portfolio 模块版本
- risk 模块版本
- execution 模块版本
- brokerage 模块版本

---

## Step 19：Lean 的真实结果统计链

Lean 里年化、Sharpe、回撤、交易次数不是由一个统一“Evaluation 模块”算的，而是由下面这条结果统计链负责：

1. `BacktestingResultHandler` / `LiveTradingResultHandler`
2. `BaseResultsHandler`
3. `StatisticsBuilder`
4. `AlgorithmPerformance`
5. `PortfolioStatistics`
6. `TradeStatistics`
7. `Statistics`
8. `Report`

### 关键代码位置

- `BaseResultsHandler.GenerateStatisticsResults(...)`
- `StatisticsBuilder.Generate(...)`
- `StatisticsBuilder.GetSummary(...)`
- `PortfolioStatistics`
- `Statistics.CalculateDrawdownMetrics(...)`
- `Report/Report.cs`
- `Report/ReportElements/*`

### 指标对应位置

- 年化收益率：`PortfolioStatistics.CompoundingAnnualReturn`
- 夏普率：`PortfolioStatistics.SharpeRatio`
- 最大回撤：`PortfolioStatistics.Drawdown`
- 交易次数：`StatisticsBuilder.GetSummary(...)` 里的 `Total Orders`

---

## Step 20：结果统计 / 报告扩展位

如果你要让结果统计也支持 RPC / 热插拔，不要去改策略主链，而是挂在结果链。

当前 Lean 里可扩的位置包括：

- `BaseResultsHandler.SummaryStatistic(...)`
- `IAlgorithm.RuntimeStatistics`
- `StatisticsResults.AddCustomSummaryStatistics(...)`
- `ReportElements/*`

### 适合做成热插的内容

1. 自定义 summary 指标
2. 自定义 runtime statistics
3. 自定义 report element
4. 自定义结果汇总规则

### 这层现有 composite 是否可用

这里不是典型 composite，而是：

- 统计链固定
- 指标与报告元素可扩展

更像“插件链”，不是“并列合并”。

---

## Step 21：跑结果统计

跑完策略后，结果链会收集：

- equity
- benchmark
- trades
- orders
- runtime statistics

然后生成：

- summary
- rolling statistics
- report data

如果你扩展了统计插件或报告插件，这一步就是它们的执行时机。

---

## Step 22：做仓位对照

这里对照的是：

- 策略仓位路径
- 参考序列输入

输出：

- 每日误差
- 平均绝对误差
- 方向一致率

这不是策略层，而是结果链的附加分析输入。

---

## Step 23：做绩效对比

这里对比的是：

- 年化收益率
- 夏普率
- 最大回撤
- 交易次数

这些都来自 Lean 的结果统计链，不是单独一个业务策略模块。

---

## Step 24：差异归因

差异归因分成三层：

1. 数据层
   - provider
   - downloader
   - history source
2. 策略层
   - universe
   - alpha
   - portfolio
   - risk
   - execution
   - brokerage
3. 结果链层
   - 统计口径
   - report element
   - summary 指标
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

- 数据层先改 `DataProvider / Downloader / History`
- 策略层先改 `UniverseSelection`、`Alpha`、`Risk`
- 合成层必须改 `PortfolioConstruction`
- 状态层必须改 `Execution`
- 制度层必须改 `BrokerageModel`
- 年化 / Sharpe / 回撤 / 交易次数由 `BacktestingResultHandler -> BaseResultsHandler -> StatisticsBuilder -> PortfolioStatistics / Statistics / TradeStatistics -> Report` 这条结果统计链负责
