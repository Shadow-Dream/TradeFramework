# Engine 常驻时的模块开发与接入手册

这份手册只描述一种工作方式：Engine 已经在运行，策略和模块的开发、发布、接入都通过控制 API 完成。开发者不直接编辑 Engine config，不手写 live manifest，也不通过环境变量切换策略。更新策略或替换模块时，控制 API 会把“模块仓库 + 实例 config + 接入关系”编译成 Engine 能读取的 manifest，Engine 负责热加载；只有模块声明自己的 `hotSwapMode` 需要暂停、清仓或重启时，运行时才需要进入对应流程。

## 1. 启动控制 API

这一步在 Engine 所在机器做一次。`liveManifestPath` 必须等于 Engine 当前配置里的 `pipeline-manifest` 路径；后续这个文件只由控制 API 写。

```bash
cd /data/data_jyz/trade
mkdir -p /tmp/lean-control
cat >/tmp/lean-control/strategy-control.json <<'JSON'
{
  "liveManifestPath": "/tmp/my_pipeline/pipeline.json",
  "releaseRoot": "/tmp/lean-strategy-releases",
  "controlRoot": "/tmp/lean-control/state"
}
JSON

python3 strategy_submit_api.py \
  --config /tmp/lean-control/strategy-control.json \
  --host 0.0.0.0 \
  --port 8777
```

另开一个终端确认控制面可用：

```bash
cd /data/data_jyz/trade
python3 strategy_submit.py --api http://127.0.0.1:8777 list-modules
python3 strategy_submit.py --api http://127.0.0.1:8777 current
```

`list-modules` 应该能直接看到 Lean 预置模块。它们不需要 `add-module`。下表不是占位清单，而是当前控制面默认可实例化的 Lean 预置模块：

| 类型 | 模块 | 用途 |
| --- | --- | --- |
| `Input` | `json-input@builtin` | 用实例 config 注册 symbol 输入 |
| `Universe` | `null-universe@builtin` | 空 Universe 占位 |
| `Universe` | `qc500-universe@builtin` | Lean QC500 基本面 Universe |
| `Universe` | `ema-cross-universe@builtin` | Lean EMA Cross 基本面 Universe |
| `Signal` | `null-signal@builtin` | 空 Alpha/Signal 占位 |
| `Signal` | `ema-cross-alpha@builtin` | Lean EMA Cross Alpha |
| `Signal` | `historical-returns-alpha@builtin` | Lean Historical Returns Alpha |
| `Signal` | `macd-alpha@builtin` | Lean MACD Alpha |
| `Signal` | `rsi-alpha@builtin` | Lean RSI Alpha |
| `Signal` | `pearson-correlation-pairs-alpha@builtin` | Lean Pearson pairs trading Alpha |
| `Target` | `null-target@builtin` | 空 Portfolio Target 占位 |
| `Target` | `equal-weighting-target@builtin` | Lean Equal Weighting Portfolio |
| `Target` | `insight-weighting-target@builtin` | Lean Insight Weighting Portfolio |
| `Target` | `confidence-weighted-target@builtin` | Lean Confidence Weighted Portfolio |
| `Target` | `accumulative-insight-target@builtin` | Lean Accumulative Insight Portfolio |
| `Target` | `mean-variance-optimization-target@builtin` | Lean Mean Variance Optimizer Portfolio |
| `Target` | `black-litterman-optimization-target@builtin` | Lean Black-Litterman Optimizer Portfolio |
| `Target` | `risk-parity-target@builtin` | Lean Risk Parity Portfolio |
| `Target` | `mean-reversion-target@builtin` | Lean Mean Reversion Portfolio |
| `Target` | `sector-weighting-target@builtin` | Lean Sector Weighting Portfolio |
| `Constraint` | `null-risk@builtin` | 空 Risk/Constraint 占位 |
| `Constraint` | `trailing-stop-risk@builtin` | Lean Trailing Stop Risk |
| `Constraint` | `maximum-sector-exposure-risk@builtin` | Lean Maximum Sector Exposure Risk |
| `Execution` | `null-execution@builtin` | 不下单的 Execution 占位 |
| `Execution` | `immediate-execution@builtin` | 立即执行 target |
| `Execution` | `spread-execution@builtin` | Lean Spread Execution |
| `Execution` | `standard-deviation-execution@builtin` | Lean Standard Deviation Execution |
| `Execution` | `vwap-execution@builtin` | Lean VWAP Execution |
| `MarketRule` | `default-market@builtin` | Lean 默认 Brokerage/MarketRule |

