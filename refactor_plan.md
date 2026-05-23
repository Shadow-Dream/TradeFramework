# Lean 模块化 / 热插拔重构计划

本文档给出一份面向 `Lean` 的正式重构计划，目标是把当前偏“进程内对象直连”的策略/数据/结果链，重构成：

- 固定 runtime 常驻
- pipeline 通过配置装配
- 模块可通过多种并列方式接入
- 支持 pause / resume / snapshot / restore
- 现有 Lean 预置实现与外部实现走同一套接口语义

本文档重点回答两件事：

1. 怎么留接口，在保持自由度的同时，符合设计语义
2. Lean 现有预置类怎么改写成符合该接口的实现，而不是额外做一套“特殊子类平行体系”

---

## 1. 重构目标

重构完成后，系统应具备以下能力：

1. 数据层、选股层、Alpha 层、仓位构造层、风险层、执行层、券商制度层、结果统计层都能以模块方式装配
2. 模块接入方式支持：
   - RPC
   - DLL
3. pipeline 通过 manifest/config 组织，而不是在算法类里写死 `new XXX(...)`
4. 运行中允许模块热切，但热切边界受控制面约束
5. 预置实现和外部实现一视同仁，都走统一接口和统一生命周期

---

## 2. 非目标

这次重构不追求：

1. 把 Lean 全部改成纯微服务
2. 消灭所有进程内调用
3. 第一版就实现所有模块的无损热切
4. 破坏当前已有 Framework API 的语义兼容性

---

## 3. 设计原则

### 3.1 一层只保留一层语义

每层接口只表达该层该表达的东西：

- 数据层只管数据获取与变换
- 选股层只管 universe
- Alpha 层只管观点
- 仓位构造层只管目标持仓生成
- 风险层只管约束和修正
- 执行层只管落单与订单状态
- 券商制度层只管交易制度
- 结果统计层只管统计和报告

不要跨层混义。

### 3.2 “模块接入方式”不能污染“业务语义”

模块接入方式只是实现部署形式，不是业务概念。

也就是说：

- 业务层接口不应该叫 `IRpcAlphaModel`
- 也不应该叫 `IDllRiskModel`
- 也不应该叫 `IPythonAlphaModel`
- 也不应该叫 `IMatlabPortfolioModel`

业务语义接口只表达业务。

真正区分接入方式的，应是模块装载器、控制面和适配层。

### 3.3 预置实现与外部实现必须走同一条路

这是本次重构最重要的约束。

错误做法：

- 保留现有 Lean 预置类直接进主链
- 另起一套 `RpcXXX` / `DllXXX` 特殊子类给外部模块接入
- 两条实现链平行存在

这样会导致：

- 预置类和外部类能力不对称
- 控制面只能管理外部模块，不能管理预置模块
- 热插拔、snapshot、pause/resume 只覆盖部分模块
- 未来维护成本翻倍

正确做法：

- 把现有预置类也改造成“模块实现”
- 它们和外部模块一样，统一注册、统一装配、统一生命周期

### 3.4 必须有控制面，但不要求必须“本地”

需要固定的是“控制面能力”，不是“控制面必须写在 Lean 进程里”。

控制面至少负责：

1. 模块注册
2. 模块装载/卸载
3. 状态所有权
4. pause / resume
5. snapshot / restore
6. 事件路由
7. 失败降级

这个控制面可以：

- 在 Lean 进程内
- 也可以是外部编排服务

---

## 4. 总体架构

重构后分成四层：

### 4.1 Engine Contracts

保留 Lean 原有主语义接口：

- `IDataProvider`
- `IMapFileProvider`
- `IFactorFileProvider`
- `IHistoryProvider`
- `IDataFeed`
- `IDataDownloader`
- `IUniverseSelectionModel`
- `IAlphaModel`
- `IPortfolioConstructionModel`
- `IRiskManagementModel`
- `IExecutionModel`
- `IBrokerageModel`

这些接口继续表达业务语义，不掺接入方式语义。

### 4.2 Module Contracts

新增统一模块契约，用于承载接入方式无关的生命周期语义。

