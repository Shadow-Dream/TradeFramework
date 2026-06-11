# Engine 常驻时的模块开发与接入手册

这份手册只描述一种工作方式：Engine 已经在运行，策略开发者通过控制 API 添加模块、配置实例、接入运行中的 pipeline lane。开发过程中不要直接编辑 Engine config，不要手写 live manifest，也不要用环境变量切换策略。

当前设计把操作拆成三类：

| 操作 | 命令 | 作用 | 是否影响正在运行的 Engine |
| --- | --- | --- | --- |
| 模块添加 | `add-module` | 把某个模块实现加入对应类型的模块库 | 不影响 |
| 运行接入 | `attach --lane-id <lane>` / `detach --lane-id <lane>` | 选择模块模板、填写实例参数和输入输出 data key，并接入或移出指定 pipeline lane | 影响该 lane，触发热加载 |
| 结果研究 | Web Results / `visualizations` API | 对已有 data key 配 visualizer，或临时实例化因子/Analyzer 做纯可视化研究 | 不影响 Engine 和 Pipeline |

模块库里保存的是模块模板，不是可运行模块。模块只有在某条 Pipeline lane 里被实例化并接入后才运行；不存在“先实例化但不进入 Pipeline”的开发流程。Pipeline 中的模块即使没有被后续模块消费，也可以合法空跑并产出 data key，用于调试和指标观察。

Results 里的可视化和 Pipeline 接入是两件事：Pipeline 回测过程中出现过的 data key 必须可以直接画图，此时只填写 visualizer 参数；如果在 Results 里临时添加因子或 Analyzer，则需要同时填写临时模块参数和 visualizer 参数，但它不会修改 Pipeline。

`attach` 是一次完整的 lane 接入提交，不是增量 patch。手工提交时，`--instances-file` 应包含本次 `stages`、`marketRule`、`market` 和 `alphaGraph.nodes` 引用到的全部 lane-local 实例；Web Pipeline 和 devkit publish 会自动生成这份完整 payload。

控制 API 会把“模块库 + lane 内联实例 config + 每条 lane 的接入关系”编译成 Engine 内部 manifest。Engine 读取 `pipeline-lanes-manifest`，在一个 Engine 进程里把多条 active lane 组合成一个 Lean Framework runtime；策略开发者的稳定入口是控制 API。

## 1. 启动控制 API

在 Engine 所在机器执行一次。`liveManifestPath` 是兼容旧单主线的默认 manifest；`lanesManifestPath` 是 Engine 当前读取的 `pipeline-lanes-manifest` 文件。后续这些文件只由控制 API 写。

```bash
cd /data/data_jyz/trade
mkdir -p /data/data_jyz/trade/.runtime/live
mkdir -p /data/data_jyz/trade/.runtime/releases
mkdir -p /data/data_jyz/trade/.runtime/control

cat >/data/data_jyz/trade/.runtime/strategy-control.json <<'JSON'
{
  "liveManifestPath": "/data/data_jyz/trade/.runtime/live/pipeline.json",
  "lanesManifestPath": "/data/data_jyz/trade/.runtime/live/lanes-manifest.json",
  "releaseRoot": "/data/data_jyz/trade/.runtime/releases",
  "controlRoot": "/data/data_jyz/trade/.runtime/control"
}
JSON

python3 strategy_submit_api.py \
  --config /data/data_jyz/trade/.runtime/strategy-control.json \
  --host 0.0.0.0 \
  --port 8777
```

另开一个终端检查控制面：

```bash
cd /data/data_jyz/trade
python3 strategy_submit.py --api http://127.0.0.1:8777 list-modules
python3 strategy_submit.py --api http://127.0.0.1:8777 list-instances
python3 strategy_submit.py --api http://127.0.0.1:8777 list-lanes
python3 strategy_submit.py --api http://127.0.0.1:8777 history --limit 20
python3 strategy_submit.py --api http://127.0.0.1:8777 current --lane-id main
```

不要把 `releaseRoot` 或 `controlRoot` 放在 `/tmp`。如果重启控制 API 后 `list-modules` 只剩 Lean 预置模块，通常就是启动时换了 `controlRoot`，或者原来的 `controlRoot` 被清掉了。正确状态应该是：Lean 预置模块加上之前通过 `add-module` 入库的所有自定义模块。

Engine 启动配置里使用 lane registry，而不是直接指向某一条策略：