这些 Lean Framework 预置模块的实例 config 会按构造函数参数名绑定。比如 `ema-cross-alpha@builtin` 可以传 `fastPeriod`、`slowPeriod`、`resolution`；`trailing-stop-risk@builtin` 可以传 `maximumDrawdownPercent`。未传的参数使用 Lean 默认值。

`ManualUniverseSelectionModel` 不是默认预置模块：它本身可以实例化，但当前没有从实例 config 注入 symbols 的干净入口，直接放进模块库会让用户以为它是可配置 Universe。需要可配置 Universe 时，上传一个自定义 Universe 模块或后续补一个专门的 JSON Universe wrapper。

## 2. 用预置模块接入一个运行中的策略骨架

先创建一个可调参数 Input 实例。这里创建的是实例，不是模块实现；同一个 `json-input@builtin` 可以创建多个实例，每个实例有自己的 config。

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  create-instance \
  --instance-id input.us-largecap \
  --kind Input \
  --module-id json-input \
  --version builtin \
  --config-json '{
    "symbols": ["SPY", "QQQ", "IWM"],
    "resolution": "Daily",
    "securityType": "Equity",
    "market": "usa",
    "fillForward": true
  }'
```

再创建其他运行槽位的预置实例：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 create-instance \
  --instance-id universe.none --kind Universe --module-id null-universe --version builtin

python3 strategy_submit.py --api http://127.0.0.1:8777 create-instance \
  --instance-id signal.none --kind Signal --module-id null-signal --version builtin

python3 strategy_submit.py --api http://127.0.0.1:8777 create-instance \
  --instance-id signal.ema.fast \
  --kind Signal \
  --module-id ema-cross-alpha \
  --version builtin \
  --config-json '{"fastPeriod": 8, "slowPeriod": 21, "resolution": "Daily"}'

python3 strategy_submit.py --api http://127.0.0.1:8777 create-instance \
  --instance-id target.none --kind Target --module-id null-target --version builtin

python3 strategy_submit.py --api http://127.0.0.1:8777 create-instance \
  --instance-id risk.none --kind Constraint --module-id null-risk --version builtin

python3 strategy_submit.py --api http://127.0.0.1:8777 create-instance \
  --instance-id execution.immediate --kind Execution --module-id immediate-execution --version builtin

python3 strategy_submit.py --api http://127.0.0.1:8777 create-instance \
  --instance-id market.default --kind MarketRule --module-id default-market --version builtin
```

