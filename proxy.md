# Proxy / 插件接口设计调研报告

这份报告只做接口和流程调研，不修改实现。调研目标是站在“Engine 已经常驻运行、策略可以在本机或远程开发、策略更新不应该重启 Engine”的前提下，检查当前可插拔组件、提交 API、devkit、transport proxy 和热更新链路里是否存在接口设计不合理、无意义接口、死接口和过度复杂。

## 总体判断

当前方向是对的：策略开发者不应该直接改 Engine config、不应该手工替换 manifest、不应该手工 export 环境变量；策略提交应该通过控制面 API，manifest 只作为 Engine 内部 IR。

但现在实现还没有完全达到这个边界。主要问题不是某个函数名，而是三层接口混在了一起：

```text
策略开发者 API     strategy.py / devkit SDK / 本地测试
提交 API          bundle / artifact / manifest 校验 / 发布
Engine 内部 IR    PipelineManifest / ModuleConfiguration / IModule / transport wrappers
```

其中 `IModule`、`ModuleConfiguration`、`entryPoint`、`HotSwapMode`、transport command 等更像 Engine 内部适配层，不应该变成策略作者每天面对的东西。devkit 已经开始遮蔽这些细节，但遮蔽不完整，导致 bundle 能构建、文档能写通，不代表 Engine 端一定能安全热接入。

本报告里的“简化”不是把接口能力削成最低配，也不是把所有动态逻辑都改成 JSON。判断一个接口该不该保留，要看它承载的是用户真实的策略表达能力，还是 Engine 内部适配细节。前者应保留，甚至要给更干净的 SDK；后者才应该隐藏、生成或删除。

具体原则：

```text
保留：用户需要用代码表达动态策略决策的接口
隐藏：生命周期、加载形态、manifest 字段、transport command 等运行时样板
剔除：没有调度闭环、没有行为差异、用户声明后也不改变系统行为的接口
```

例如 `Input` 不能简单降级成只支持 JSON。静态 symbols 用 JSON 很合适，但如果客户需要按账户状态、日期、远程参数、实验开关、外部研究结果动态决定输入，代码接口就是必要能力。正确做法是同时支持“静态配置的低成本路径”和“代码化动态输入的高能力路径”，而不是用一个替代另一个。

## 接口复杂度评估口径

除了功能是否闭环，还要评估每个组件的“实现成本是否均衡”。如果一个可插拔组件要求用户实现二三十个方法/属性，而其他组件只需要实现一两个方法，这通常说明抽象边界不对：要么把 Lean/Engine 内部大接口直接暴露给了用户，要么缺少 adapter/base class。

这里把复杂度分成三类：

```text
业务复杂度：用户为了表达策略本来就需要写的逻辑，例如 signals/targets/risk。
接入复杂度：为了接入 Engine 必须写的生命周期、manifest、transport、类型适配。
领域复杂度：一个接口横跨多个领域，例如订单限制、费用、滑点、买卖力、结算。
```

合理的策略开发接口应该让用户主要承担业务复杂度。接入复杂度应由 devkit、生成代码、base class 或 adapter 吸收；领域复杂度只有在用户明确选择高级扩展点时才暴露。

下面的统计只计算组件自身业务接口，不把 `IModule` 的默认生命周期和属性计入每个组件。`Initialize`、`Pause`、`Resume`、`CreateSnapshot`、`RestoreSnapshot`、`CheckHealth`、`Key`、`Kind`、`ActivationMode`、`Version`、`HotSwapMode` 都属于统一接入层，应单独隐藏到 adapter/base class/生成代码里，不参与各组件之间的复杂度比较。

当前组件自身接口实现成本大致如下：

| 组件 | 组件自身接口要求，不含 `IModule` | 用户真正关心的业务方法 | 复杂度判断 |
| --- | --- | --- | --- |
| `Input` | `CreateInputs`，1 个方法 | `inputs(context)` 1 个方法 | 合理；动态能力有价值，应保留代码化输入 |
| `Universe` | `GetNextRefreshTimeUtc` + `CreateUniverses`，2 个方法 | `universe(context)` 1 个方法，或静态 symbol 列表 | 中等；刷新时间是高级能力，可默认化 |
| `Signal` | `Update` + `OnSecuritiesChanged`，2 个方法 | `signals(context)` 1 个方法 | 合理；证券变更通知应是可选 hook |
| `Target` | 单模块 `CreateTargets` + `OnSecuritiesChanged`，2 个方法；多模块切到 `CreateIntents`，1 个方法 | `targets(context, insights)` 1 个方法 | 方法数不高，但单/多模块接口不一致 |
| `Constraint` | `ManageRisk` + `OnSecuritiesChanged`，2 个方法 | `risk(context, targets)` 1 个方法 | 合理；证券变更通知应是可选 hook |
| `Execution` | `Execute` + `OnOrderEvent` + `OnSecuritiesChanged`，3 个方法 | `execute(context, targets)` 1 个方法；高级场景才需要 order event | 偏高但可解释；订单事件应是可选 hook |
| `MarketRule` | `IBrokerageModel` 3 个属性 + 约 15 个方法，约 18 个成员 | `market_rule(context, command, payload)` 或更细的 2-3 个 policy 方法 | 明显过重，用户侧不应实现完整 brokerage model |
| `Analyzer` | `RequiredObservations` + `Analyze`，2 个成员 | `analyze(observations)` 1 个方法；声明 observations 可作为返回值或装饰器 | 方法数合理，但调度未闭环 |

结论是：排除 `IModule` 后，多数组件自身只有 1-3 个业务成员，复杂度基本合理；真正失衡的是 `MarketRule`，因为它额外引入完整 `IBrokerageModel`，自身就接近 18 个成员。`Execution` 的 `OnOrderEvent` 属于真实高级能力，不应该删除，但应做成可选 hook。`Universe` 的刷新时间也是高级能力，普通静态 universe 不应强制用户关心。

## 接口职责表