建议最少包含：

- 模块标识
- 模块版本
- 模块种类
- 初始化参数
- pause / resume
- snapshot / restore
- 健康检查

这层接口不替代业务接口，而是包在业务接口外面。

### 4.3 Control Plane

负责：

- 读取 pipeline manifest
- 实例化模块
- 建立依赖关系
- 执行热切策略
- 管理状态迁移

### 4.4 Adapters / Loaders

负责把：

- 预置实现
- 远程服务模块
- 进程内插件模块
- 脚本模块

统一转换成 Engine Contracts + Module Contracts 的组合对象。

### 4.5 模块运行形态

这次重构不应把模块接入方式限定成 `RPC` 或 `DLL`。

更合理的做法是把它们统一抽象成“模块运行形态”，至少支持：

1. `remote-service`
   - 例如 gRPC / HTTP / WebSocket / MQ worker
2. `in-process-plugin`
   - 例如本地或上传后加载的 DLL
3. `script-runner`
   - 例如 Python 脚本、MATLAB 脚本、Julia 脚本、R 脚本
4. `out-of-process-local`
   - 例如本机独立进程，通过 stdin/stdout、socket、named pipe 交互

这些运行形态的共同要求只有四个：

1. 能接收参数
2. 能执行
3. 能返回结果
4. 能被控制面暂停、恢复、快照、替换

换句话说，真正固定下来的不是“只能 RPC / DLL”，而是统一模块契约。

---

## 5. 接口保留策略

这一节回答“怎么留接口”。

### 5.1 保留现有 Lean 业务接口不动

以下接口应保留其业务语义：

- `IUniverseSelectionModel`
- `IAlphaModel`
- `IPortfolioConstructionModel`
- `IRiskManagementModel`
- `IExecutionModel`
- `IBrokerageModel`

理由：

1. 这些接口已经对应 Lean 主链
2. 这些接口已经表达清楚各层职责
3. 直接替换会破坏现有生态

### 5.2 新增“模块生命周期接口”，而不是改业务接口签名

不要把 `Pause()`、`Snapshot()`、`Resume()` 直接塞进每个业务接口里。

原因：

1. 会污染业务接口语义
2. 会让所有现有实现都被迫修改
3. 会把“模块接入方式问题”变成“业务接口问题”

正确做法：

- 新增一套模块生命周期接口
- 让具体模块对象同时实现：
  - 业务接口
  - 生命周期接口

### 5.3 新增统一模块描述接口

系统需要一层统一的模块描述语义，用于 manifest 装配：

描述内容至少包含：

1. 模块类型
2. 业务类别
3. 版本
4. 接入方式
5. 参数 schema
6. snapshot 能力声明
7. 热切能力声明

### 5.4 新增统一模块工厂接口

不能让主算法或控制面直接 `new XXX(...)`。

必须通过统一工厂解析 manifest 并实例化模块。

工厂职责：

1. 根据 manifest 找到模块
2. 判断模块运行形态
3. 生成模块实例
4. 注入初始化参数
5. 返回符合业务接口 + 生命周期接口的对象

### 5.5 结果统计层接口保留策略

结果统计层不建议发明一个新的“统一 Evaluation 接口”替代 Lean 原链路。

因为 Lean 当前的真实统计链已经明确：

- `BacktestingResultHandler / LiveTradingResultHandler`
- `BaseResultsHandler`
- `StatisticsBuilder`
- `AlgorithmPerformance`
- `PortfolioStatistics`
- `TradeStatistics`
- `Statistics`
- `Report`

这里更合理的做法是：

- 保留统计主链
- 暴露统计扩展位

而不是用一个抽象的“evaluation service”盖过去。

---

## 6. 预置类重写原则

这一节回答“Lean 现有预置类怎么改写成符合接口的”。

核心原则：

> 预置类必须被改写成普通模块，而不是保留原样再额外补一套外部接入的特殊子类。

### 6.1 不保留“双轨制”

禁止下面这种结构：