最后把这些实例接到正在运行的 pipeline。只有这一步会改 live manifest，并让 Engine 看到新接入关系。

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  attach \
  --strategy-id preset-skeleton \
  --version 20260523-001 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.none"],
    "target": ["target.none"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --market-rule market.default
```

这里仍然接入 `signal.none`，因为它是最小可运行骨架。要切到 EMA Alpha，只需要把 attach 里的 `"signal": ["signal.none"]` 改成 `"signal": ["signal.ema.fast"]`，不需要重启 Engine。

检查当前 Engine manifest：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 current
```

`input.us-largecap` 的 config 会被控制 API 编译到该模块的 `parameters.config` 中，Engine 侧由 `JsonInputModule.Initialize(configuration)` 读取。开发者不需要为简单 symbol 输入手写模块；当输入来源需要复杂逻辑时，再写自定义 `Input` 模块。

## 3. 上传自定义模块到模块库

模块开发分三步：把实现加入仓库、创建带 config 的实例、把实例接入 pipeline。`add-module` 只是入库，不启用；`create-instance` 只是保存 config，不启用；`attach` 才影响 Engine。

### 3.1 DLL 模块

本地 DLL 先上传到 Engine 机器的模块仓库目录，再用 `{{moduleRoot}}` 引用它。开发机器不需要和 Engine 共享路径。

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  add-module \
  --kind Signal \
  --module-id momentum-signal \
  --version 20260523-001 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Signals.MomentumSignalModule \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath": "{{moduleRoot}}/artifacts/MySignals.dll"}' \
  --file /tmp/build/MySignals.dll:artifacts/MySignals.dll
```

远程 artifact 用 `--remote-file`，控制 API 会下载并校验到模块仓库：

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  add-module \
  --kind Target \
  --module-id equal-weight-target \
  --version 20260523-001 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Targets.EqualWeightTargetModule \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath": "{{moduleRoot}}/artifacts/MyTargets.dll"}' \
  --remote-file 'https://example.com/releases/MyTargets.dll,artifacts/MyTargets.dll,sha256=<hex>'
```

创建实例时传入业务参数：

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  create-instance \
  --instance-id signal.momentum.fast \
  --kind Signal \
  --module-id momentum-signal \
  --version 20260523-001 \
  --config-json '{"lookback": 20, "threshold": 0.03}'

python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  create-instance \
  --instance-id target.equal.weight \
  --kind Target \
  --module-id equal-weight-target \
  --version 20260523-001 \
  --config-json '{"maxPositionPercent": 0.2}'
```

### 3.2 RPC 模块

远程服务模块只在模块库记录服务地址，策略逻辑可以运行在其他机器或云端。

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  add-module \
  --kind Constraint \
  --module-id remote-risk \
  --version 20260523-001 \
  --activation-mode RemoteService \
  --entry-point RemoteRiskService \
  --hot-swap-mode Live \
  --parameters-json '{"baseUrl": "http://10.0.0.8:9100"}'

python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  create-instance \
  --instance-id risk.remote.maxdd \
  --kind Constraint \
  --module-id remote-risk \
  --version 20260523-001 \
  --config-json '{"maxDrawdown": 0.08, "grossExposureLimit": 1.2}'
```

### 3.3 脚本或进程模块

脚本模块把可执行文件上传到模块仓库，`command` 使用 `{{moduleRoot}}` 指向 Engine 机器上的落盘位置。

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  add-module \
  --kind Analyzer \
  --module-id pnl-analyzer \
  --version 20260523-001 \
  --activation-mode ScriptRunner \
  --entry-point PnlAnalyzer \
  --hot-swap-mode Live \
  --parameters-json '{"command": "python3 {{moduleRoot}}/analyzer.py"}' \
  --file ./analyzer.py:analyzer.py

python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  create-instance \
  --instance-id analyzer.pnl \
  --kind Analyzer \
  --module-id pnl-analyzer \
  --version 20260523-001 \
  --config-json '{"window": 252}'
```

## 4. 接入完整自定义 pipeline

所有组件实例创建好之后，一次 attach 只描述“哪些实例接到哪些槽位”。下面示例覆盖 Engine 当前支持的主要可插拔组件类型：

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  attach \
  --strategy-id live-momentum \
  --version 20260523-002 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.momentum.fast"],
    "target": ["target.equal.weight"],
    "constraint": ["risk.remote.maxdd"],
    "execution": ["execution.immediate"],
    "analyzer": ["analyzer.pnl"]
  }' \
  --market-rule market.default
```

如果只改某个实例的参数，重新 `create-instance` 同一个 `instance-id`，再 `attach` 同一组接入关系即可。控制 API 会重新编译 manifest；Engine 是否需要暂停由涉及模块的 `hotSwapMode` 决定。纯 `Live` 模块不应该要求重启 Engine。

## 5. 细粒度 Market 组件

当前 Engine 已能接入整体 `MarketRule`，预置的 `default-market@builtin` 是 Lean 默认 BrokerageModel 的无参 wrapper。新的控制面已经把更细的 market 子组件作为模块库类型保留下来：`FeeModel`、`SlippageModel`、`FillModel`、`BuyingPowerModel`、`SettlementModel`、`LeverageRule`、`ShortableProvider`、`BenchmarkProvider` 等。

添加一个远程 fee 模块：

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  add-module \
  --kind FeeModel \
  --module-id tiered-fee \
  --version 20260523-001 \
  --activation-mode RemoteService \
  --parameters-json '{"baseUrl": "http://10.0.0.9:9200"}'

python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  create-instance \
  --instance-id fee.us-equity \
  --kind FeeModel \
  --module-id tiered-fee \
  --version 20260523-001 \
  --config-json '{
    "currency": "USD",
    "tiers": [
      {"notional": 100000, "rate": 0.001},
      {"notional": 1000000, "rate": 0.0005}
    ]
  }'
