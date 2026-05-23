# Lean 热插拔重构计划 v2

## 1. 目标

把当前 `Lean` 的“进程内对象直连 + 代码里写死装配”重构成：

1. 固定 runtime 常驻
2. pipeline 由配置装配，不在代码里 `new()`
3. 模块接入方式并列支持：
   - remote service
   - in-process plugin
   - script runner
   - out-of-process local worker
4. 预置实现和外部实现走同一条装配链
5. 支持 pause / resume / snapshot / restore
6. 支持热插拔，但不同模块热插边界不同

---

## 2. 总体原则

### 2.1 模块按业务语义分，不按接入方式分

模块按下面 8 类抽象：

1. `DataModule`
2. `UniverseModule`
3. `SignalModule`
4. `TargetModule`
5. `ConstraintModule`
6. `ExecutionModule`
7. `MarketRuleModule`
8. `AnalyzerModule`

`RPC`、`DLL`、`Python/MATLAB 脚本` 只是运行形态，不是业务抽象。

### 2.2 用户不直接面对 Composite 概念

对用户，只暴露“模块”。

组合、多路合并、依赖图、fallback、优先级，都是 runtime 内部装配语义。

也就是说：

- 用户不需要关心 `CompositeAlphaModel`
- 用户不需要关心 `CompositeRiskManagementModel`
- 用户不需要关心 `CompositeUniverseSelectionModel`

用户只需要声明：

- 我上传了哪些模块
- 它们的依赖是什么
- pipeline 怎么组装

### 2.3 预置实现与外部实现一视同仁

这是强约束。

错误做法：

- 预置类继续直连主链
- 外部模块走一条特殊 `RpcXXX` / `DllXXX` 路

正确做法：

- 预置类也注册成模块实现
- 由同一套 manifest / factory / lifecycle 管理
- 外部模块和预置模块唯一差别只是“代码来源”

### 2.4 输出层只有一个统一抽象

用户只看到：

- `AnalyzerModule`

它的输入是观测数据并集，输出可以是：

- 标量指标
- 时间序列
- 表格
- 图表规格
- 报告片段

“结果收集器”不是用户层概念，而是 runtime 内部机制。

---

## 3. Runtime 结构

runtime 固定提供 4 类内部能力：

1. `Control Plane`
2. `Module Factory`
3. `Observation Bus / Store`
4. `Pipeline Executor`

### 3.1 Control Plane

负责：

1. 模块注册
2. 模块装载/卸载
3. pause / resume
4. snapshot / restore
5. 健康检查
6. 失败降级
7. 热插边界校验

### 3.2 Module Factory

负责：

1. 读取模块描述
2. 识别运行形态
3. 创建模块实例
4. 注入初始化参数
5. 返回统一模块对象

### 3.3 Observation Bus / Store

负责：

1. 收集所有 producer 输出
2. 用稳定 key 存储
3. 按 analyzer 依赖自动分发
4. 支持 replay / debug / trace

### 3.4 Pipeline Executor

负责：

1. 读取 pipeline manifest
2. 组装模块图
3. 调度执行顺序
4. 维护跨模块状态边界

---

## 4. 稳定 Key 机制

所有模块输出都必须 key 化。

示例：

- `data.market.ndx.close`
- `data.history.ndx.daily`
- `universe.current.us_tech`
- `signal.trend.ndx.direction`
- `target.ndx.weight`
- `constraint.max_drawdown.state`
- `execution.order.ndx.fill`
- `market_rule.bitget.fee.total`
- `equity.curve`
- `benchmark.curve`
- `reference.manual_position.ndx`

稳定 key 的作用：

1. 解耦 producer 和 analyzer
2. 支持统一观测收集
3. 支持 snapshot / replay
4. 支持调试和版本对齐

---

## 5. Pipeline 组织方式

pipeline 必须配置化。

不允许在主算法或 runtime 里写：

- `new UniverseSelectionHost(...)`
- `new AlphaHost(...)`
- `new RiskHost(...)`
- `new ExecutionCoordinator(...)`

### 5.1 Manifest 必须表达的内容