1. 现有 `DefaultDataProvider` 继续直连主链
2. 另加 `RpcDataProvider` 给外部用
3. 控制面只认 `RpcDataProvider`
4. 预置类不走控制面

这会造成：

- 预置类不支持热插
- 预置类不支持统一生命周期
- 外部模块和预置模块行为不一致

### 6.2 正确做法：预置类也作为模块装配

例如：

- 当前的 `DefaultDataProvider`
- 当前的 `DownloaderDataProvider`
- 当前的 `CompositeAlphaModel`
- 当前的 `ImmediateExecutionModel`

都不再由主链直接 `new` 出来，而是：

1. 先注册成系统内置模块
2. 通过同一套 manifest 引用
3. 通过同一套模块工厂创建
4. 通过同一套控制面管理

这样预置类和外部类唯一的区别只在：

- 实现代码来源不同

而不是：

- 生命周期不同
- 管理方式不同

### 6.3 “预置类改写成模块实现”的语义

这里不是说一定要把所有预置类拆成真正外部 DLL 文件，也不是说一定要把它们搬成 RPC 服务。

这里的意思是：

- 预置类要被视为“模块实现”
- 允许通过和外部模块一样的装配入口被载入

也就是说，预置类可以：

- 仍然编译进 Lean 仓库
- 但对控制面来说，它们是“内置模块”
- 和外部 DLL 模块、RPC 模块、脚本模块处在同一抽象层

如果某个预置类未来也想变成远程服务或脚本实现，也必须走同一套模块契约，而不是另开特殊通路。

---

## 7. 各层重构计划

### 7.1 数据层

#### 当前真实挂载点

- `IDataProvider`
- `IMapFileProvider`
- `IFactorFileProvider`
- `IHistoryProvider`
- `IDataFeed`
- `IDataDownloader`

典型预置类：

- `DefaultDataProvider`
- `DownloaderDataProvider`
- `ApiDataProvider`
- `CompositeDataProvider`
- `HistoryProviderManager`

#### 保留接口策略

保留这些业务接口不动。

#### 预置类改写策略

把预置数据类都注册成模块实现：

1. `DefaultDataProvider` 改成内置 data-provider 模块
2. `DownloaderDataProvider` 改成内置 downloader 模块
3. `ApiDataProvider` 改成内置 remote-data 模块
4. `CompositeDataProvider` 改成内置 composite provider 模块
5. `HistoryProviderManager` 改成内置 history orchestration 模块

#### 控制面职责

数据控制面负责：

1. provider 注册
2. provider fallback
3. provider 切换
4. provider 健康检查
5. 数据层 snapshot / 恢复

#### composite 策略

数据层现有 composite 可复用：

- `CompositeDataProvider`
- `HistoryProviderManager`

但要把它们也纳入模块工厂和控制面，而不是直接静态实例化。

#### 热插边界

这层最适合最早实现热插拔。

---

### 7.2 选股层

#### 当前真实挂载点

- `IUniverseSelectionModel`
- `CompositeUniverseSelectionModel`

#### 保留接口策略

保留 `IUniverseSelectionModel` 不动。

#### 预置类改写策略

现有预置选股模型全部改成“内置 universe 模块”。

控制面不能区分：

- 这是预置 universe
- 还是外部上传 universe

两者都走同一装配路径。

#### composite 策略

`CompositeUniverseSelectionModel` 可直接保留，但不再直接由算法类 `new`。

它应作为一种“内置 composite universe 模块”存在。

#### 热插边界

可运行中热切，但切换时必须联动数据订阅刷新。

---

### 7.3 Alpha 层

#### 当前真实挂载点

- `IAlphaModel`
- `CompositeAlphaModel`

#### 保留接口策略

保留 `IAlphaModel` 不动。

#### 预置类改写策略

现有预置 alpha 全部改成“内置 alpha 模块”。

不能保留“预置 alpha 直接用，外部 alpha 走 `RpcAlphaModel`”这种双轨制。

应统一为：

1. manifest 选中某个 alpha 模块
2. 工厂创建模块实例
3. 控制面管理其生命周期

#### composite 策略