```json
{
  "pipeline-lanes-manifest": "/data/data_jyz/trade/.runtime/live/lanes-manifest.json",
  "pipeline-hot-reload-interval-ms": 1000
}
```

如果还在用旧的 `pipeline-manifest`，Engine 只能读取一条主线；要单 Engine 多 pipeline，必须切到 `pipeline-lanes-manifest`。

## 2. 持久化目录

先确认控制面使用的是同一套持久化目录：

```bash
cat /data/data_jyz/trade/.runtime/strategy-control.json
ls -R /data/data_jyz/trade/.runtime/control
ls -R /data/data_jyz/trade/.runtime/releases | head -n 80
```

控制 API 会持久化这些内容：

| 路径 | 内容 |
| --- | --- |
| `controlRoot/modules.json` | 当前模块库里的自定义模块定义 |
| `controlRoot/instances.json` | 兼容旧 API 的历史全局实例；新的 Web Pipeline 流程不再依赖“未接入实例” |
| `controlRoot/lanes.json` | 所有 active lane 的当前 manifest、策略版本和状态 |
| `controlRoot/lanes/<laneId>/attachment.json` | 指定 lane 当前正在运行的接入关系，以及该 lane 内联实例的 config、inputs、outputs |
| `controlRoot/attachment.json` | 兼容旧版 `main` lane 的接入关系 |
| `controlRoot/iterations.json` | 每次 `attach`、`detach`、legacy `submit` 的迭代记录 |
| `controlRoot/events.jsonl` | 模块添加、实例保存、接入、解绑、删除等事件日志 |
| `controlRoot/artifacts.json` | 数据版本、snapshot、checkpoint、结果、报告等运行产物索引 |
| `controlRoot/deleted-modules.json` | 逻辑删除记录 |
| `releaseRoot/_modules/<kind>/<moduleId>/<version>/` | 上传过的 DLL、脚本、远程 artifact 下载结果和 `module.json` |
| `releaseRoot/_attachments/<laneId>/<strategyId>/<version>/` | 每次接入生成的 `pipeline.json`、`attachment.json`、`control-snapshot.json` |
| `releaseRoot/_artifacts/<kind>/<artifactId>/` | 数据、snapshot、checkpoint、结果文件和 `artifact.json` |
| `.runtime/live/lanes/<laneId>/pipeline.json` | 指定 lane 的 live manifest |
| `.runtime/live/lanes-manifest.json` | Engine 读取的多 lane registry |

重启控制 API 后执行：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 list-modules
python3 strategy_submit.py --api http://127.0.0.1:8777 list-instances
python3 strategy_submit.py --api http://127.0.0.1:8777 list-lanes
python3 strategy_submit.py --api http://127.0.0.1:8777 list-artifacts
python3 strategy_submit.py --api http://127.0.0.1:8777 history --limit 20
python3 strategy_submit.py --api http://127.0.0.1:8777 current --lane-id main
```

`list-modules` 应该包含 Lean 预置模块和历史自定义模块；`list-instances` 应该包含历史实例 config；`list-lanes` 应该包含之前接入过的 active lane；`list-artifacts` 应该包含历史数据和运行产物；`history` 应该能看到历史迭代；`current --lane-id <lane>` 应该读取指定 lane 的 live manifest。

## 3. 确认可用的预置模块

先列出模块库：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 list-modules
```

