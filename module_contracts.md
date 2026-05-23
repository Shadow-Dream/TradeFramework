# Engine 可插拔组件接入契约

这份文档说明每个 pipeline 组件接入 Engine 时必须满足的接口要求。重点是 `InProcessPlugin` DLL 接入；`RemoteService`、`ScriptRunner`、`OutOfProcessWorker` 走 transport wrapper，不要求策略代码直接实现 C# 接口。

## 1. 通用 DLL 接入规则

DLL 模块不是按固定函数名加载，而是按 manifest 里的 `entryPoint` 加载一个 C# 类型：

```json
{
  "key": "input.main",
  "kind": "Input",
  "activationMode": "InProcessPlugin",
  "entryPoint": "MyStrategy.Modules.MyInputModule",
  "version": "20260522-001",
  "parameters": {
    "assemblyPath": "{{releaseRoot}}/artifacts/MyStrategyModules.dll"
  },
  "hotSwapMode": "Live"
}
```

Engine 会执行：

```text
assemblyPath -> load DLL
entryPoint   -> resolve public type
type         -> must implement IModule
constructor  -> public parameterless constructor
Initialize(configuration)
stage bind   -> must implement the stage-specific Lean interface
```

每个 DLL 模块都必须实现 `QuantConnect.Modules.IModule`：

```csharp
public interface IModule
{
    string Key { get; }
    ModuleKind Kind { get; }
    ModuleActivationMode ActivationMode { get; }
    string Version { get; }
    ModuleHotSwapMode HotSwapMode { get; }

    ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default);
    ValueTask Pause(CancellationToken cancellationToken = default);
    ValueTask Resume(CancellationToken cancellationToken = default);
    ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default);
    ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default);
    ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default);
}
```

这些方法名只表达生命周期用途，不把异步实现细节写进名字。虽然返回类型是 `ValueTask`，但策略作者通常不需要关心这一点；建议复用 `ModuleState`、公共基类或 devkit 生成代码实现生命周期样板。策略业务逻辑放在对应 stage 接口的方法里。

## 2. 组件类型和接口

| kind | stage 字段 | DLL 必须实现的接口 | Engine 绑定位置 |
| --- | --- | --- | --- |
| `Input` | `inputs` | `IInputModule` | `algorithm.AddSecurity(...)` |
| `Universe` | `universe` | `IUniverseSelectionModel` + `IModule` | `SetUniverseSelection` / `AddUniverseSelection` |
| `Signal` | `signal` | `IAlphaModel` + `IModule` | `SetAlpha` / `AddAlpha` |
| `Target` | `target` | 单 target: `IPortfolioConstructionModel` + `IModule`; 多 target: `IPortfolioIntentModel` + `IModule` | `SetPortfolioConstruction` |
| `Constraint` | `constraint` | `IRiskManagementModel` + `IModule` | `SetRiskManagement` / `AddRiskManagement` |
| `Execution` | `execution` | `IExecutionModel` + `IModule` | `SetExecution` |
| `MarketRule` | `marketRule` | `IBrokerageModel` + `IModule` | `SetBrokerageModel` |
| `Analyzer` | `analyzer` | `IAnalyzerModule` | loaded into pipeline runtime; analyzer 调用侧消费 |

`IAnalyzerModule` 已经继承 `IModule` 和 `IObservationConsumer`。

`DataSource` 是 Engine 内部/基础设施侧的数据提供者角色，例如本地数据文件、历史数据 provider、map/factor file provider。它不是策略 pipeline stage，也不通过策略提交 manifest 接入。

## 3. Input

Input 模块只负责把策略需要的市场输入登记给 Engine。它不下载数据，也不实现数据源；真正的数据读取、历史数据加载、实盘行情接入仍由 Lean 的 DataFeed/DataProvider 链路完成。

### 3.1 预置 JsonInputModule

静态输入不需要写 DLL。控制面可以接入内置 `JsonInputModule`，在 `ModuleInstance.config` 里传 symbols/resolution 等参数：

```json
{
  "instanceId": "input.us-largecap",
  "kind": "Input",
  "moduleId": "json-input",
  "version": "1.0.0",
  "config": {
    "symbols": ["SPY", "QQQ"],
    "resolution": "Daily",
    "securityType": "Equity",
    "market": "usa"
  }
}
```