下面按模块分开列，只写组件自身接口，不列 `IModule` 的统一生命周期接口。

### Input

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `CreateInputs` | 声明 Engine 应登记哪些市场输入 | pipeline 绑定或刷新 input 时 | `QCAlgorithm` / 当前上下文 | `InputRegistration` 列表 | 普通用户不直接实现，写 `strategy.inputs(context)`；需要保留动态代码能力 |

### Universe

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `GetNextRefreshTimeUtc` | 告诉 Framework 下次什么时候刷新 universe | Framework 调度 universe refresh 前 | 无 | 下一次 UTC 刷新时间 | 普通用户不应强制实现，静态 universe 可默认 |
| `CreateUniverses` | 创建 Lean universe selection 对象 | 算法初始化或 universe refresh 时 | `QCAlgorithm` | `Universe` 列表 | 普通用户写 `strategy.universe(context)` 或静态 symbols，adapter 负责转换 |

### Signal

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `Update` | 根据当前数据产生交易信号 | 每次收到新 `Slice` 数据时 | `QCAlgorithm`、`Slice` | `Insight` 列表 | 普通用户写 `strategy.signals(context)` |
| `OnSecuritiesChanged` | 通知信号模块证券池发生增删 | universe 改变后 | `QCAlgorithm`、`SecurityChanges` | 无 | 可选 hook，不强制普通策略实现 |

### Target

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `CreateTargets` | 把 insights 转成目标持仓 | Framework 处理 alpha 输出后 | `QCAlgorithm`、`Insight[]` | `IPortfolioTarget` 列表 | 普通用户写 `strategy.targets(context, insights)` |
| `CreateIntents` | 产生可合并的目标意图，而不是最终 target | 多 target 模块组合时 | `QCAlgorithm`、`Insight[]` | `PortfolioTargetIntent` 列表 | 不应作为普通用户额外规则；应由统一 target contract/adapter 吸收 |
| `OnSecuritiesChanged` | 通知 target 模块证券池发生增删 | universe 改变后 | `QCAlgorithm`、`SecurityChanges` | 无 | 可选 hook，不强制普通策略实现 |

### Constraint

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `ManageRisk` | 对目标持仓做风险约束或调整 | target 生成后、execution 前 | `QCAlgorithm`、当前 targets | 调整后的 targets | 普通用户写 `strategy.risk(context, targets)` |
| `OnSecuritiesChanged` | 通知约束模块证券池发生增删 | universe 改变后 | `QCAlgorithm`、`SecurityChanges` | 无 | 可选 hook，不强制普通策略实现 |

### Execution

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `Execute` | 把目标持仓转成订单行为 | risk 输出 targets 后 | `QCAlgorithm`、targets | 无，副作用是下单 | 普通用户写 `strategy.execute(context, targets)`；复杂执行可走高级接口 |
| `OnOrderEvent` | 让执行模块感知成交、拒单、取消等订单事件 | Engine 收到订单事件时 | `QCAlgorithm`、`OrderEvent` | 无 | 高级能力，应可选，不应成为简单执行策略的必填项 |
| `OnSecuritiesChanged` | 通知执行模块证券池发生增删 | universe 改变后 | `QCAlgorithm`、`SecurityChanges` | 无 | 可选 hook，不强制普通策略实现 |

### MarketRule

当前 manifest 里只有一个 `marketRule` 槽位，`PipelineBinder` 也是调用 `algorithm.SetBrokerageModel(...)` 绑定整个 `IBrokerageModel`。所以现状下，`FeeModel`、`SlippageModel`、`BuyingPowerModel`、`SettlementModel`、`ShortableProvider` 这些不是独立可插拔模块；如果要通过 Engine 插件系统替换它们，粒度是替换整个 `MarketRule`。

Lean 的 `SetBrokerageModel` 会设置新的 brokerage model，并在没有用户自定义 `SecurityInitializer` 的情况下重新初始化已有 securities，因此整块替换 `MarketRule` 可以连带更新 fee/slippage/buying-power 等模型。但这仍然不是“只插拔 fee model”这种细粒度能力。

当前 `TransportMarketRuleModule` 实际上只远程化了 `CanSubmitOrder`、`CanExecuteOrder` 和 `describe_market_rule` 里的杠杆、手续费、滑点；其他 brokerage 子模型大多委托给 `DefaultBrokerageModel`。所以更准确的用户侧抽象应该是一个窄的 `MarketRulePolicy`，而不是让用户以为自己能独立提交一组 market 子模型。

新的拆分方案应该把 Lean 需要的 `IBrokerageModel` 留在 Engine 内部，只作为稳定 adapter；真正可插拔的是 adapter 下面的一组小组件。结构如下：

```text
CompositeMarketModel : IBrokerageModel
  orderSubmitRule        -> CanSubmitOrder
  orderUpdateRule        -> CanUpdateOrder
  orderExecutionRule     -> CanExecuteOrder
  leverageRule           -> GetLeverage
  feeModel               -> GetFeeModel / order fee
  slippageModel          -> GetSlippageModel / slippage
  fillModel              -> GetFillModel
  buyingPowerModel       -> GetBuyingPowerModel
  settlementModel        -> GetSettlementModel
  marginInterestModel    -> GetMarginInterestRateModel
  shortableProvider      -> GetShortableProvider
  benchmarkProvider      -> GetBenchmark
```

manifest 不再只有一个 `marketRule`，而是一个 `market` 段，每个子能力可以独立指向一个 module。没写的字段走默认 brokerage model：

```json
{
  "market": {
    "base": "Default",
    "orderSubmitRule": "market.submit",
    "orderExecutionRule": "market.execute",
    "leverageRule": "market.leverage",
    "feeModel": "market.fee",
    "slippageModel": "market.slippage",
    "buyingPowerModel": "market.buying_power",
    "fillModel": "market.fill"
  }
}
```

每个子组件的建议接口如下：

