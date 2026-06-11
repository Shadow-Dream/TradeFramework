# Alpha 模块组合手册

这份文档只描述 Engine 已经在运行时，Alpha 在当前框架里的开发、组合和接入方式。Alpha 在控制 API 里对应 `Signal` 类型；开发者不修改 Engine config，不手写 live manifest，也不通过环境变量切换策略。Alpha 模块先用 `add-module` 入库；参数化实例、输入输出 data key 和 Pipeline 接入通过一次 `attach --instances-file ...` 完成。`create-instance` 只保留为旧全局实例库兼容接口，不是当前推荐流程。

## 1. 当前框架里的 Alpha 组合语义

`attach` 请求里的 `signal` 是一个数组：

```json
{
  "signal": ["signal.ema.20x50", "signal.rsi.14"]
}
```

Engine 侧绑定时会把第一个实例设为主 Alpha，后续实例依次追加为 Lean 的 composite alpha。也就是说，上面这个配置的含义是：

- `signal.ema.20x50` 可以产出自己的 `Insight`；
- `signal.rsi.14` 也可以产出自己的 `Insight`；
- Target/Portfolio 模块会收到这些 Alpha 产出的全部 insights。

这个组合是“并列叠加”，不是自动做逻辑 AND。比如“20 均线和 50 均线交叉”与“价格在 20 均线上方”如果拆成两个独立 Alpha 放进数组，当前框架不会自动理解成“两个条件同时满足才发信号”。这种条件交集应该写成一个新的 Alpha 模块，或者后续补一个专门的 `SignalComposer`/`SignalGate` 模块来做跨 Alpha 的门控。

因此当前有两种干净用法：

1. 多个独立 Alpha 并列接入：适合多因子各自产生信号，由 Target 模块统一处理权重。
2. 一个 Alpha 内部组合多个条件：适合“条件 A 且 条件 B 且 条件 C”这种明确的因子逻辑。

这两种方式都不需要重启 Engine。只有 `attach` 会影响正在运行的 pipeline。

## 2. 并列组合多个预置 Alpha

先确认控制 API 和预置模块可见：

```bash
cd /data/data_jyz/trade
python3 strategy_submit.py --api http://127.0.0.1:8777 list-modules
```

写出本次接入要使用的完整实例集合。这里使用同一个预置模块 `ema-cross-alpha@builtin` 实例化一个 20/50 EMA Alpha，再实例化一个 RSI Alpha：

```bash
cat >/tmp/alpha-composite.instances.json <<'JSON'
{
  "input.us-index": {
    "instanceId": "input.us-index",
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
  "signal.ema.20x50": {
    "instanceId": "signal.ema.20x50",
    "kind": "Signal",
    "moduleId": "ema-cross-alpha",
    "version": "builtin",
    "config": {"fastPeriod": 20, "slowPeriod": 50, "resolution": "Daily"}
  },
  "signal.rsi.14": {
    "instanceId": "signal.rsi.14",
    "kind": "Signal",
    "moduleId": "rsi-alpha",
    "version": "builtin",
    "config": {"period": 14, "resolution": "Daily"}
  },
  "universe.none": {
    "instanceId": "universe.none",
    "kind": "Universe",
    "moduleId": "null-universe",
    "version": "builtin"
  },
  "target.equal": {
    "instanceId": "target.equal",
    "kind": "Target",
    "moduleId": "equal-weighting-target",
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

把多个 Alpha 一起接入。关键是 `signal` 数组：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 attach \
  --strategy-id alpha-composite-demo \
  --version 20260524-001 \
  --stages-json '{
    "inputs": ["input.us-index"],
    "universe": ["universe.none"],
    "signal": ["signal.ema.20x50", "signal.rsi.14"],
    "target": ["target.equal"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/alpha-composite.instances.json \
  --market-rule market.default
```

检查当前 manifest：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 current
```

如果后续只想替换 Alpha 组合，不需要重新创建 Input、Target、Risk、Execution。重新 `attach` 一次，把 `signal` 数组改掉即可。

## 3. 条件组合 Alpha：20/50 均线交叉且价格在 20 均线上方

这个因子不应该拆成两个并列 Alpha，因为当前并列组合不会自动做 AND。正确做法是写一个 `Signal` 模块，让这个模块内部同时判断：

- 20 日均线上穿 50 日均线；
- 当前价格高于 20 日均线；
- 条件满足时产出一个看涨 `Insight`。

在本地新建一个 Alpha 源文件，例如：

```bash
mkdir -p /tmp/my-alpha
```

把下面代码保存为 `/tmp/my-alpha/Ma20Ma50CrossAboveMa20Alpha.cs`：

```csharp
using System;
using System.Collections.Generic;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.Consolidators;
using QuantConnect.Indicators;
using QuantConnect.Securities;