1. 数据模块列表
2. 选股模块列表
3. 信号模块列表
4. 目标模块列表
5. 约束模块列表
6. 执行模块列表
7. 券商制度模块
8. 分析模块列表
9. 模块顺序
10. 依赖关系
11. merge / fallback / override 规则
12. 热插边界
13. pause / resume 策略
14. snapshot / restore 后端

### 5.2 Manifest 覆盖的三条链

1. 数据链
2. 策略链
3. 统计输出链

---

## 6. 各层重构方案

### 6.1 数据层

#### 目标抽象

- `DataModule`

#### 当前 Lean 映射

- `IDataProvider`
- `IMapFileProvider`
- `IFactorFileProvider`
- `IHistoryProvider`
- `IDataFeed`
- `IDataDownloader`

预置实现：

- `DefaultDataProvider`
- `DownloaderDataProvider`
- `ApiDataProvider`
- `CompositeDataProvider`
- `HistoryProviderManager`

#### 重构要求

1. 所有预置类都注册为内置 `DataModule`
2. 外部 `data provider`、下载器、历史源也作为同类模块注册
3. `CompositeDataProvider` 和 `HistoryProviderManager` 作为内置组合模块保留
4. 用户可根据接口自己新增 data provider

#### 热插边界

数据层允许运行中热切。

#### 接口策略

保留 Lean 现有数据接口不动，只新增统一模块契约。

---

### 6.2 选股层

#### 目标抽象

- `UniverseModule`

#### 当前 Lean 映射

- `IUniverseSelectionModel`
- `CompositeUniverseSelectionModel`

#### 重构要求

1. 预置 universe 也注册成内置模块
2. 外部 universe 通过任意运行形态接入
3. `CompositeUniverseSelectionModel` 作为内置组合模块保留
4. 不把 `NDX` 写死在系统设计里

#### 热插边界

允许运行中热切，但必须联动订阅刷新。

#### 接口策略

保留 `IUniverseSelectionModel`，外面包统一模块契约。

---

### 6.3 Alpha 层

#### 目标抽象

- `SignalModule`

#### 当前 Lean 映射

- `IAlphaModel`
- `CompositeAlphaModel`

#### 重构要求

1. 原始数据视为特殊 source signal
2. 普通 signal 可依赖原始数据，也可依赖其它 signal
3. 预置 alpha 注册为内置模块
4. 外部 alpha 走同一套模块装配
5. `CompositeAlphaModel` 只作为 runtime 内部组合实现，不作为用户概念

#### 热插边界

允许运行中热切。  
若模块有内部状态，必须支持：

- pause
- snapshot
- restore

---

### 6.4 仓位构造层

#### 目标抽象

- `TargetModule`

#### 当前 Lean 映射

- `IPortfolioConstructionModel`

#### 重构要求

1. 不允许多个模块直接各自产出最终 `IPortfolioTarget`
2. 统一先产出 `target intent`
3. 由 runtime 固定的 merge 语义合成最终 target
4. 预置 portfolio 也必须改造成同样的 intent-producing 模块

#### 为什么最关键

这一层当前没有可复用的现成 composite 语义。

#### 必须明确的规则

1. 同 symbol 冲突合并
2. 叠加 / 覆盖 / 优先级 / 分桶
3. veto 权限
4. 增仓/减仓权限

#### 热插边界

允许热切，但 merge 规则必须固定在 runtime。

---

### 6.5 风险层

#### 目标抽象

- `ConstraintModule`

#### 当前 Lean 映射

- `IRiskManagementModel`
- `CompositeRiskManagementModel`

#### 重构要求

1. 预置 risk 改造成内置约束模块
2. 外部 risk 走同一套模块装配
3. `CompositeRiskManagementModel` 继续保留为内置组合实现

#### 热插边界

允许运行中热切。

---

### 6.6 执行层

#### 目标抽象

- `ExecutionModule`

#### 当前 Lean 映射

- `IExecutionModel`
- 订单事件回流到单一 execution model

#### 重构要求