| 子组件 | 用户侧接口 | 输入 | 输出 | 默认行为 | 建议热替换条件 |
| --- | --- | --- | --- | --- | --- |
| `OrderSubmitRule` | `can_submit_order(context, order)` | security、order、portfolio/order 摘要 | allow / reject reason | `DefaultBrokerageModel.CanSubmitOrder` | `Live`，只影响后续新订单 |
| `OrderUpdateRule` | `can_update_order(context, order, update)` | security、order、update request | allow / reject reason | `DefaultBrokerageModel.CanUpdateOrder` | `Live`，只影响后续改单 |
| `OrderExecutionRule` | `can_execute_order(context, order)` | security、order、market state | bool | `DefaultBrokerageModel.CanExecuteOrder` | `Live` 或 `RequiresPause`；回测可 live，实盘建议 pause |
| `LeverageRule` | `get_leverage(context, security)` | security、portfolio/account 摘要 | leverage decimal | default brokerage leverage | `RequiresFlatNoOrders`，会影响保证金和仓位约束 |
| `FeeModel` | `get_order_fee(context, order)` | security、order | fee amount/currency | default fee model | `Live`，替换后影响后续费用计算 |
| `SlippageModel` | `get_slippage(context, order)` | security、order、price/liquidity 摘要 | slippage decimal | default slippage model | `Live`，替换后影响后续成交价格估计 |
| `FillModel` | `fill(context, order)` 或 `get_fill_model` | security、order、market data 摘要 | fill event / fill model | default fill model | `RequiresPause`，会改变成交语义 |
| `BuyingPowerModel` | `get_buying_power_model` 或 narrow margin checks | security、portfolio、order | buying power result / model | default buying power model | `RequiresFlatNoOrders`，影响保证金和可下单数量 |
| `SettlementModel` | `get_settlement_model` | security、account type | settlement model/spec | default settlement model | `RequiresFlatNoOrders`，影响现金到账和持仓结算 |
| `MarginInterestModel` | `get_margin_interest_rate(context, security)` | security、account/cashbook | interest rate/spec | default margin interest model | `RequiresFlatNoOrders` 或 `RequiresPause` |
| `ShortableProvider` | `is_shortable(context, symbol, quantity)` | symbol、quantity、time | bool / available quantity | default shortable provider | `Live`，只影响后续 short order |
| `BenchmarkProvider` | `get_benchmark(context)` | securities / time | benchmark | default benchmark | `Live`，不应和交易规则绑死 |

热替换时不应该重新加载整个 `CompositeMarketModel`，而是替换其中一个子组件引用：

```text
提交 feeModel 新版本
  -> control API preflight fee module
  -> MarketModelRegistry.Replace("feeModel", newFeeModel)
  -> 对已有 Securities 只更新 security.FeeModel
  -> submit/slippage/buyingPower 等保持原引用
```

不同子组件对已有 securities 的更新方式也应分开：

| 子组件 | 替换后对已有 securities 的处理 |
| --- | --- |
| `OrderSubmitRule` / `OrderUpdateRule` / `OrderExecutionRule` | 不需要重建 securities；`CompositeMarketModel` 持有新 rule 引用即可 |
| `FeeModel` | 遍历 `algorithm.Securities`，只替换 `security.FeeModel` |
| `SlippageModel` | 遍历 `algorithm.Securities`，只替换 `security.SlippageModel` |
| `FillModel` | 遍历 `algorithm.Securities`，只替换 `security.FillModel` |
| `LeverageRule` | 遍历 securities 调 `security.SetLeverage(...)`；应要求空仓无挂单 |
| `BuyingPowerModel` | 遍历 securities，只替换 `security.BuyingPowerModel`；应要求空仓无挂单 |
| `SettlementModel` | 遍历 securities，只替换 `security.SettlementModel`；应要求空仓或 pause |
| `MarginInterestModel` | 遍历 securities，只替换 `security.MarginInterestRateModel` |
| `ShortableProvider` | 遍历 securities，只替换 shortable provider |
| `BenchmarkProvider` | 只替换 adapter 内 benchmark provider，不需要碰 securities |

这样拆分后，`IBrokerageModel` 不再是用户实现对象，而是 Engine 内部聚合器。用户提交的是小的 market 子组件；换 fee 不会让 submit rule、slippage、buying power、settlement 一起掉线。

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `CanSubmitOrder` | 判断订单是否允许提交给 brokerage | 下单前 | `Security`、`Order` | bool + 拒绝消息 | 可作为用户侧 policy 暴露 |
| `CanUpdateOrder` | 判断订单是否允许修改 | 修改订单前 | `Security`、`Order`、`UpdateOrderRequest` | bool + 拒绝消息 | 多数用户不关心，应有默认 brokerage 行为 |
| `CanExecuteOrder` | 判断订单在当前市场规则下是否可执行 | 回测 / 模拟成交前 | `Security`、`Order` | bool | 可作为用户侧 policy 暴露 |
| `ApplySplit` | 处理拆股对订单票据的影响 | split 事件发生时 | order tickets、split | 无 | 不应暴露给普通策略作者 |
| `GetLeverage` | 给证券返回杠杆 | 创建/配置 security 时 | `Security` | decimal leverage | 可作为简化 policy 的一部分 |
| `GetBenchmark` | 返回 brokerage benchmark | 初始化 brokerage model 时 | `SecurityManager` | `IBenchmark` | 不应属于普通 MarketRule |
| `GetFillModel` | 返回成交模型 | 创建/配置 security 时 | `Security` | `IFillModel` | 高级 brokerage 能力，普通 MarketRule 不应必填 |
| `GetFeeModel` | 返回手续费模型 | 创建/配置 security 时 | `Security` | `IFeeModel` | 可被简化成 fee policy，普通用户不应实现完整模型 |
| `GetSlippageModel` | 返回滑点模型 | 创建/配置 security 时 | `Security` | `ISlippageModel` | 可被简化成 slippage policy，普通用户不应实现完整模型 |
| `GetSettlementModel` | 返回结算模型 | 创建/配置 security 时 | `Security` / `AccountType` | `ISettlementModel` | 不应属于普通 MarketRule |
| `GetMarginInterestRateModel` | 返回融资利率模型 | 创建/配置 security 时 | `Security` | `IMarginInterestRateModel` | 不应属于普通 MarketRule |
| `GetBuyingPowerModel` | 返回买卖力模型 | 创建/配置 security 时 | `Security` / `AccountType` | `IBuyingPowerModel` | 高级 brokerage 能力，不应普通必填 |
| `GetShortableProvider` | 返回可卖空股票来源 | 创建/配置 security 时 | `Security` | `IShortableProvider` | 高级 brokerage 能力，不应普通必填 |