控制面 attach 后会把 config 编译到内部 manifest 的 `parameters.config`，Engine 侧 `QuantConnect.Algorithm.Modules.JsonInputModule` 读取该 JSON 并生成 `InputRegistration`。这条路径适合静态或参数化 input；需要动态逻辑时仍然可以添加自定义 `Input` 模块实现。

支持的 config 形态：

```json
{
  "symbols": ["SPY", "QQQ"],
  "resolution": "Daily",
  "securityType": "Equity",
  "market": "usa",
  "fillForward": true,
  "leverage": 0,
  "extendedMarketHours": false
}
```

也可以逐项覆盖：

```json
{
  "symbols": [
    "SPY",
    {
      "ticker": "BTCUSD",
      "securityType": "Crypto",
      "market": "coinbase",
      "resolution": "Minute"
    }
  ],
  "resolution": "Daily"
}
```

```csharp
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Modules;

public sealed class MyInputModule : IInputModule
{
    public IEnumerable<InputRegistration> CreateInputs(QCAlgorithm algorithm)
    {
        return new[]
        {
            InputRegistration.Equity("SPY", Resolution.Minute),
            InputRegistration.Equity("QQQ", Resolution.Daily)
        };
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.Input`。
- `CreateInputs` 不能返回 null symbol。
- 热更新移除旧 symbol 时，如果账户还有持仓或挂单，Engine 会拒绝移除该 symbol。

## 4. Universe

Universe 模块负责 Lean universe selection。它必须同时是 Lean 的 universe model 和 Engine module。

```csharp
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;

public sealed class MyUniverseModule : ManualUniverseSelectionModel, IModule
{
    public MyUniverseModule()
        : base(new[] { Symbol.Create("SPY", SecurityType.Equity, Market.USA) })
    {
    }

    public override IEnumerable<Universe> CreateUniverses(QCAlgorithm algorithm)
    {
        return base.CreateUniverses(algorithm);
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.Universe`。
- 当前没有独立 `TransportUniverseModule`；devkit 如果要把 Python universe 接入 Engine，应生成 DLL。
- 仅继承 `ManualUniverseSelectionModel` 不够，还必须实现 `IModule`，否则 `InProcessPlugin` loader 会拒绝加载。

## 5. Signal

Signal 模块产出 Lean `Insight`。

```csharp
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;

public sealed class MySignalModule : IAlphaModel, IModule
{
    public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {
        var symbol = Symbol.Create("SPY", SecurityType.Equity, Market.USA);
        yield return Insight.Price(symbol, TimeSpan.FromDays(1), InsightDirection.Up);
    }

    public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
    {
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.Signal`。
- 多个 signal 会按 manifest 顺序绑定，第一个 `SetAlpha`，后续 `AddAlpha`。

## 6. Target

Target 模块把 insights 转成 portfolio targets。

单 target 模块实现：