namespace MyCompany.Signals;

public sealed class Ma20Ma50CrossAboveMa20Alpha : AlphaModel
{
    private readonly int _fastPeriod;
    private readonly int _slowPeriod;
    private readonly Resolution _resolution;
    private readonly Dictionary<Symbol, SymbolState> _states = new();

    public Ma20Ma50CrossAboveMa20Alpha(
        int fastPeriod = 20,
        int slowPeriod = 50,
        Resolution resolution = Resolution.Daily)
    {
        _fastPeriod = fastPeriod;
        _slowPeriod = slowPeriod;
        _resolution = resolution;
        Name = $"MaCrossAboveFast({fastPeriod},{slowPeriod},{resolution})";
    }

    public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {
        foreach (var state in _states.Values)
        {
            if (!state.Fast.IsReady || !state.Slow.IsReady)
            {
                continue;
            }

            var price = algorithm.Securities[state.Symbol].Price;
            var fast = state.Fast.Current.Value;
            var slow = state.Slow.Current.Value;

            var goldenCross = state.PreviousFast <= state.PreviousSlow && fast > slow;
            var priceAboveFast = price > fast;

            if (goldenCross && priceAboveFast)
            {
                yield return Insight.Price(
                    state.Symbol,
                    _resolution.ToTimeSpan().Multiply(_fastPeriod),
                    InsightDirection.Up,
                    sourceModel: Name);
            }

            state.PreviousFast = fast;
            state.PreviousSlow = slow;
        }
    }

    public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
    {
        foreach (var security in changes.AddedSecurities)
        {
            if (!_states.ContainsKey(security.Symbol))
            {
                _states[security.Symbol] = new SymbolState(
                    algorithm,
                    security.Symbol,
                    _fastPeriod,
                    _slowPeriod,
                    _resolution);
            }
        }

        foreach (var security in changes.RemovedSecurities)
        {
            if (_states.Remove(security.Symbol, out var state))
            {
                state.Dispose(algorithm);
            }
        }
    }

    private sealed class SymbolState
    {
        private readonly IDataConsolidator _consolidator;

        public Symbol Symbol { get; }
        public SimpleMovingAverage Fast { get; }
        public SimpleMovingAverage Slow { get; }
        public decimal PreviousFast { get; set; }
        public decimal PreviousSlow { get; set; }

        public SymbolState(QCAlgorithm algorithm, Symbol symbol, int fastPeriod, int slowPeriod, Resolution resolution)
        {
            Symbol = symbol;
            Fast = new SimpleMovingAverage($"{symbol}.SMA{fastPeriod}", fastPeriod);
            Slow = new SimpleMovingAverage($"{symbol}.SMA{slowPeriod}", slowPeriod);

            _consolidator = algorithm.ResolveConsolidator(symbol, resolution);
            algorithm.SubscriptionManager.AddConsolidator(symbol, _consolidator);
            algorithm.RegisterIndicator(symbol, Fast, _consolidator);
            algorithm.RegisterIndicator(symbol, Slow, _consolidator);
            algorithm.WarmUpIndicator(symbol, Fast, resolution);
            algorithm.WarmUpIndicator(symbol, Slow, resolution);

            PreviousFast = Fast.Current.Value;
            PreviousSlow = Slow.Current.Value;
        }

        public void Dispose(QCAlgorithm algorithm)
        {
            algorithm.SubscriptionManager.RemoveConsolidator(Symbol, _consolidator);
        }
    }
}
```

用现有示例工程编译 DLL。这里为了演示最短路径，直接把文件复制进 `hotswap-modules` 工程；正式开发时可以建自己的模块工程，只要引用 `Lean/Common/QuantConnect.csproj` 和 `Lean/Algorithm/QuantConnect.Algorithm.csproj` 即可。

```bash
cp /tmp/my-alpha/Ma20Ma50CrossAboveMa20Alpha.cs hotswap-modules/
/root/.dotnet/dotnet build hotswap-modules/QuantConnect.HotSwap.Modules.csproj -c Debug
```

把 DLL 作为新的 `Signal` 模块加入模块库。`add-module` 只入库，不启用：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Signal \
  --module-id ma20-ma50-cross-above-ma20-alpha \
  --version 20260524-001 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Signals.Ma20Ma50CrossAboveMa20Alpha \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath":"{{moduleRoot}}/artifacts/QuantConnect.HotSwap.Modules.dll"}' \
  --file hotswap-modules/bin/Debug/net10.0/QuantConnect.HotSwap.Modules.dll:artifacts/QuantConnect.HotSwap.Modules.dll
```