### Analyzer

| 接口 / 方法 | 作用 | 触发时机 | 输入 | 输出 | 用户是否应直接实现 |
| --- | --- | --- | --- | --- | --- |
| `RequiredObservations` | 声明 analyzer 需要哪些 observation | analyzer 初始化或调度前 | 无 | `ObservationKey` 集合 | 可由 `analyze` 返回或装饰器声明，不必手写接口属性 |
| `Analyze` | 消费 observations 并输出分析结果 | analyzer 调度时 | observation 字典 | `AnalyzerResult` | 普通用户写 `strategy.analyze(observations)`；但当前调度未闭环 |

因此，“简化”要按下面的规则执行：

```text
如果接口成员是用户表达策略必须的能力：保留，并把 API 做干净。
如果接口成员是高级能力：保留为可选 hook，不强制普通策略实现。
如果接口成员只是 Engine 接入样板：隐藏到 adapter/base class/生成代码。
如果接口成员声明后没有运行时行为：补闭环或删除。
```

## 高优先级问题

### 1. 热更新不是事务化的，失败后可能留下中间态

位置：

- `Lean/Common/Modules/PipelineRuntime.cs`
- `Lean/Algorithm/Modules/PipelineHotReloadService.cs`
- `Lean/Algorithm/Modules/PipelineBinder.cs`

当前 reload 过程大致是：

```text
Pause old -> Snapshot old -> Unload old -> Load new -> Restore new -> Resume -> Bind algorithm
```

风险在于：旧模块在新模块成功绑定到 `QCAlgorithm` 之前已经被卸载。如果 `Load new`、`Restore` 或 `PipelineBinder.Bind` 失败，当前代码只记录错误，没有把旧模块恢复回去。对常驻 Engine 来说，这不是“提交失败不影响当前运行”，而是可能进入“部分模块已替换、算法未完成绑定”的状态。

这和“不重启 Engine 的策略更新”目标冲突。提交 API 应该做到：新版本先被完整预检、加载、健康检查、绑定计划验证，只有最后 commit 阶段才切换 active pipeline。失败时当前 pipeline 必须继续工作。

建议方向：

```text
prepare(new manifest) -> load candidate modules -> validate bind/support matrix -> health check
commit atomically -> swap active pipeline -> dispose replaced modules
rollback on any prepare/commit failure
```

### 2. Universe 组件链路没有闭环

位置：

- `Lean/Algorithm/Modules/RemoteServiceModuleFactory.cs`
- `Lean/Algorithm/Modules/ScriptRunnerModuleFactory.cs`
- `Lean/Algorithm/Modules/OutOfProcessWorkerModuleFactory.cs`
- `Lean/Common/Modules/ExternalAssemblyModuleFactory.cs`
- `strategy_devkit/build.py`

问题有两层。

第一，transport factory 只支持：

```text
Input / Signal / Target / Constraint / Execution / MarketRule / Analyzer
```

不支持 `Universe`。也就是说 `Universe` 不能通过 `RemoteService`、`ScriptRunner`、`OutOfProcessWorker` 接入。

第二，devkit 生成的 `GeneratedUniverseModule` 继承 `ManualUniverseSelectionModel`，但没有实现 `IModule`。而 `ExternalAssemblyModuleFactory` 对 `InProcessPlugin` 的硬要求是目标类型必须实现 `IModule`。因此当前 devkit 可能生成一个能编译、能打包、能提交的 bundle，但 Engine 加载 universe 模块时会失败。

这属于接口支持矩阵和提交前校验缺失。提交 API 现在只验证 `kind` 和 `activationMode` 是合法枚举，没有验证“这个 kind 是否真的支持这个 activation mode”，也没有验证 DLL entryPoint 是否实现 Engine 要求的接口。

建议方向：

```text
提交 API 增加 kind × activationMode 支持矩阵
devkit build 阶段对生成模块做 Engine 级别 contract check
Universe 要么补齐 IModule adapter，要么明确限制为配置型 universe，不暴露为普通插件
```

### 3. Target 单模块和多模块要求的接口不同

位置：

- `Lean/Algorithm/Modules/PipelineBinder.cs`
- `Lean/Algorithm/Modules/CompositePortfolioConstructionModule.cs`
- `Lean/Algorithm/Portfolio/IPortfolioIntentModel.cs`

当前规则是：

```text
target 模块数量 = 1 -> 必须实现 IPortfolioConstructionModel
target 模块数量 > 1 -> 每个模块必须实现 IPortfolioIntentModel
```

这个规则对策略开发者不直观，也容易出现“单模块可用，一加第二个模块就报接口不匹配”的问题。它把组合策略的内部实现细节泄露到了插件 contract 里。

建议方向是统一 target contract。更干净的设计是：

```text
用户侧 Target 只产出 intent 或 target spec
Engine 内部统一做 merge/coordinator/materialize
```

如果短期不改，也至少要在提交 API 或 devkit 检查：当 `target` 数组长度大于 1 时，对应模块必须实现 intent contract。

### 4. MarketRule 暴露成 IBrokerageModel 过大

位置：

- `Lean/Algorithm/Modules/TransportMarketRuleModule.cs`