```csharp
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;

public sealed class MyTargetModule : IPortfolioConstructionModel, IModule
{
    public IEnumerable<IPortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
    {
        var symbol = Symbol.Create("SPY", SecurityType.Equity, Market.USA);
        yield return new PortfolioTarget(symbol, 100);
    }

    public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
    {
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

多个 target 模块同时启用时，当前 binder 要求每个 target 实现 `IPortfolioIntentModel`，Engine 会用 `CompositePortfolioConstructionModule` 合并 intents：

```csharp
public sealed class MyIntentTargetModule : IPortfolioIntentModel, IModule
{
    public IEnumerable<PortfolioTargetIntent> CreateIntents(QCAlgorithm algorithm, Insight[] insights)
    {
        // return intent objects consumed by the merger
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.Target`。
- 只有一个 target 时，可以直接实现 `IPortfolioConstructionModel`。
- 多个 target 时，不要只实现 `IPortfolioConstructionModel`，否则绑定时会失败。

## 7. Constraint

Constraint 模块对应 Lean risk management。

```csharp
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;

public sealed class MyConstraintModule : IRiskManagementModel, IModule
{
    public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        return targets;
    }

    public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
    {
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.Constraint`。
- 常用 `hotSwapMode` 是 `RequiresPause`，因为 risk 逻辑可能依赖当前 portfolio state。

## 8. Execution

Execution 模块负责执行 portfolio targets。

```csharp
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;
using QuantConnect.Orders;

public sealed class MyExecutionModule : IExecutionModel, IModule
{
    public void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        foreach (var target in targets)
        {
            algorithm.MarketOrder(target.Symbol, target.Quantity);
        }
    }

    public void OnOrderEvent(QCAlgorithm algorithm, OrderEvent orderEvent)
    {
    }

    public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
    {
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.Execution`。
- 当前 Engine 不支持多个 execution 模块同时绑定。
- 常用 `hotSwapMode` 是 `RequiresPause`。

## 9. MarketRule

MarketRule 模块对应 Lean brokerage model，用来控制订单可提交性、杠杆、费用、滑点等。

```csharp
using QuantConnect.Brokerages;
using QuantConnect.Modules;
using QuantConnect.Securities;

public sealed class MyMarketRuleModule : DefaultBrokerageModel, IModule
{
    public override decimal GetLeverage(Security security)
    {
        return 2m;
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.MarketRule`。
- 推荐继承 `DefaultBrokerageModel`，只 override 需要改的规则；直接实现 `IBrokerageModel` 会有大量方法要实现。
- 常用 `hotSwapMode` 是 `RequiresFlatNoOrders`，避免在已有持仓或挂单时切换交易规则。

## 10. Analyzer

Analyzer 模块消费 observation，输出分析结果。

```csharp
using QuantConnect.Modules;

public sealed class MyAnalyzerModule : IAnalyzerModule
{
    public IReadOnlyCollection<ObservationKey> RequiredObservations { get; } =
        Array.Empty<ObservationKey>();

    public AnalyzerResult Analyze(IReadOnlyDictionary<ObservationKey, object> observations)
    {
        return new AnalyzerResult(new Dictionary<string, object>
        {
            ["name"] = "my-analyzer"
        });
    }

    // IModule lifecycle omitted; use ModuleState.
}
```

要求：

- `Kind` 应为 `ModuleKind.Analyzer`。
- `IAnalyzerModule` 已包含 `IModule` 和 `IObservationConsumer`。
- Analyzer 当前在 runtime 中加载，具体调用由 observation/analyzer 调用侧决定。

## 11. RemoteService / ScriptRunner / OutOfProcessWorker

远程服务和脚本模块不要求业务代码直接实现上面的 C# 接口。Engine 会按 `kind` 创建 transport wrapper：

| activationMode | 需要的 manifest 参数 | 说明 |
| --- | --- | --- |
| `RemoteService` | `baseUrl` | Engine 通过 HTTP 调用远程服务 |
| `ScriptRunner` | `command`，可选 `arguments` / `workingDirectory` | Engine 每次通过 JSON line 进程协议调用脚本 |
| `OutOfProcessWorker` | `command`，可选 `arguments` / `workingDirectory` | Engine 通过 JSON line worker 进程调用 |

当前 transport wrapper 支持：

```text
Input, Signal, Target, Constraint, Execution, MarketRule, Analyzer
```

当前不支持：

```text
Universe
```

远程或脚本代码需要实现 transport action，而不是 C# interface：

| kind | 主要 action |
| --- | --- |
| `Input` | `register_inputs` |
| `Signal` | `update_signal` |
| `Target` | `create_targets` |
| `Constraint` | `manage_risk` |
| `Execution` | `execute_targets` |
| `MarketRule` | `can_submit_order`, `can_execute_order`, `describe_market_rule` |
| `Analyzer` | `analyze` |

所有 transport 模块还需要支持 lifecycle action：

```text
initialize, pause, resume, snapshot, restore, health
```

devkit 的目的就是把这些 transport action 包装起来，让策略开发者只写干净的 `strategy.py`。

## 12. 失败行为

常见失败点：

- `assemblyPath` 文件不存在：加载失败。
- `entryPoint` 类型不存在：加载失败。
- 类型没有实现 `IModule`：加载失败。
- 类型没有 public 无参构造函数：实例化失败。
- 类型实现了 `IModule`，但没有实现 stage 需要的 Lean 接口：pipeline 绑定失败。
- 接口方法里抛异常：热更新或运行时调用失败。
- `hotSwapMode` 是 `RequiresRestart`：当前热更新服务不会应用该 manifest。
- `hotSwapMode` 是 `RequiresFlatNoOrders`，但账户有持仓或挂单：热更新服务会等待，不应用该 manifest。

热更新失败时，Engine 会记录错误并保留当前 active pipeline；提交 API 不应该把开发机路径、远程 URL 或临时构建路径写进最终 Engine manifest。