把带参数的 Alpha 实例写进本次完整实例集合。`config` 会按构造函数参数名绑定到 `fastPeriod`、`slowPeriod`、`resolution`：

```bash
python3 - <<'PY'
import json
path = "/tmp/alpha-composite.instances.json"
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["signal.ma20ma50.above20"] = {
    "instanceId": "signal.ma20ma50.above20",
    "kind": "Signal",
    "moduleId": "ma20-ma50-cross-above-ma20-alpha",
    "version": "20260524-001",
    "config": {"fastPeriod": 20, "slowPeriod": 50, "resolution": "Daily"}
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\\n")
PY
```

接入这个条件组合 Alpha：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 attach \
  --strategy-id ma-cross-gated-alpha \
  --version 20260524-001 \
  --stages-json '{
    "inputs": ["input.us-index"],
    "universe": ["universe.none"],
    "signal": ["signal.ma20ma50.above20"],
    "target": ["target.equal"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/alpha-composite.instances.json \
  --market-rule market.default
```

如果想让这个条件组合 Alpha 和另一个独立 Alpha 并列运行，把它们一起放进 `signal` 数组：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 attach \
  --strategy-id ma-cross-plus-rsi \
  --version 20260524-002 \
  --stages-json '{
    "inputs": ["input.us-index"],
    "universe": ["universe.none"],
    "signal": ["signal.ma20ma50.above20", "signal.rsi.14"],
    "target": ["target.equal"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/alpha-composite.instances.json \
  --market-rule market.default
```

这时含义是：

- `signal.ma20ma50.above20` 内部做“20/50 金叉且价格在 20 均线上方”；
- `signal.rsi.14` 独立地产生 RSI 信号；
- Target 统一处理两个 Alpha 的输出。

## 4. 开发和替换 Alpha 的固定流程

开发新 Alpha 时按这个顺序做：

1. 写 Alpha 代码，只关心 `Update` 和必要的 `OnSecuritiesChanged`。
2. 本地编译 DLL 或打包脚本/RPC 服务。
3. 用 `add-module` 把模块实现加入 `Signal` 模块库。
4. 在本次完整 instances 文件里写入带参数的 Alpha 实例、输入 data key、输出 data key。
5. 用 `attach --instances-file ...` 把实例放进 `signal` 数组。
6. 用 `current` 检查 Engine 当前 manifest。

修改参数时，不需要重新 `add-module`。直接修改完整 instances 文件里的 config，再 `attach`：

```bash
python3 - <<'PY'
import json
path = "/tmp/alpha-composite.instances.json"
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["signal.ma10ma30.above10"] = {
    "instanceId": "signal.ma10ma30.above10",
    "kind": "Signal",
    "moduleId": "ma20-ma50-cross-above-ma20-alpha",
    "version": "20260524-001",
    "config": {"fastPeriod": 10, "slowPeriod": 30, "resolution": "Daily"}
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\\n")
PY

python3 strategy_submit.py --api http://127.0.0.1:8777 attach \
  --strategy-id ma-cross-gated-alpha \
  --version 20260524-002 \
  --stages-json '{
    "inputs": ["input.us-index"],
    "universe": ["universe.none"],
    "signal": ["signal.ma10ma30.above10"],
    "target": ["target.equal"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --instances-file /tmp/alpha-composite.instances.json \
  --market-rule market.default
```

替换 Alpha 代码时，推荐发布新模块版本，不覆盖旧版本：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Signal \
  --module-id ma20-ma50-cross-above-ma20-alpha \
  --version 20260524-002 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Signals.Ma20Ma50CrossAboveMa20Alpha \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath":"{{moduleRoot}}/artifacts/QuantConnect.HotSwap.Modules.dll"}' \
  --file hotswap-modules/bin/Debug/net10.0/QuantConnect.HotSwap.Modules.dll:artifacts/QuantConnect.HotSwap.Modules.dll
```

然后创建指向新版本的实例，再 `attach`。这样历史 pipeline 仍然能复现旧版本行为。

## 5. 什么时候不需要重启 Engine

下面这些操作都不应该重启 Engine：

| 操作 | 是否影响运行中 Engine | 说明 |
| --- | --- | --- |
| `add-module` | 否 | 只把 Alpha 实现加入模块库 |
| `attach --instances-file ...` | 是 | 提交 Alpha 参数、data key 绑定和接入关系，热更新运行中的 pipeline |
| `create-instance` | 否 | 旧全局实例库兼容接口，当前不作为标准流程 |
| `attach` 更换 `signal` 数组 | 是 | 热更新运行中的 pipeline |
| 调整 Alpha 参数 | 是 | 修改 inline instance config 后重新 `attach` |
| 并列增加一个 Alpha | 是 | 修改 `signal` 数组后重新 `attach` |

是否需要暂停或更复杂的切换流程，应该由模块自己的 `hotSwapMode` 决定。对于这里的 `InProcessPlugin` + `Live` Alpha，目标行为是热接入，不重启 Engine。

## 6. 当前需要注意的边界

多个 Alpha 并列接入时，框架不会自动解决同一 symbol 的冲突信号。例如一个 Alpha 发 `Up`，另一个 Alpha 发 `Down`，最终仓位如何处理取决于 Target/Portfolio 模块。

条件组合不要靠多个 Alpha 的顺序表达。`signal` 数组顺序只影响绑定顺序，不表示“先过滤再发信号”。需要过滤、投票、加权、AND/OR 逻辑时，应写成一个明确的 Alpha 模块，或者后续补一个专门的组合器模块。

Alpha 不负责订阅数据源，也不负责下单。Input/Universe 决定有哪些证券进入算法，Alpha 只根据已经进入算法的数据产出 `Insight`，Target 把 `Insight` 转成目标仓位，Execution 执行订单。

## 7. 目标形态：Alpha 图而不是单纯 Alpha 列表

当前 `signal` 数组只能表达“接入哪些 Alpha 实例”。它适合 Lean 原生 composite alpha，但不能表达“这个 Alpha 的 `ema20` 输入来自另一个模块的 `ema_1` 输出”这种依赖关系。

现在控制 API 已经支持在 `Signal` 槽位内部提交一个 Alpha 图。图里每个节点都是一个 Alpha/Indicator/Logic 模块实例，每个节点声明自己的输入端口、输出端口和端口类型；节点之间通过稳定的连线 id 连接。Engine 侧已经能解析 `alphaGraph`、加载图节点，并用 `CompiledAlphaGraphModule` 按图执行。开发者要写可被图执行的节点时，实现 `IAlphaGraphNode`，普通 Lean `IAlphaModel` 仍然可以作为单输出节点接入。

### 7.1 模块注册时声明端口契约

模块加入模块库时，除了 `kind/moduleId/version/entryPoint`，可以用 `--ports-json` 声明端口契约：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 add-module \
  --kind Signal \
  --module-id ema-indicator \
  --version 20260524-001 \
  --activation-mode InProcessPlugin \
  --entry-point MyCompany.Alphas.EmaIndicatorNode \
  --hot-swap-mode Live \
  --parameters-json '{"assemblyPath":"{{moduleRoot}}/artifacts/MyAlphaNodes.dll"}' \
  --file /tmp/build/MyAlphaNodes.dll:artifacts/MyAlphaNodes.dll \
  --ports-json '{
    "inputs": {
      "price": {
        "type": "series.price",
        "required": true
      }
    },
    "outputs": {
      "ema": {
        "type": "series.indicator.ema"
      }
    }
  }'
```

等价的完整 JSON 定义是：

```json
{
  "kind": "Signal",
  "moduleId": "ema-indicator",
  "version": "20260524-001",
  "activationMode": "InProcessPlugin",
  "entryPoint": "MyCompany.Alphas.EmaIndicatorNode",
  "hotSwapMode": "Live",
  "ports": {
    "inputs": {
      "price": {
        "type": "series.price",
        "required": true
      }
    },
    "outputs": {
      "ema": {
        "type": "series.indicator.ema"
      }
    }
  },
  "configSchema": {
    "period": {
      "type": "integer",
      "default": 20
    },
    "resolution": {
      "type": "string",
      "default": "Daily"
    }
  }
}
```

另一个判断模块可以声明成：

```json
{
  "kind": "Signal",
  "moduleId": "cross-and-price-gate",
  "version": "20260524-001",
  "activationMode": "InProcessPlugin",
  "entryPoint": "MyCompany.Alphas.CrossAndPriceGateNode",
  "hotSwapMode": "Live",
  "ports": {
    "inputs": {
      "fast": {
        "type": "series.indicator"
      },
      "slow": {
        "type": "series.indicator"
      },
      "price": {
        "type": "series.price"
      }
    },
    "outputs": {
      "rise": {
        "type": "signal.direction"
      }
    }
  }
}
```

这里的 `type` 不一定必须非常细，但至少应该区分：

| 类型 | 含义 |
| --- | --- |
| `series.price` | 价格序列 |
| `series.volume` | 成交量序列 |
| `series.indicator` | 通用指标序列 |
| `series.indicator.ema` | EMA 指标序列 |
| `signal.direction` | 方向信号，例如 rise/fall/flat |
| `signal.score` | 连续分数 |
| `insight.price` | Lean `Insight.Price` 语义 |
| `any` | 任意类型，供 switch/branch/debug 等泛型节点使用 |

端口类型规则应该允许兼容关系。例如 `series.indicator.ema` 可以接到 `series.indicator`，但不能接到 `series.price`；`any` 可以接任意输出，但后端需要在运行时做类型保护。

### 7.2 在实例 payload 里绑定输入和输出字段名

实例化时不只是传 config，还要给每个输入端和输出端绑定连线 id。比如：

```json
{
  "instanceId": "alpha.price.spy",
  "kind": "Signal",
  "moduleId": "price-source",
  "version": "20260524-001",
  "config": {
    "symbol": "SPY",
    "resolution": "Daily"
  },
  "outputs": {
    "price": "price_1"
  }
}
```

20 日 EMA 节点：

```json
{
  "instanceId": "alpha.ema20",
  "kind": "Signal",
  "moduleId": "ema-indicator",
  "version": "20260524-001",
  "config": {
    "period": 20,
    "resolution": "Daily"
  },
  "inputs": {
    "price": "price_1"
  },
  "outputs": {
    "ema": "ema_1"
  }
}
```

50 日 EMA 节点：

```json
{
  "instanceId": "alpha.ema50",
  "kind": "Signal",
  "moduleId": "ema-indicator",
  "version": "20260524-001",
  "config": {
    "period": 50,
    "resolution": "Daily"
  },
  "inputs": {
    "price": "price_1"
  },
  "outputs": {
    "ema": "ema_2"
  }
}
```

组合判断节点：

```json
{
  "instanceId": "alpha.cross-gate",
  "kind": "Signal",
  "moduleId": "cross-and-price-gate",
  "version": "20260524-001",
  "inputs": {
    "fast": "ema_1",
    "slow": "ema_2",
    "price": "price_1"
  },
  "outputs": {
    "rise": "rise_1"
  }
}
```

把最终方向信号转成 Lean Insight 的节点：

```json
{
  "instanceId": "alpha.rise-to-insight",
  "kind": "Signal",
  "moduleId": "direction-to-price-insight",
  "version": "20260524-001",
  "config": {
    "period": "20.00:00:00"
  },
  "inputs": {
    "direction": "rise_1",
    "price": "price_1"
  },
  "outputs": {
    "insight": "insight_1"
  }
}
```

### 7.3 `attach` 时提交 Alpha 图

脚本式编辑可以在 `attach` 时直接提交一个数组。核心是 `--alpha-graph-json` 的 `nodes`：

```bash
python3 strategy_submit.py --api http://127.0.0.1:8777 attach \
  --strategy-id blueprint-alpha-demo \
  --version 20260524-001 \
  --stages-json '{
    "inputs": ["input.us-index"],
    "universe": ["universe.none"],
    "signal": [],
    "target": ["target.equal"],
    "constraint": ["risk.none"],
    "execution": ["execution.immediate"],
    "analyzer": []
  }' \
  --alpha-graph-json '{
    "nodes": [
      "alpha.price.spy",
      "alpha.ema20",
      "alpha.ema50",
      "alpha.cross-gate",
      "alpha.rise-to-insight"
    ],
    "outputs": {
      "insights": ["insight_1"]
    }
  }' \
  --market-rule market.default
```

蓝图式编辑时，前端不需要理解 Lean。它只需要：

1. 拖入节点；
2. 读取节点端口契约；
3. 给每条连线分配一个唯一 id，例如 `price_1`、`ema_1`、`ema_2`；
4. 把节点的 `inputs` 和 `outputs` 写成上面的 JSON；
5. 提交给控制 API。

脚本式编辑时，用户直接写同样的 JSON 数组即可。

### 7.4 后端校验规则

控制 API 在接受 Alpha 图时应该先做静态校验：

| 校验项 | 规则 |
| --- | --- |
| 节点存在 | 每个 `instanceId` 必须存在于本次 attachment 的 `instances`，或存在于兼容旧流程的全局实例库 |
| 端口存在 | `inputs`/`outputs` 里的字段名必须在模块端口契约里声明 |
| 输入已连接 | required input 必须绑定一个连线 id |
| 输出唯一 | 同一个连线 id 只能有一个生产者，除非端口声明允许 merge |
| 类型兼容 | 输入端口类型必须兼容输出端口类型 |
| 无环依赖 | 默认要求 DAG；有状态反馈节点必须显式声明允许 cycle |
| 最终输出 | 至少有一个输出能转换成 Lean `Insight` |

这些校验让前端拖线时可以即时提示，也让脚本式提交能被明确拒绝。

### 7.5 后端编译方式

Alpha 图最终仍然要编译成 Engine 可执行的 `Signal`。推荐方式是控制 API 把 `alphaGraph` 编译成一个系统内置的组合模块，例如：

```json
{
  "kind": "Signal",
  "moduleId": "compiled-alpha-graph",
  "version": "20260524-001",
  "parameters": {
    "graph": {
      "nodes": [],
      "edges": [],
      "outputs": {}
    }
  }
}
```

Engine 侧会把 `alphaGraph` 包装成一个 `CompiledAlphaGraphModule`。这个模块负责：

1. 按拓扑顺序执行节点；
2. 把每个输出写入运行时上下文，例如 `price_1`、`ema_1`、`rise_1`；
3. 把最终 `insight.*` 输出转换成 Lean `Insight`；
4. 对状态型节点保存必要状态，以支持 snapshot/checkpoint。

这样可以保持外层 pipeline 不复杂：`Signal` 槽位仍然是数组，但数组里的一个元素可以是“已编译的 Alpha 图”。如果用户愿意，也可以继续把多个已编译图或普通 Alpha 并列放进 `signal` 数组。

控制 API 现在写入 manifest 的 `alphaGraph` 是自包含结构，不只保存节点 id。它会额外生成：

| 字段 | 用途 |
| --- | --- |
| `nodes` | 用户提交的节点顺序 |
| `outputs` | 图的最终输出线，例如 `insights: ["insight_1"]` |
| `bindings` | 每个节点的 `moduleId/version/config/inputs/outputs/ports` |
| `edges` | 根据 wire id 推导出的节点连接关系 |

因此后续 Engine 运行时不需要回查控制 API 状态，也能从 release manifest 复现完整 Alpha 图。

图节点运行时接口是：

```csharp
public interface IAlphaGraphNode : IModule
{
    IReadOnlyDictionary<string, object> Evaluate(
        QCAlgorithm algorithm,
        Slice data,
        AlphaGraphNodeBinding binding,
        IReadOnlyDictionary<string, object> inputs);

    void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes);
}
```

`Evaluate` 返回的是端口名到值的字典。例如 EMA 节点返回 `{ "ema": emaValue }`，方向判断节点返回 `{ "rise": directionValue }`，最终 Insight 节点返回 `{ "insight": new[] { insight } }`。Engine 会按照实例的 `outputs` 把这些端口值写入对应 wire id。

### 7.6 和当前实现的差距

当前框架现在已经有：

- `Signal` 槽位数组；
- `add-module` 模块入库；
- inline attachment instance config；
- `attach` 热接入；
- Lean composite alpha 并列组合；
- 模块端口契约 `ports.inputs/ports.outputs`；
- 实例级输入/输出连线绑定；
- Alpha 图 schema；
- 控制 API 的图校验；
- Engine manifest 解析 `alphaGraph`；
- Engine 加载图节点；
- `CompiledAlphaGraphModule` 按图执行节点并收集 `insights` 输出。

还缺：

- 更完整的类型系统和可配置端口兼容规则；
- 图级 snapshot/checkpoint/version 记录。

所以，按预期体验看，当前 `signal` 数组只是第一层插槽；真正支撑蓝图和脚本式 Alpha 组装的，是下一层 `alphaGraph`。