`MarketRule` 当前对接 Lean 的 `IBrokerageModel`，但 `IBrokerageModel` 是一个很大的接口，包含买卖力、手续费、滑点、结算、shortable、benchmark、split 等很多职责。transport wrapper 实际只把这些命令远程化：

```text
describe_market_rule
can_submit_order
can_execute_order
```

其他大量方法都委托给 `DefaultBrokerageModel`。这说明用户真正需要实现的不是完整 brokerage model，而是一个小得多的 market rule policy。

从接口复杂度看，这也是当前最失衡的组件：不计 `IModule` 默认生命周期和属性，`MarketRule` 自身仍然要面对 `IBrokerageModel` 的 3 个属性和约 15 个方法，约 18 个成员；而 `Signal`、`Target`、`Constraint` 自身只有 1-3 个业务成员。这个差距说明 `IBrokerageModel` 不应作为用户侧 contract。

建议方向：

```text
用户侧暴露 MarketRulePolicy:
  can_submit_order
  can_execute_order
  describe_fee_slippage_leverage

Engine 内部 adapter 再把它转换为 IBrokerageModel
```

这样策略作者不会误以为自己需要理解和实现完整券商模型。

### 5. Analyzer / Observation 目前像未闭环的预留设计

位置：

- `Lean/Common/Modules/IAnalyzerModule.cs`
- `Lean/Common/Modules/IObservationProducer.cs`
- `Lean/Common/Modules/IObservationConsumer.cs`
- `Lean/Algorithm/Modules/PipelineBinder.cs`
- `Lean/Common/Modules/PipelineRuntime.cs`

`PipelineRuntime` 会加载 analyzer 模块，transport analyzer 也能声明 `RequiredObservations` 并执行 `Analyze`。但 `PipelineBinder.Bind` 没有把 analyzer 接入算法运行循环；`IObservationProducer` 在生产代码里没有实际使用点；`RequiredObservations` 也没有被统一调度器消费。

这说明 analyzer 当前更像“可以加载的模块”，不是“已经成为运行时数据流的一部分的模块”。如果文档把 analyzer 当作完整插件类型，容易误导。

建议方向：

```text
短期：文档标记 Analyzer 为实验性 / 输出观察模块，说明触发方还不完整
中期：增加 ObservationBus 或 AnalyzerScheduler，明确谁收集 observations、何时调用 Analyze、结果写到哪里
长期：删除未使用的 IObservationProducer，直到有真实 producer 接入
```

## 中优先级问题

### 6. Input 作为模块仍然偏重

位置：

- `Lean/Algorithm/Modules/IInputModule.cs`
- `Lean/Algorithm/Modules/PipelineBinder.cs`
- `strategy_devkit/sdk.py`

重命名为 `Input` 后，语义比原来的 `DataSubscription` 清楚很多：它登记 Engine 应该加入哪些 market input，不负责真正下载数据。

但它仍然是一个模块接口：

```csharp
IEnumerable<InputRegistration> CreateInputs(QCAlgorithm algorithm)
```

对于最常见场景，输入只是静态声明：

```json
[
  {"symbol": "SPY", "resolution": "Daily"},
  {"symbol": "QQQ", "resolution": "Daily"}
]
```

把这种东西做成一个 DLL 类或远程 command，收益有限，复杂度偏高。它的合理性只在这些场景成立：

```text
输入需要根据时间、状态、远程配置动态变化
输入声明要和策略代码一起编译、测试、发布
输入生成逻辑需要被 devkit 管住，而不是手写 manifest
```

建议方向：

```text
用户侧保留 strategy.inputs(context) 这种干净写法
Engine 内部可以继续生成 IInputModule adapter
manifest 里也可以支持纯 JSON inputs，作为静态输入的低成本路径
动态输入仍然保留代码接口，由 devkit/adapter 承接复杂逻辑
```

也就是说，`Input` 的“动态决策能力”要保留；应该简化的是 `IInputModule`、`InputRegistration`、manifest stage、transport command 这些 Engine 接入样板，不是把输入能力砍成静态列表。普通策略作者应感觉自己在写 `strategy.inputs(context)`，而不是在开发一个 Engine 模块。

### 7. IModule 生命周期对用户侧过重

位置：

- `Lean/Common/Modules/IModule.cs`
- `Lean/Common/Modules/ModuleState.cs`
- `strategy_devkit/build.py`

生命周期方法现在命名已经去掉 `Async`，比之前更好：

```text
Initialize / Pause / Resume / CreateSnapshot / RestoreSnapshot / CheckHealth
```

但这组接口本身仍然偏 Engine 内部。大多数策略作者不应该手写这六个方法，也不应该理解 `ModuleSnapshot`、`ModuleHealthCheckResult`、`CancellationToken` 的运行时细节。

当前 devkit 通过 `LeanStrategy` 遮蔽了这些方法，这是正确方向。问题在于生成的 C# adapter 仍然把完整 `IModule` 样板暴露在生成代码里，后续如果用户需要写 C# 插件，学习成本仍然较高。

建议方向：

```text
IModule 保留为 Engine 内部 lifecycle contract
用户侧提供 BaseModule / StrategyComponent / SDK adapter
用户只实现业务方法，例如 signals/targets/risk/execute/market_rule/analyze
生命周期只作为可选 hook 暴露
```

### 8. ActivationMode 的区分有重复

位置：

- `Lean/Common/Modules/ModuleActivationMode.cs`
- `Lean/Algorithm/Modules/ScriptRunnerModuleFactory.cs`
- `Lean/Algorithm/Modules/OutOfProcessWorkerModuleFactory.cs`

`ScriptRunner` 和 `OutOfProcessWorker` 当前都使用 `JsonLineProcessTransportClient`，参数也都是：

```text
command
arguments
workingDirectory
```

两者运行机制基本相同，主要差异是命名语义。这个区分对 Engine 内部可能有意义，但对提交 API 和策略作者来说会增加选择成本。

建议方向：