1. 预置 execution 类注册为内置执行模块
2. 外部执行模块走同一套模块装配
3. runtime 必须有统一执行控制面
4. 统一管理：
   - order ownership
   - order event routing
   - 未完成执行计划

#### 热插边界

不是不能热插，而是必须先补：

- pause / resume
- snapshot / restore
- 未完成执行计划持久化
- 订单事件回放

没有这些能力前，只允许空仓切。

---

### 6.7 券商制度层

#### 目标抽象

- `MarketRuleModule`

#### 当前 Lean 映射

- `IBrokerageModel`
- `BrokerageModelSecurityInitializer`

#### 重构要求

1. 所有预置券商模型注册为内置市场规则模块
2. `Bitget` 也是同类模块，不走特殊通道
3. runtime 保持单活跃券商模型
4. 券商制度通过统一控制面切换

#### 为什么不能做并列 composite

券商制度不是天然可并列组合的，多个模型同时作用于同一 `Security` 会语义冲突。

#### 热插边界

可热切，但需要：

- pause / resume
- snapshot / restore
- 持仓/挂单状态重建
- security 重初始化策略

否则只能空仓切或冷切。

---

### 6.8 结果统计层

#### 目标抽象

- `AnalyzerModule`

#### 当前 Lean 映射

结果链真实模块：

- `BacktestingResultHandler / LiveTradingResultHandler`
- `BaseResultsHandler`
- `StatisticsBuilder`
- `AlgorithmPerformance`
- `PortfolioStatistics`
- `TradeStatistics`
- `Statistics`
- `Report`

#### 重构要求

1. 用户只面对 `AnalyzerModule`
2. runtime 内部自动收集观测数据
3. analyzer 通过稳定 key 声明输入依赖
4. 预置 summary 指标、图表、报告元素都注册成内置 analyzer/renderer 模块

#### 为什么不拆很多接口

这一层本质上都是：

- 对观测数据做统计、比较、汇总、展示

所以统一成一个输出/分析抽象更合理。

#### 热插边界

运行中可切，但必须保证：

- 已采样观测不丢
- 新 analyzer 能消费旧 snapshot

---

## 7. Observation 机制

这层不暴露给用户。

runtime 内部维护：

- Observation Bus
- Observation Store

负责：

1. 自动收集所有 producer 输出
2. 按稳定 key 存储
3. 按 analyzer 依赖自动分发
4. 支持 replay / trace / debug

用户不需要自己写“结果收集器”。

---

## 8. 预置实现改写策略

这是核心约束。

### 错误做法

1. 预置类继续直连主链
2. 外部模块走 `RpcXXX` / `DllXXX` 特殊类
3. 两套实现路径平行存在

### 正确做法

1. 保留现有 Lean 业务接口
2. 新增统一模块契约
3. 预置实现全部注册为“内置模块”
4. 外部实现注册为“外部模块”
5. 两者都通过同一模块工厂和控制面装配

### 结论

不能把允许外部接入的功能做成一个特殊子类然后平行于现有预置类。

必须把现有预置类整体改造成符合同一模块契约的实现。

---

## 9. 分阶段实施

### Phase 1

- 统一模块契约
- 统一模块工厂
- pipeline manifest
- 控制面接口
- observation key 规范

### Phase 2

- 迁移数据层
- 迁移 universe 层
- 迁移 alpha 层
- 迁移 risk 层

### Phase 3

- 新增 target intent 与 merge 层
- 迁移所有 portfolio 构造实现

### Phase 4

- 新增 execution 控制面
- 迁移所有 execution 实现

### Phase 5

- 新增 brokerage 控制面
- 迁移所有 brokerage 实现

### Phase 6

- 统一 analyzer 模块
- 迁移统计与报告实现

---

## 10. 最终结论

这次 v2 重构的关键不是：

- 再加几种 `RpcXXX` 子类

而是：

1. 保留 Lean 当前业务接口语义
2. 新增统一模块契约
3. 新增统一控制面
4. 新增 observation 机制
5. 让 pipeline 配置化
6. 让预置实现与外部实现走同一条装配链
7. 让 `RPC`、`DLL`、脚本执行器、子进程 worker 都成为并列运行形态