`CompositeAlphaModel` 可以直接复用。  
但它也必须是模块化对象，而不是算法类里直接 `new CompositeAlphaModel(...)`。

#### 热插边界

可运行中热切。  
如果 alpha 有内部状态，则模块必须支持：

- pause
- snapshot
- restore

---

### 7.4 仓位构造层

#### 当前真实挂载点

- `IPortfolioConstructionModel`

#### 保留接口策略

保留 `IPortfolioConstructionModel` 对 Lean 主链的最终输出语义。

也就是：

- 对主链依旧输出 `IPortfolioTarget`

#### 关键重构点

这里不能只做“预置类模块化”。

必须新增一层中间抽象：

- 外部 portfolio / processor 模块先输出统一仓位意图
- 固定控制面合并意图
- 最后再落到 `IPortfolioTarget`

#### 预置类改写策略

现有预置 portfolio 类也要改写成：

1. 内置 portfolio 模块
2. 或内置 portfolio-intent 模块

不能让预置类继续直接吐 target，而外部模块走另一条“意图层”通路。

否则又回到双轨制。

#### composite 策略

这一层当前没有可直接复用的 `CompositePortfolioConstructionModel`。

所以这里必须新增自己的合并层。

这个合并层不是特殊外部类，而是：

- 整个系统的统一 portfolio 合成控制面

#### 热插边界

可以热切，但合并语义必须固定在控制面里，不能分散到每个模块里。

---

### 7.5 风险层

#### 当前真实挂载点

- `IRiskManagementModel`
- `CompositeRiskManagementModel`

#### 保留接口策略

保留 `IRiskManagementModel` 不动。

#### 预置类改写策略

现有预置 risk 类改成“内置 risk 模块”。

#### composite 策略

`CompositeRiskManagementModel` 当前语义可直接沿用。

但它也必须纳入模块控制面，而不是作为代码里的静态组合器存在。

#### 热插边界

通常可运行中热切。

---

### 7.6 执行层

#### 当前真实挂载点

- `IExecutionModel`
- 订单事件回流到单一 execution model

#### 保留接口策略

保留 `IExecutionModel` 作为执行子模块的业务接口。

#### 关键重构点

这里不能把外部执行模块直接平行挂进去。

必须新增统一执行控制面：

1. 统一接收 target
2. 统一管理订单 ownership
3. 统一转发 order event
4. 统一做 pause / resume / snapshot / restore

#### 预置类改写策略

现有预置 execution 类，比如 `ImmediateExecutionModel`，也要改成：

- 内置 execution 模块

由执行控制面统一调用，而不是继续直接挂在主链上。

#### composite 策略

这里不建议做简单的“并列 composite”。

需要的是：

- 执行协调器
- 多执行子模块

#### 热插边界

执行层不是不能热插，而是必须先有：

- pause / resume
- snapshot / restore
- 未完成执行计划持久化
- 订单事件回放

没有这些能力时，只能空仓切换。

---

### 7.7 券商制度层

#### 当前真实挂载点

- `IBrokerageModel`
- `BrokerageModelSecurityInitializer`

#### 保留接口策略

保留 `IBrokerageModel` 不动。

#### 预置类改写策略

现有预置券商模型，比如：

- `DefaultBrokerageModel`
- `InteractiveBrokersBrokerageModel`
- 其他内置 brokerage model

都要改成“内置券商模块”。

不能让：

- 预置券商模型继续直连
- 外部 `Bitget` 才走插件路径

#### composite 策略

不建议做 `CompositeBrokerageModel`。

这里应保持：

- 单活跃券商模型
- 由券商控制面负责切换

#### 热插边界

这层理论上也能热切，但必须先有：

- pause / resume
- snapshot / restore
- 持仓与挂单状态重建
- security 重初始化策略

在这些能力没补齐前，只建议：

- 空仓无挂单切换
或
- 冷切

---

### 7.8 结果统计层

#### 当前真实模块链

- `BacktestingResultHandler / LiveTradingResultHandler`
- `BaseResultsHandler`
- `StatisticsBuilder`
- `AlgorithmPerformance`
- `PortfolioStatistics`
- `TradeStatistics`
- `Statistics`
- `Report`