```text
用户侧统一成 Process 或 Worker
内部如果确实要区分短生命周期脚本和长期 worker，再由 SDK/manifest 生成层处理
```

### 9. entryPoint 对 transport 模块意义不清

位置：

- `Lean/Common/Modules/ModuleConfiguration.cs`
- `Lean/Common/Modules/PipelineManifestJsonLoader.cs`
- `Lean/Common/Modules/ExternalAssemblyModuleFactory.cs`
- `Lean/Algorithm/Modules/RemoteServiceModuleFactory.cs`

`entryPoint` 对 `BuiltIn` 和 `InProcessPlugin` 是真实类型名，必须存在。但对 `RemoteService`、`ScriptRunner`、`OutOfProcessWorker` 来说，实际调用靠的是 transport command，`entryPoint` 基本只是逻辑标识。

由于 `ModuleConfiguration` 强制要求 `entryPoint`，transport 模块也必须填一个不会被真正解析的字符串，例如：

```text
strategy-devkit.signal
strategy-devkit.target
```

这会让用户误以为远程服务也需要某种函数名或类名 entry point。

建议方向：

```text
entryPoint 只对 BuiltIn / InProcessPlugin 必填
transport 模块改用 serviceName / componentName / endpointName 这类语义字段
提交 API 根据 activationMode 做字段级校验
```

### 10. ModuleConfiguration.dependencies 目前没有实际调度作用

位置：

- `Lean/Common/Modules/ModuleConfiguration.cs`
- `Lean/Common/Modules/PipelineRuntime.cs`

`dependencies` 被解析、去重、参与 `ShouldReuse` 比较，但没有用于：

```text
加载顺序拓扑排序
依赖存在性校验
循环依赖检测
reload 影响范围计算
```

这会形成“看起来可以声明依赖，但声明了也不改变运行时行为”的接口。

建议方向：

```text
要么先从用户侧 manifest/schema 移除
要么补齐 dependency graph: validate -> topological load -> reload impacted dependents
```

### 11. ModuleRegistry 目前像死代码

位置：

- `Lean/Common/Modules/ModuleRegistry.cs`
- `Lean/Common/Modules/IModuleRegistry.cs`

生产路径里没有看到 `ModuleRegistry` 被 `PipelineRuntime`、提交 API 或 control plane 使用。实际配置来源是 `PipelineManifest.Modules`，实际加载状态由 `ModuleControlPlane._loadedModules` 管。

这类 registry 容易让后续维护者误判系统里存在一个统一模块注册中心，但现在没有。

建议方向：

```text
如果没有真实使用计划，删除或移动到未来设计文档
如果要保留，明确它是提交 API 的 catalog 还是 Engine runtime 的 active registry
```

### 12. DataSource 枚举和提交 API 支持不一致

位置：

- `Lean/Common/Modules/ModuleKind.cs`
- `strategy_submit_api.py`

`ModuleKind` 有 `DataSource`，但提交 API 的 `MODULE_KINDS` 不包含 `DataSource`。如果 `DataSource` 是 Engine 内部标注现有数据 provider 的用途，这没问题；但如果文档把它列为可插拔组件，提交 API 会拒绝。

建议方向：

```text
明确 DataSource 是内部分类，暂不作为策略可提交模块
或者补齐 DataSource 的 contract、factory、submit schema、binder
```

## 提交 API / 云支持问题

### 控制面应拆成添加、删除、接入三类接口

当前提交 API 把 artifact 落盘、manifest 替换、模块启用混在一起。更合理的控制面应该明确区分三类动作：

```text
模块添加：把一个模块实现注册到对应类型的模块仓库，不代表启用
模块删除：从模块仓库删除一个未被使用的模块实现，或删除某个版本
模块接入：把仓库里的某个模块实现实例化并接入当前运行 pipeline
```

这里的“仓库”不是策略运行态，而是可用模块 catalog。例如：

```text
input 仓库       json-input@1.0 / python-input@2.1 / research-input@dev
broker 仓库      default-broker@1.0 / ibkr-broker@1.0 / custom-broker@202605
fee 仓库         default-fee@1.0 / tiered-fee@1.2 / remote-fee@dev
signal 仓库      momentum-signal@1.0 / ml-signal@3.4
```

只有“接入”才会影响 Engine 当前运行状态。这个分离可以解决几个问题：

```text
可以先上传/注册模块，做校验和健康检查，但不影响正在运行的策略
可以预置一批可调参数模块，接入时只传 config 即可
同一个模块实现可以被多次接入，每次使用不同 instance config
删除模块时可以检查引用计数，避免删掉正在运行的实现
```

建议控制面资源模型：

```text
ModuleDefinition
  id: string
  kind: Input / Signal / Target / FeeModel / SlippageModel / ...
  version: string
  activationMode: InProcessPlugin / RemoteService / Process / BuiltIn
  artifact: dll / script / remote endpoint / built-in type
  contract: supported methods and schema
  defaultConfigSchema: JSON schema or typed config description

ModuleInstance
  instanceId: string
  definitionId: string
  kind: same as definition.kind
  config: per-instance user config
  hotSwapMode: derived or override-checked
  status: loaded / active / paused / failed
```

接口语义建议如下，名称可以按工程实际调整：

```http
POST /v1/modules
```

把模块实现添加到仓库。请求只描述“这个模块是什么、怎么加载、artifact 在哪里、默认 config schema 是什么”，不启用。

```json
{
  "kind": "Input",
  "moduleId": "json-input",
  "version": "1.0.0",
  "activationMode": "BuiltIn",
  "entryPoint": "QuantConnect.Algorithm.Modules.JsonInputModule",
  "artifact": null,
  "configSchema": {
    "type": "object",
    "properties": {
      "symbols": {"type": "array"},
      "resolution": {"type": "string"}
    }
  }
}
```

```http
DELETE /v1/modules/{kind}/{moduleId}/versions/{version}
```

从仓库删除模块实现。只有没有 active instance 引用时才允许删除；否则返回引用列表。