当前控制面会自动提供这些 Lean 预置模块，不需要 `add-module`：

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
| `Signal` | `price-source@builtin` | Alpha 图里的价格/成交量 data key 源 |
| `Signal` | `sma-indicator@builtin` | SMA 因子模板，实例化时填写 `period`、输入 key、输出 key |
| `Signal` | `ema-indicator@builtin` | EMA 因子模板，实例化时填写 `period`、输入 key、输出 key |
| `Signal` | `wma-indicator@builtin` | WMA 因子模板 |
| `Signal` | `vwma-indicator@builtin` | VWMA 因子模板 |
| `Signal` | `rsi-indicator@builtin` | RSI 因子模板 |
| `Signal` | `roc-indicator@builtin` | ROC 因子模板 |
| `Signal` | `macd-indicator@builtin` | MACD 因子模板 |
| `Signal` | `bollinger-bands-indicator@builtin` | Bollinger Bands 因子模板 |
| `Signal` | `atr-indicator@builtin` | ATR 因子模板 |
| `Signal` | `stochastic-indicator@builtin` | Stochastic 因子模板 |
| `Signal` | `obv-indicator@builtin` | OBV 因子模板 |
| `Signal` | `cross-over-gate@builtin` | 数值交叉门控节点 |
| `Signal` | `direction-to-price-insight@builtin` | 把方向 signal 转成 Lean Insight |
| `FeeModel` | `constant-fee@builtin`、`interactive-brokers-fee@builtin`、`alpaca-fee@builtin`、`coinbase-fee@builtin`、`binance-fee@builtin` | 细粒度 market fee 模板 |
| `SlippageModel` | `null-slippage@builtin`、`constant-slippage@builtin`、`volume-share-slippage@builtin`、`market-impact-slippage@builtin` | 细粒度 slippage 模板 |
| `FillModel` | `immediate-fill@builtin`、`equity-fill@builtin`、`latest-price-fill@builtin`、`future-fill@builtin` | 细粒度 fill 模板 |
| `BuyingPowerModel` | `security-margin-buying-power@builtin`、`cash-buying-power@builtin`、`constant-buying-power@builtin`、`null-buying-power@builtin` | 细粒度 buying power 模板 |
| `SettlementModel` | `immediate-settlement@builtin`、`delayed-settlement@builtin`、`account-currency-immediate-settlement@builtin`、`future-settlement@builtin` | 细粒度 settlement 模板 |

`DataSource` 不是策略 pipeline stage。它属于 Engine 底层数据设施，例如数据文件 provider、历史数据 provider、map/factor provider。策略要登记自己需要哪些行情输入时，使用 `Input`；真正的数据读取仍由 Lean DataFeed/DataProvider 完成。

## 4. 接入一个最小可运行 pipeline

先写这次接入要实例化的模块。模块库里只有模板，下面这些对象只有随 `attach` 提交后才会进入 `main` lane：

```bash
cat >/tmp/live-skeleton.instances.json <<'JSON'
{
  "input.us-largecap": {
    "instanceId": "input.us-largecap",
    "kind": "Input",
    "moduleId": "json-input",
    "version": "builtin",
    "config": {
      "symbols": ["SPY", "QQQ", "IWM"],
      "resolution": "Daily",
      "securityType": "Equity",
      "market": "usa",
      "fillForward": true
    }
  },
  "universe.none": {
    "instanceId": "universe.none",
    "kind": "Universe",
    "moduleId": "null-universe",
    "version": "builtin"
  },
  "signal.none": {
    "instanceId": "signal.none",
    "kind": "Signal",
    "moduleId": "null-signal",
    "version": "builtin"
  },
  "target.none": {
    "instanceId": "target.none",
    "kind": "Target",
    "moduleId": "null-target",
    "version": "builtin"
  },
  "risk.none": {
    "instanceId": "risk.none",
    "kind": "Constraint",
    "moduleId": "null-risk",
    "version": "builtin"
  },
  "execution.immediate": {
    "instanceId": "execution.immediate",
    "kind": "Execution",
    "moduleId": "immediate-execution",
    "version": "builtin"
  },
  "market.default": {
    "instanceId": "market.default",
    "kind": "MarketRule",
    "moduleId": "default-market",
    "version": "builtin"
  }
}
JSON
```