#### 保留接口策略

不要发明一个抽象的统一“Evaluation API”去替代这条链。

正确做法是：

- 保留结果链主语义
- 在现有扩展位上做插件化

#### 预置类改写策略

现有统计与报告实现也要作为模块能力纳入控制面管理：

1. 预置 summary 指标
2. 预置 report element
3. 预置 drawdown / rolling sharpe / annual returns 等 report 组件

它们都应能通过同一套 manifest 被选择，而不是在 `Report.cs` 里写死。

#### composite / 扩展策略

结果统计层不是典型“并列 composite”，而是：

- 固定主链
- 指标集合可扩展
- 报告元素集合可扩展

#### 热插边界

结果统计层可以在 run 间切换，也可以在 pause 后切换。  
如果要在运行中切换，必须保证：

- 已采样统计数据不丢
- 新插件能消费旧 snapshot

---

## 8. Pipeline 组织方式

整个 pipeline 的组装本身也必须可编辑，不应写死在代码里。

错误做法：

- 在主算法或 runtime 里写：
  - `new UniverseSelectionHost(...)`
  - `new AlphaHost(...)`
  - `new RiskHost(...)`
  - `new ExecutionCoordinator(...)`

正确做法：

- pipeline 通过 manifest / config / 外部控制面定义
- runtime 根据配置装配模块图

### pipeline 配置至少应描述

1. 数据模块列表
2. universe 模块列表
3. alpha 模块列表
4. portfolio 模块列表
5. risk 模块列表
6. execution 模块列表
7. brokerage 模块
8. 结果统计插件列表
9. 模块顺序
10. composite / merge 规则
11. fallback 规则
12. 模块运行形态
13. 热插边界
14. pause / resume 规则
15. snapshot / restore 后端

### pipeline 配置覆盖的三条链

1. 数据链
2. 策略链
3. 结果统计链

### runtime 装配流程

1. 读取 pipeline manifest
2. 解析模块类型与版本
3. 装配控制面和插件
4. 建立依赖关系
5. 校验热插规则
6. 进入运行态

修改配置或上传新模块时：

1. 校验变更是否合法
2. 触发 pause
3. snapshot / 持久化
4. 卸载旧模块
5. 装载新模块
6. restore
7. resume

---

## 9. 分阶段实施计划

### Phase 1：统一模块语义

完成：

- 模块描述
- 模块工厂
- pipeline manifest
- 控制面接口
- 模块运行形态适配层

### Phase 2：优先改数据/选股/alpha/risk

把这些最容易热插的层先迁过去：

- DataProvider
- UniverseSelection
- Alpha
- Risk

### Phase 3：补 portfolio 合并层

新增统一仓位意图与合并控制面。

### Phase 4：补 execution 协调器

接管：

- order ownership
- order event routing
- execution snapshot/restore

### Phase 5：补 brokerage 控制面

最后处理最难的一层：

- 券商制度热切换
- security 重初始化
- 订单与持仓一致性

### Phase 6：把结果统计层插件化

让：

- summary 指标
- report element
- 参考序列分析器

都走统一配置装配。

---

## 10. 最终结论

为了满足你的要求，这次重构的核心不是“新增几种 `RpcXXX` 子类”，而是：

1. 保留 Lean 当前业务接口语义
2. 新增统一模块生命周期语义
3. 用控制面统一管理预置类和外部模块
4. 让预置类也走模块装配路径，而不是保留双轨制
5. 让 pipeline 组织本身配置化
6. 让数据链、策略链、结果统计链都能通过同一套 manifest 管理

最关键的判断有三条：

1. `RPC`、`DLL`、脚本执行器等都是并列的模块运行形态，不是前后备份关系
2. `Execution` 和 `Brokerage` 不是不能热插，而是必须先有 pause/resume + snapshot/restore + 状态回放
3. `PortfolioConstruction` 是最需要补新合并层的地方，因为 Lean 当前没有现成可复用的 `CompositePortfolioConstructionModel`