```http
POST /v1/pipeline/instances
```

把仓库里的模块实现实例化，但可以先不启用。这里传的是 per-instance config，类似 Unity / UE 编辑器里给组件实例调公开变量。

```json
{
  "instanceId": "input.us-largecap",
  "kind": "Input",
  "moduleId": "json-input",
  "version": "1.0.0",
  "config": {
    "symbols": [
      {"ticker": "SPY", "securityType": "Equity", "market": "usa"},
      {"ticker": "QQQ", "securityType": "Equity", "market": "usa"}
    ],
    "resolution": "Daily",
    "fillForward": true
  }
}
```

```http
POST /v1/pipeline/attach
```

真正接入当前运行 pipeline。请求只说把哪些 instance 接到哪个 stage 或 market 子槽位：

```json
{
  "strategyId": "full-demo",
  "version": "20260523-001",
  "stages": {
    "inputs": ["input.us-largecap"],
    "signal": ["signal.momentum"],
    "target": ["target.equal-weight"],
    "constraint": ["risk.max-drawdown"],
    "execution": ["execution.market-order"]
  },
  "market": {
    "feeModel": "fee.tiered-us",
    "slippageModel": "slippage.volume-share",
    "orderSubmitRule": "submit.us-hours-only"
  }
}
```

也可以支持创建并接入一步完成，但语义仍然要分开：先创建/校验 instance，再 commit attach。

```http
POST /v1/pipeline/detach
```

从当前 pipeline 解绑某个 instance。解绑不等于删除模块实现，也不等于删除 instance 历史；它只影响运行 pipeline。

这个模型能支持“预置可调参数模块”。例如可以内置一个 `json-input` 模块，它的代码实现固定，用户每次只改 instance config：

```json
{
  "kind": "Input",
  "moduleId": "json-input",
  "version": "1.0.0",
  "config": {
    "symbols": ["SPY", "QQQ", "IWM"],
    "resolution": "Minute"
  }
}
```

这样既保留 `Input` 的动态代码能力，又给静态输入提供低成本路径：

```text
简单用户：接入预置 json-input，并在 config 里改 symbols/resolution
高级用户：添加 custom-input 模块，代码里动态决定 inputs，然后接入它
```

接入时的 config 不应该直接塞进全局 manifest 字段，而应作为 `ModuleInstance.config` 进入模块初始化和 transport payload：

```text
ModuleDefinition 描述模块实现
ModuleInstance.config 描述本次接入怎么调参
Pipeline attachment 描述实例接到哪个 stage
```

这样可以做到同一个模块实现多实例复用：

```text
json-input@1.0
  input.us-equity     config: SPY/QQQ/IWM, Daily
  input.crypto        config: BTCUSD/ETHUSD, Minute

tiered-fee@1.2
  fee.us-equity       config: US fee tiers
  fee.crypto          config: crypto venue fee tiers
```

对 Engine 来说，最终仍然可以生成内部 `PipelineManifest`，但 manifest 变成控制面编译产物，不再是用户接口。编译过程大致是：

```text
ModuleDefinition + ModuleInstance + PipelineAttachment
  -> contract validation
  -> preflight load/health/config schema validation
  -> generated PipelineManifest
  -> prepare/commit hot reload
```

这也给删除提供清晰规则：

```text
detach instance：停止在 pipeline 中使用
delete instance：删除未 active 的实例配置
delete module definition/version：删除仓库里的实现，要求没有实例引用
```

### 13. 提交 API 校验深度不够

位置：

- `strategy_submit_api.py`

当前提交 API 能校验：

```text
manifest 基本结构
module kind 枚举
activationMode 枚举
必要参数存在
stage 引用的 module key 存在
bundle file 路径安全
sourceUrl sha256
```

但还不能校验：

```text
kind × activationMode 是否真的支持
InProcessPlugin assemblyPath 是否存在于 releaseDir
entryPoint 类型是否存在
entryPoint 是否实现 IModule 以及对应 Lean 接口
RemoteService 是否可达、是否协议兼容
ScriptRunner/OutOfProcessWorker 是否能启动并通过 initialize/health
Analyzer 是否真的会被运行时消费
Target 多模块接口是否匹配
```

这会导致提交成功，但 Engine 热加载失败。对于云开发/远程提交，这是非常差的体验，因为失败发生在 Engine 内部运行期，而不是提交响应里。

建议方向：

```text
POST /v1/strategies/submit
  1. 写入 release staging dir
  2. 生成 candidate manifest
  3. preflight contract check
  4. optional dry-run load/health
  5. commit live manifest
```

提交 API 应该返回“已接收并切换”或“拒绝并说明原因”，而不是只负责写 manifest。

### 14. sourceUrl 支持已经有了，但缺少 artifact trust policy

位置：

- `strategy_submit_api.py`
- `strategy_devkit/bundle.py`

远程文件现在可以通过 `sourceUrl` 下载，并可选 `sha256` 校验。这解决了“远程文件怎么办”的基本问题。

但上云后还需要更明确的策略：

```text
是否强制 sha256
是否限制 host allowlist
是否限制最大文件大小
是否支持私有对象存储鉴权
是否做 artifact cache
是否把下载和提交事务绑定
```

建议方向：

```text
本地开发可以允许无 sha256
云/生产提交强制 sha256 + size limit + allowlist / signed URL
```

## 低优先级 / 命名和边界问题

### 15. ModuleState.ActivationMode 固定返回 BuiltIn，可能误导

位置：

- `Lean/Common/Modules/ModuleState.cs`
- `Lean/Common/Modules/PluginModuleHandle.cs`

`ModuleState.ActivationMode` 固定是 `BuiltIn`。外部 DLL 插件会被 `PluginModuleHandle` 包一层，外层报告 `InProcessPlugin`。运行时看外层通常没问题，但插件作者在模块内部如果查看自己的 `_state.ActivationMode`，会看到 `BuiltIn`，这和实际加载方式不一致。