```

接入时把它放进 `market` 槽位：

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  attach \
  --strategy-id live-momentum \
  --version 20260523-003 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.momentum.fast"],
    "target": ["target.equal.weight"],
    "constraint": ["risk.remote.maxdd"],
    "execution": ["execution.immediate"],
    "analyzer": ["analyzer.pnl"]
  }' \
  --market-rule market.default \
  --market-json '{"feeModel": "fee.us-equity"}'
```

注意：这些细粒度 market 子组件目前是控制面可登记、可实例化、可接入的槽位，但 Engine 运行时仍需要后续 `CompositeMarketModel` adapter 才能真正把它们替换到 Lean 的 `Security` 模型里。现在保留这些槽位的目的，是让 API 和模块仓库模型先按正确粒度稳定下来，避免继续把简单 fee/slippage 变化绑定成“替换整个 MarketRule 服务”。

## 6. 解绑和删除

解绑只修改当前接入关系，不删除模块库里的实现，也不删除实例 config：

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  detach \
  --instances-json '["signal.momentum.fast"]'
```

也可以按 stage 或 market 槽位解绑：

```bash
python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  detach \
  --stages-json '["analyzer"]'

python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  detach \
  --market-json '["feeModel"]'
```

删除模块实现前，先确认没有实例引用它：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 list-instances

python3 strategy_submit.py \
  --api http://127.0.0.1:8777 \
  delete-module \
  --kind Signal \
  --module-id momentum-signal \
  --version 20260523-001
```

内置预置模块不能删除，也不能用同一个 `kind/moduleId/version` 覆盖。

## 7. 开发时是否需要重启 Engine

日常策略开发不应该重启 Engine。按下面规则判断：

| 操作 | 是否影响运行中 Engine |
| --- | --- |
| `list-modules` / `list-instances` / `current` | 不影响 |
| `add-module` | 不影响，只入库 |
| `create-instance` | 不影响，只保存 config |
| `attach` | 影响，触发 Engine 热加载 |
| `detach` | 影响，触发 Engine 热加载 |
| 模块 `hotSwapMode=Live` | 不需要暂停或重启 |
| 模块 `hotSwapMode=RequiresPause` | Engine 应暂停对应 pipeline 后替换 |
| 模块 `hotSwapMode=RequiresFlatNoOrders` | 需要无持仓/无挂单后替换 |
| 模块 `hotSwapMode=RequiresRestart` | 才需要重启 Engine |

因此，新的开发策略是：模块实现先入库，实例参数随时创建或覆盖，运行接入只通过 `attach` 提交。Engine config 和环境变量只用于启动 Engine 与控制 API，不作为策略开发接口。