把这些实例一次性接入运行中的 Engine：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id live-skeleton \
  --version 20260524-001 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.none"],
    "target": ["target.none"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/live-skeleton.instances.json \
  --market-rule market.default
```

检查 Engine 当前看到的 manifest：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 current --lane-id main
```

## 5. 开发并上传 DLL 模块

推荐先用 devkit 创建策略侧模块工程。它会生成 Signal/Target 模板、payload、README 和配套单元测试项目：

```bash
cd /data/data_jyz/trade
python3 -m strategy_devkit.scaffold new \
  --name momentum \
  --out /tmp/momentum-module \
  --strategy-id momentum \
  --version 20260524-001

cd /tmp/momentum-module
/root/.dotnet/dotnet test tests/Momentum.Modules.Tests.csproj \
  -c Release \
  --nologo \
  --logger "console;verbosity=minimal"
```

每个脚手架模块都自带 `tests/ModuleSmokeTests.cs`。这个测试不启动 Engine，也不重编 Lean；它只验证模块生命周期和 stage 接口可以被单独调用。实现策略逻辑时，先在这个测试项目里补历史数据 replay、输入样本和预期 signal/target，再提交到控制 API。

编辑生成的文件：

- `/tmp/momentum-module/src/MomentumSignalModule.cs`：写 signal 逻辑。
- `/tmp/momentum-module/src/MomentumTargetModule.cs`：写 target sizing。
- `/tmp/momentum-module/payloads/input.instance.json`：写输入标的和周期。
- `/tmp/momentum-module/payloads/signal.config.json`：写 signal 参数。
- `/tmp/momentum-module/payloads/package.json`：登记这个 DLL 包里暴露的模块入口。
- `/tmp/momentum-module/tests/fixtures/replay.json`：写本地 bar replay 样本。
- `/tmp/momentum-module/tests/ModuleSmokeTests.cs`：补单元测试、replay 断言和预期 signal/target。

如果修改了 `src/*Config` 类，先刷新 schema：

```bash
python3 -m strategy_devkit.schema --root /tmp/momentum-module --write
```

需要给前端暴露说明、范围或分组时，在 config 属性上加 `ConfigField`：

```csharp
[ConfigField("Insight holding period in calendar days.", Minimum = 1, Maximum = 365, Group = "Signal")]
public int InsightDays { get; set; } = 35;
```

`schema --write` 会把这些元数据写入 `payloads/*.schema.json` 和 `payloads/package.json`。

如果发布时提示版本已存在，用 devkit 同步 bump 所有相关文件：

```bash
python3 -m strategy_devkit.version bump \
  --root /tmp/momentum-module \
  --version 20260524-002
```

提交时不要手动并发跑一串 `create-instance`。直接执行：

```bash
cd /data/data_jyz/trade
python3 -m strategy_devkit.publish \
  --root /tmp/momentum-module \
  --api http://127.0.0.1:8777
```

`publish` 会按顺序执行：

```bash
/root/.dotnet/dotnet build /tmp/momentum-module/Momentum.Modules.csproj -c Release --nologo
/root/.dotnet/dotnet test /tmp/momentum-module/tests/Momentum.Modules.Tests.csproj -c Release --nologo --logger "console;verbosity=minimal"
python3 -m strategy_devkit.publish --root /tmp/momentum-module --api http://127.0.0.1:8777
python3 strategy_submit.py --api http://127.0.0.1:8777 record-artifact ...
```

实际实现里 `publish` 会把 DLL 加进 package payload 后调用 `/v1/module-packages`，再把 `payloads/*.instance.json` 合并进一次 inline `attach` 请求。一个 DLL 里同时包含 Signal/Target 时只上传一次，再注册多个 entry point。这个流程只更新控制面的模块库、lane attachment 和 artifact；Engine 不需要重启。

DLL 的详细接口要求写在 `module_contracts.md`。最重要的规则是：不是按固定函数名查找，而是按 `entryPoint` 加载公开 C# 类型；类型必须能作为对应 stage 使用，例如 `Signal` 必须是 alpha model，`Input` 必须实现 `IInputModule.CreateInputs(...)`。

## 6. 开发并上传脚本模块

编辑 `./analyzer.py`：

```python
#!/usr/bin/env python3
import json
import sys

payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
print(json.dumps({"accepted": True, "name": "pnl-analyzer", "payload": payload}))
```

上传到 `Analyzer` 模块库：

```bash
chmod +x ./analyzer.py

python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Analyzer \
  --module-id pnl-analyzer \
  --version 20260524-001 \
  --activation-mode ScriptRunner \
  --entry-point PnlAnalyzer \
  --hot-swap-mode Live \
  --parameters-json '{"command":"python3 {{moduleRoot}}/analyzer.py"}' \
  --file ./analyzer.py:analyzer.py:x
```

把 Analyzer 实例写进本次接入 payload，然后 attach：

```bash
cat >/tmp/live-momentum-analyzer.instances.json <<'JSON'
{
  "analyzer.pnl": {
    "instanceId": "analyzer.pnl",
    "kind": "Analyzer",
    "moduleId": "pnl-analyzer",
    "version": "20260524-001",
    "config": {"window": 252}
  }
}
JSON

python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id live-momentum \
  --version 20260524-003 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.momentum.20"],
    "target": ["target.none"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": ["analyzer.pnl"]
  }' \
  --instances-file /tmp/live-momentum-analyzer.instances.json \
  --market-rule market.default
```

## 7. 接入远程 RPC 模块

在远程机器启动服务，例如 `http://10.0.0.8:9100`。服务按 `module_contracts.md` 里的 transport wrapper 协议实现对应组件命令。RPC 不登记裸地址；登记的是一个带版本指纹的远程后端模块，`baseUrl` 只是调用入口，`backend` 才是复现历史结果用的冻结信息。

把服务登记到模块库：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Constraint \
  --module-id remote-risk \
  --version 20260524-001 \
  --activation-mode RemoteService \
  --entry-point RemoteRiskService \
  --hot-swap-mode Live \
  --remote-url http://10.0.0.8:9100 \
  --remote-contract-hash sha256:1111111111111111111111111111111111111111111111111111111111111111 \
  --remote-deployment-id image-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --remote-manifest-url http://10.0.0.8:9100/.well-known/trade-module.json
```

把远程模块实例写进本次接入 payload：

```bash
cat >/tmp/live-momentum-risk.instances.json <<'JSON'
{
  "risk.remote.maxdd": {
    "instanceId": "risk.remote.maxdd",
    "kind": "Constraint",
    "moduleId": "remote-risk",
    "version": "20260524-001",
    "config": {"maxDrawdown": 0.08, "grossExposureLimit": 1.2}
  }
}
JSON
```

接入：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id live-momentum \
  --version 20260524-004 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.momentum.20"],
    "target": ["target.none"],
    "constraint": ["risk.remote.maxdd"],
    "execution": ["execution.immediate"],
    "analyzer": ["analyzer.pnl"]
  }' \
  --instances-file /tmp/live-momentum-risk.instances.json \
  --market-rule market.default
```

## 8. 用 alpha 图组合多个 Signal 节点

普通 `signal` 数组是并列组合；多个 alpha 都可以独立产出 `Insight`。如果要蓝图式连线，使用 `ports`、实例 `inputs/outputs` 和 `alphaGraph`。

先为一个 typed alpha node 准备端口文件。编辑 `/tmp/alpha-graph/ema-ports.json`：

```bash
mkdir -p /tmp/alpha-graph

cat >/tmp/alpha-graph/ema-ports.json <<'JSON'
{
  "inputs": {},
  "outputs": {
    "ema": { "type": "indicator.ema", "required": true }
  }
}
JSON
```

编辑 `/tmp/alpha-graph/cross-ports.json`：

```bash
cat >/tmp/alpha-graph/cross-ports.json <<'JSON'
{
  "inputs": {
    "fast": { "type": "indicator.ema", "required": true },
    "slow": { "type": "indicator.ema", "required": true },
    "price": { "type": "price", "required": true }
  },
  "outputs": {
    "insights": { "type": "insight.list", "required": true }
  }
}
JSON
```

把节点实现加入 `Signal` 模块库。这里假设三个节点都已经编译在同一个 DLL 里：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Signal \
  --module-id ema-node \
  --version 20260524-001 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Graph.EmaNode \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath":"{{moduleRoot}}/artifacts/AlphaGraphNodes.dll"}' \
  --ports-file /tmp/alpha-graph/ema-ports.json \
  --file /tmp/build/AlphaGraphNodes.dll:artifacts/AlphaGraphNodes.dll

python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Signal \
  --module-id price-node \
  --version 20260524-001 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Graph.PriceNode \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath":"{{moduleRoot}}/artifacts/AlphaGraphNodes.dll"}' \
  --ports-json '{"inputs":{},"outputs":{"price":{"type":"price","required":true}}}' \
  --file /tmp/build/AlphaGraphNodes.dll:artifacts/AlphaGraphNodes.dll

python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Signal \
  --module-id ma-cross-gate \
  --version 20260524-001 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Graph.MaCrossGateNode \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath":"{{moduleRoot}}/artifacts/AlphaGraphNodes.dll"}' \
  --ports-file /tmp/alpha-graph/cross-ports.json \
  --file /tmp/build/AlphaGraphNodes.dll:artifacts/AlphaGraphNodes.dll
```

写出本次接入的完整实例集合，并给每个节点端口分配连线 id：

```bash
cat >/tmp/alpha-graph/instances.json <<'JSON'
{
  "input.us-largecap": {
    "instanceId": "input.us-largecap",
    "kind": "Input",
    "moduleId": "json-input",
    "version": "builtin",
    "config": {"symbols": ["SPY"], "resolution": "Daily", "securityType": "Equity", "market": "usa"}
  },
  "signal.ema20.node": {
    "instanceId": "signal.ema20.node",
    "kind": "Signal",
    "moduleId": "ema-node",
    "version": "20260524-001",
    "config": {"period": 20},
    "outputs": {"ema": "ema_20"}
  },
  "signal.ema50.node": {
    "instanceId": "signal.ema50.node",
    "kind": "Signal",
    "moduleId": "ema-node",
    "version": "20260524-001",
    "config": {"period": 50},
    "outputs": {"ema": "ema_50"}
  },
  "signal.price.node": {
    "instanceId": "signal.price.node",
    "kind": "Signal",
    "moduleId": "price-node",
    "version": "20260524-001",
    "outputs": {"price": "price_1"}
  },
  "signal.cross.node": {
    "instanceId": "signal.cross.node",
    "kind": "Signal",
    "moduleId": "ma-cross-gate",
    "version": "20260524-001",
    "inputs": {"fast": "ema_20", "slow": "ema_50", "price": "price_1"},
    "outputs": {"insights": "rise_1"}
  },
  "universe.none": {"instanceId": "universe.none", "kind": "Universe", "moduleId": "null-universe", "version": "builtin"},
  "target.none": {"instanceId": "target.none", "kind": "Target", "moduleId": "null-target", "version": "builtin"},
  "risk.none": {"instanceId": "risk.none", "kind": "Constraint", "moduleId": "null-risk", "version": "builtin"},
  "execution.immediate": {"instanceId": "execution.immediate", "kind": "Execution", "moduleId": "immediate-execution", "version": "builtin"},
  "market.default": {"instanceId": "market.default", "kind": "MarketRule", "moduleId": "default-market", "version": "builtin"}
}
JSON
```

编辑 `/tmp/alpha-graph/graph.json`：

```bash
cat >/tmp/alpha-graph/graph.json <<'JSON'
{
  "nodes": [
    "signal.ema20.node",
    "signal.ema50.node",
    "signal.price.node",
    "signal.cross.node"
  ],
  "outputs": {
    "insights": ["rise_1"]
  }
}
JSON
```

接入 alpha 图。这里 `signal` 数组留空，因为图本身会被 Engine 编译成一个 alpha：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id alpha-graph-demo \
  --version 20260524-001 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": [],
    "target": ["target.none"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/alpha-graph/instances.json \
  --market-rule market.default \
  --alpha-graph-file /tmp/alpha-graph/graph.json
```

如果节点端口类型不兼容、连线没有 producer、同一根 wire 有多个 producer，或者图里存在环，`attach` 会被控制 API 拒绝。更完整的 alpha 操作说明见 `alpha.md`。

## 9. 接入 MarketRule 和 market 子组件

整体 MarketRule 直接接入 `marketRule`：

```bash
cat >/tmp/live-market.instances.json <<'JSON'
{
  "input.us-largecap": {
    "instanceId": "input.us-largecap",
    "kind": "Input",
    "moduleId": "json-input",
    "version": "builtin",
    "config": {"symbols": ["SPY"], "resolution": "Daily", "securityType": "Equity", "market": "usa"}
  },
  "universe.none": {"instanceId": "universe.none", "kind": "Universe", "moduleId": "null-universe", "version": "builtin"},
  "signal.none": {"instanceId": "signal.none", "kind": "Signal", "moduleId": "null-signal", "version": "builtin"},
  "target.none": {"instanceId": "target.none", "kind": "Target", "moduleId": "null-target", "version": "builtin"},
  "risk.none": {"instanceId": "risk.none", "kind": "Constraint", "moduleId": "null-risk", "version": "builtin"},
  "execution.immediate": {"instanceId": "execution.immediate", "kind": "Execution", "moduleId": "immediate-execution", "version": "builtin"},
  "market.default": {"instanceId": "market.default", "kind": "MarketRule", "moduleId": "default-market", "version": "builtin"}
}
JSON

python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id live-market \
  --version 20260524-001 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.none"],
    "target": ["target.none"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/live-market.instances.json \
  --market-rule market.default
```

细粒度 market 子组件可以先入库、实例化、放进 attachment 的 `market` 字段：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind FeeModel \
  --module-id tiered-fee \
  --version 20260524-001 \
  --activation-mode RemoteService \
  --remote-url http://10.0.0.9:9200 \
  --remote-contract-hash sha256:2222222222222222222222222222222222222222222222222222222222222222 \
  --remote-deployment-id image-sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb

python3 - <<'PY'
import json
path = "/tmp/live-market.instances.json"
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["fee.us-equity"] = {
    "instanceId": "fee.us-equity",
    "kind": "FeeModel",
    "moduleId": "tiered-fee",
    "version": "20260524-001",
    "config": {"currency": "USD", "tiers": [{"notional": 100000, "rate": 0.001}, {"notional": 1000000, "rate": 0.0005}]}
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\\n")
PY

python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id live-market \
  --version 20260524-002 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.none"],
    "target": ["target.none"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/live-market.instances.json \
  --market-rule market.default \
  --market-json '{"feeModel":"fee.us-equity"}'
```

当前这些 market 子组件是控制面可登记、可实例化、可接入的槽位；它们不会被写成 Engine module。运行时要真正替换 Lean `Security` 上的 fee/slippage/fill/buying power 等模型，还需要对应的 runtime adapter 支持。不要为了改 fee 或 slippage 去替换整个 `MarketRule` 服务；新的 API 粒度已经按子组件预留。

## 10. 更新正在运行的策略

只改参数时，修改本次 attachment 的完整 instances 文件，再重新 `attach`：

```bash
python3 - <<'PY'
import json
path = "/tmp/live-momentum.instances.json"
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["signal.momentum.20"]["config"]["lookback"] = 30
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\\n")
PY

python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id live-momentum \
  --version 20260524-005 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.momentum.20"],
    "target": ["target.none"],
    "constraint": ["risk.remote.maxdd"],
    "execution": ["execution.immediate"],
    "analyzer": ["analyzer.pnl"]
  }' \
  --instances-file /tmp/live-momentum.instances.json \
  --market-rule market.default
```

替换代码时，添加新模块版本，把 signal 实例指向新版本，再重新 `attach`：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Signal \
  --module-id momentum-signal \
  --version 20260524-002 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Signals.MomentumSignalModule \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath":"{{moduleRoot}}/artifacts/MomentumModule.dll"}' \
  --file "$MOMENTUM_DLL:artifacts/MomentumModule.dll"

python3 - <<'PY'
import json
path = "/tmp/live-momentum.instances.json"
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["signal.momentum.20.v2"] = {
    "instanceId": "signal.momentum.20.v2",
    "kind": "Signal",
    "moduleId": "momentum-signal",
    "version": "20260524-002",
    "config": {"lookback": 20}
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\\n")
PY

python3 strategy_submit.py --api http://127.0.0.1:8777 attach --lane-id main \
  --strategy-id live-momentum \
  --version 20260524-006 \
  --stages-json '{
    "inputs": ["input.us-largecap"],
    "universe": ["universe.none"],
    "signal": ["signal.momentum.20.v2"],
    "target": ["target.none"],
    "constraint": ["risk.remote.maxdd"],
    "execution": ["execution.immediate"],
    "analyzer": ["analyzer.pnl"]
  }' \
  --instances-file /tmp/live-momentum.instances.json \
  --market-rule market.default
```

不要覆盖旧版本。历史 attachment、snapshot、checkpoint 需要能回放到当时使用的模块版本和实例 config。

每次 `attach` 都会落一条新的迭代记录：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 history --limit 20
```

对应文件会写到：

```text
/data/data_jyz/trade/.runtime/control/iterations.json
/data/data_jyz/trade/.runtime/control/events.jsonl
/data/data_jyz/trade/.runtime/releases/_attachments/<laneId>/<strategyId>/<version>/pipeline.json
/data/data_jyz/trade/.runtime/releases/_attachments/<laneId>/<strategyId>/<version>/attachment.json
/data/data_jyz/trade/.runtime/releases/_attachments/<laneId>/<strategyId>/<version>/control-snapshot.json
```

`control-snapshot.json` 保存本次接入使用的模块定义、实例 config、manifest hash 和 Input 数据依赖。历史复现时以这份 snapshot 为准，而不是读取“当前最新”的实例 config。

记录一个数据版本：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 record-artifact \
  --kind Data \
  --artifact-id data.us-equity.daily.20260524 \
  --strategy-id live-momentum \
  --metadata-json '{"source":"lean-data-folder","symbols":["SPY","QQQ"],"resolution":"Daily","from":"2020-01-01","to":"2026-05-24"}' \
  --payload-json '{"dataVersion":"20260524","hash":"<sha256>"}'
```

记录一个 checkpoint 文件：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 record-artifact \
  --kind Checkpoint \
  --artifact-id checkpoint.live-momentum.20260524-006.001 \
  --strategy-id live-momentum \
  --version 20260524-006 \
  --metadata-json '{"reason":"before-signal-replace"}' \
  --file /path/to/checkpoint.json:checkpoint.json
```

记录一个结果或报告：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 record-artifact \
  --kind Result \
  --artifact-id result.live-momentum.20260524-006 \
  --strategy-id live-momentum \
  --version 20260524-006 \
  --metadata-json '{"runMode":"backtest","currency":"USD"}' \
  --file /path/to/result.json:result.json
```

查询运行产物：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 list-artifacts
python3 strategy_submit.py --api http://127.0.0.1:8777 history --limit 50
```

`record-artifact` 只做持久化登记，不会触发 Engine 热加载。它的用途是把数据版本、checkpoint、snapshot、result 和 report 绑定到策略版本或迭代记录上，保证后续能查询和复现。

## 11. 解绑和删除

解绑实例：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 detach \
  --instances-json '["signal.momentum.20"]'
```

解绑整个 stage：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 detach \
  --stages-json '["analyzer"]'
```

解绑 market 子组件槽位：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 detach \
  --market-json '["feeModel"]'
```

删除模块版本前，先确认没有历史全局实例引用它，也没有当前 lane attachment 引用它：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 list-instances

python3 strategy_submit.py --api http://127.0.0.1:8777 delete-module \
  --kind Signal \
  --module-id momentum-signal \
  --version 20260524-001
```

内置模块不能删除，也不能用同一个 `kind/moduleId/version` 覆盖。

删除是逻辑删除：模块会从当前模块库移出，但 `releaseRoot/_modules/...` 里的 artifact 不会被物理删除，历史 attachment 仍然能追溯到当时使用的文件。

## 12. 本地验证

改控制 API 或提交脚本后执行：

```bash
cd /data/data_jyz/trade
python3 -m py_compile strategy_submit_api.py strategy_submit.py hotswap_smoke.py remote_script_hotswap_smoke.py analyzer_hotswap_smoke.py
```

改 C# 模块契约或示例模块后执行：

```bash
cd /data/data_jyz/trade
/root/.dotnet/dotnet build hotswap-modules/QuantConnect.HotSwap.Modules.csproj -c Debug --nologo
/root/.dotnet/dotnet test Lean/Tests.Modules/QuantConnect.Modules.Tests.csproj -c Debug --no-build --nologo --logger "console;verbosity=minimal"
```

检查手册里是否又出现旧 Data 模块写法：

```bash
rg -n "ModuleKind\\.Data|DataSubscriptionRequest|CreateSubscriptions|StrategyADataModule|IDataSubscription" .
```

## 13. 什么时候需要重启 Engine

日常策略开发不应该重启 Engine。按下面规则判断：

| 操作 | 是否需要重启 Engine |
| --- | --- |
| `list-modules` / `list-instances` / `list-lanes` / `current --lane-id <lane>` | 不需要 |
| `add-module` | 不需要，只入库 |
| `attach --instances-file ...` | 不需要重启；这是当前推荐的“实例化并接入”提交方式 |
| `create-instance` | 不需要；仅保留为兼容旧全局实例库的接口 |
| `attach --lane-id <lane>` | 不需要重启，但会触发该 lane 的运行时热加载 |
| `detach --lane-id <lane>` | 不需要重启，但会触发该 lane 的运行时热加载 |
| `hotSwapMode=Live` | 不需要暂停或重启 |
| `hotSwapMode=RequiresPause` | 暂停对应 lane 后替换 |
| `hotSwapMode=RequiresFlatNoOrders` | 无持仓、无挂单后替换 |
| `hotSwapMode=RequiresRestart` | 才需要重启 Engine |

因此标准流程是：先 `add-module` 把模板入库，再用 `attach --instances-file ... --lane-id <lane>` 一次提交实例参数、输入输出 data key 和接入关系。策略更新应该走提交 API，不走 Engine config、环境变量或手写 manifest；新增或替换某条主线也不应该重启 Engine。