建议方向：

```text
ModuleState 不暴露 ActivationMode
或者 Initialize 时从 configuration 注入实际 activationMode
```

### 16. Version 语义不统一，可能影响 ShouldReuse

位置：

- `Lean/Common/Modules/ModuleState.cs`
- `Lean/Common/Modules/PipelineRuntime.cs`
- transport wrapper modules

transport wrapper 的 `Version` 来自 manifest configuration；`ModuleState.Version` 来自 assembly version。`PipelineRuntime.ShouldReuse` 比较的是：

```text
existing.Version == nextConfiguration.Version
```

如果 InProcessPlugin 的 assembly version 是 `1.0.0.0`，manifest version 是 `20260521-001`，即使配置没变，也可能被判断为不能复用，造成额外 reload。

建议方向：

```text
运行时复用比较使用 previousConfiguration.Version vs nextConfiguration.Version
IModule.Version 表示实现版本或运行版本，不参与 manifest diff
或者 Initialize 时把 configuration.Version 写入 ModuleState runtimeVersion
```

### 17. transport command 是内部协议，不应成为用户文档主入口

位置：

- `Lean/Algorithm/Modules/TransportProtocol.cs`
- `strategy_devkit/sdk.py`

当前 command 包括：

```text
register_inputs
update_signal
create_targets
manage_risk
execute_targets
describe_market_rule
can_submit_order
can_execute_order
analyze
```

这些 command 对 Engine proxy 很有用，但策略作者不应该直接学习它们。devkit 的 `LeanStrategy` 已经提供了更干净的方法名：

```text
inputs / universe / signals / targets / risk / execute / market_rule / analyze
```

建议后续文档把 transport command 放到“协议附录”，主流程只讲 devkit 方法。

## 每个可插拔组件的接口状态

### Input

当前职责：登记 Engine 需要加入的 market input。

接口状态：可用，但对静态输入来说偏重。动态输入能力有价值，应通过 `strategy.inputs(context)` 这类干净 SDK 保留；不建议让用户手写 `IInputModule`。

主要风险：会被误解成真正的数据订阅或数据下载模块；另一个风险是过度简化时误删动态输入能力。

### DataSource

当前职责：看起来用于标注实际数据提供者类，但不在提交 API 可插拔类型里。

接口状态：不是完整策略插件类型。

主要风险：枚举存在但提交流程不支持，文档要避免把它列成用户可接入组件。

### Universe

当前职责：Lean universe selection。

接口状态：未闭环。transport 不支持，devkit 生成的 DLL 类型也不满足 `InProcessPlugin` 的 `IModule` 要求。

主要风险：bundle 能生成但 Engine 加载失败。

### Signal

当前职责：产生 Lean `Insight`。

接口状态：transport wrapper 较完整，devkit 映射也自然。

主要风险：当前 payload 只传时间，没有传 `Slice data`，远程 signal 如果需要当前 bar 数据，还需要扩展 context。

### Target

当前职责：从 insights 生成 portfolio targets。

接口状态：单模块可用，多模块规则不直观。

主要风险：单模块和多模块要求不同接口，组合策略时容易失败。

### Constraint

当前职责：风险管理，过滤或调整 targets。

接口状态：transport wrapper 较完整。

主要风险：命名 `Constraint` 比 Lean 原生 `RiskManagement` 抽象更泛，文档需要明确它处理的是 target 层约束，不是任意运行时约束。

### Execution

当前职责：把 targets 转成订单。

接口状态：可用，但当前 transport 只支持简单 market order 指令。

主要风险：如果用户以为能提交限价、止损、分批执行、撤单等复杂执行逻辑，目前协议不够。

### MarketRule

当前职责：订单提交/执行限制、手续费、滑点、杠杆等市场规则。

接口状态：底层接到 `IBrokerageModel`，用户侧应缩小为 policy。

主要风险：当前抽象过大，容易把券商模型复杂度暴露给策略作者。

### Analyzer

当前职责：消费 observations，输出结构化分析结果。

接口状态：加载和 transport 有了，但运行时调度未闭环。

主要风险：用户实现了 analyzer，也未必会被 Engine 自动调用。

## 建议的下一轮改造顺序

第一步先补提交 API 的 preflight，不动策略作者接口：

```text
validate kind × activationMode matrix
validate required parameters by activationMode
validate InProcessPlugin assembly + entryPoint + interface
validate stage-specific interface rule
validate remote/process initialize + health
```

第二步修 Universe 闭环：

```text
GeneratedUniverseModule 实现 IModule
或者新增 Universe adapter wrapper
或者把 universe 暂时降级为纯配置/静态 symbols，不作为完整插件模块
```

第三步把 hot reload 改成 prepare/commit/rollback：

```text
先加载 candidate pipeline
先做 bind validation
最后再切换 active pipeline
失败不影响 current pipeline
```

第四步收敛用户侧 API：

```text
IModule 继续作为 Engine 内部 contract
LeanStrategy / devkit 作为策略作者唯一主入口
manifest / entryPoint / transport command 作为内部生成物
保留代码化动态能力，不把所有能力强行压成 JSON
```

第五步清掉未闭环接口：

```text
ModuleRegistry 如果不用就删
dependencies 如果不用就从用户 schema 隐藏
IObservationProducer 如果没有 producer/scheduler 就先不公开
DataSource 如果不是可提交插件就从提交文档剥离
```

## 结论

当前系统已经有了“控制面提交 + Engine 热加载 + 多运行形态 adapter”的骨架，但还没有把用户 API、提交 API、Engine 内部 IR 完全分层。最应该优先处理的是安全性和闭环问题：提交前校验、Universe 可加载性、事务化热更新。

在这些闭环之前，不建议继续扩大插件类型或把更多内部接口写进使用文档。策略开发者应该只面对 devkit 的单文件策略 API；`IModule`、manifest、transport command、activation mode 应该逐步退到生成层和 Engine 内部。
