# Basic Workflow 模块数据流设计

> 状态：应用层 v2 已实现，待发布

## 1. 边界

Basic Workflow 描述一条具体资源组合链上的相邻输入输出合同：

```text
Sampler -> Sample -> Environment -> Observation
                                  |
                                  v
Pipeline {
  config.observationInput -> Data Dict
  Data Dict -> Universe -> Signal Graph -> Target -> Constraint -> Pipeline output
}
                                  |
                                  +-- Analysis / next cycle last.*
```

不存在全局 DataKey 或公共 K 线合同。K 线结构只属于本工作流所选 Sampler 的
`outputSchema`；下游资源因为选择了这个 Sampler，才声明消费相应路径。

更换 Sampler 后，必须重新选择或 wiring 与其输出兼容的下游资源。

Observation、Pipeline 输入投影和取消 Input Module 是通用 Engine 调整，详见
[Observation 与 Pipeline 输入投影设计](observation_pipeline_input_design.md)。其余 K 线、账户、
选股和交易字段仍只属于本工作流资源。

TradeEngine 只负责：

- DataKey、schema、Module ports 和 Graph wiring；
- Pipeline config 驱动的 Observation 到 Data Dict 投影；
- Sampler、Environment、Pipeline、Analysis 的执行顺序；
- `last.*`、`currentPipeline` 和 frozen artifact 验证。

Engine 不理解 K 线、`price`、股票、signal、target、账户或订单。BuiltIn 也使用普通资源的
归档、编译和 Runtime，没有特殊执行路径。

## 2. 字段需求

基础字段由常用策略和回测需求反推：

| 需求 | 需要的数据 |
|---|---|
| 均线、趋势、RSI | `close` |
| 突破策略 | `high`、`low`、`close` |
| K 线动量 | `open`、`close` |
| 多标的选股 | Observation 投影后的标的范围、本周期入选标的 |
| 下一周期执行 | 下一根 bar 的 `open`、上一目标、当前 positions |
| 账户估值 | 当前 bar 的 `close`、cash、positions |
| 交易结果展示 | side、quantity、price、fee |

基础工作流不输出没有上述消费者的字段。需要成交量、部分成交、拒单原因、累计费用或更复杂
账户信息时，由相应 Sampler、Environment 或 Analysis Module 明确增加自己的输出合同。

本文 JSON 中的 `<period>`、`<instrumentId>`、`<number>` 是说明占位符。Runtime number
必须有限；DataKey 不使用 array；Module 不能修改输入；`decisionTime` 和 `last` 由 Engine
保留，Module 不能绑定完整 `last`。

## 3. Dataset 组织形式

这是下一节 K 线 Sampler 的 Dataset 格式，不是 TradeEngine 的全局格式。

### 目录格式

```text
<dataset>/
  <period>/
    <instrumentId>.csv
```

例如：

```text
dataset/
  day/
    SPX.csv
    AAPL.csv
  week/
    SPX.csv
```

目录名是周期，文件名是标的编号。第一版只支持 CSV；`period` 和 `instrumentId` 不能包含
`.`，因为它们会成为 Sampler 输出的 DataKey 路径。

### 表格格式

```csv
time,open,close,high,low
2026-01-02T21:00:00Z,5903.1,5942.7,5950.2,5891.4
2026-01-03T21:00:00Z,5942.7,5968.0,5975.2,5931.6
```

每个文件只存一个周期、一个标的。表头固定为 `time,open,close,high,low`，使用 UTF-8
和逗号分隔。`time` 是 K 线结束时间，使用带绝对时区的 ISO-8601，严格递增且不能重复。
OHLC 必须是有限正数，并满足 `low <= min(open, close) <= max(open, close) <= high`。

Sampler 按配置的决策周期对这些表格做时间对齐，并转换为下一节定义的输出。Engine 只把
目录作为不可变 Dataset 封存，不解释目录名、文件名或表格列。

## 4. Sampler

上一节目录中的 CSV 文件是 Sampler 的资源输入，不是 DataKey 输入。

### 预期输入 DataKey

```json
{}
```

### 预期输出 DataKey

```json
{
  "time": "<ISO-8601 time>",
  "price": {
    "<period>": {
      "<instrumentId>": {
        "open": "<number>",
        "close": "<number>",
        "high": "<number>",
        "low": "<number>"
      }
    }
  }
}
```

这只是该 Sampler 的输出格式。选择它的下游 Module 可以绑定：

```text
price.day.SPX.open
price.day.SPX.close
price.week.SPX.high
price.month.AAPL.low
```

Sampler 可以同时输出 Dataset 中配置的多个周期和多个标的。

规则：

- `time` 等于 Sampler frame 的 `decisionTime`；
- cycle 以决策周期表的 time 为准，其他周期只读取不晚于该 time 的最新一行；
- 只输出在 `time` 时已经可见的完整 K 线；
- `low <= min(open, close) <= max(open, close) <= high`；
- Sampler 负责数据源字段、时区、周期和标的 ID 的转换；
- Sampler 输出 Dataset 中配置的市场范围，不按某个策略做选股；
- Sampler 不输出 `decisionTime`、`last` 或任何交易状态。

## 5. Environment

这个 Environment 消费上一节 Sampler 的完整输出。它按配置的 `<executionPeriod>`，在各标的
本周期 bar 的 `open` 执行上一周期目标，并按 `close` 估值。

### 预期输入 Sample

```json
{
  "time": "<ISO-8601 time>",
  "price": {
    "<period>": {
      "<instrumentId>": {
        "open": "<number>",
        "close": "<number>",
        "high": "<number>",
        "low": "<number>"
      }
    }
  }
}
```

上例省略 `last`。Environment 只绑定上一周期 Pipeline 目标：

```text
last.intent.approved
```

首周期不存在该路径。cash 和 positions 属于 Environment Module lifecycle state，不依赖上一
周期 Observation 或 Data Dict；本周期 equity 由该状态和当前 close 计算。

### 预期输出 Observation

```json
{
  "time": "<ISO-8601 time>",
  "price": {
    "<period>": {
      "<instrumentId>": {
        "open": "<number>",
        "close": "<number>",
        "high": "<number>",
        "low": "<number>"
      }
    }
  },
  "portfolio": {
    "account": {
      "cash": "<number>",
      "positions": {
        "<instrumentId>": "<number>"
      },
      "equity": "<number>"
    }
  },
  "execution": {
    "orders": {
      "<instrumentId>": {
        "side": "<buy|sell>",
        "quantity": "<positive number>",
        "price": "<number>",
        "fee": "<number>"
      }
    }
  }
}
```

没有成交时，`execution.orders` 为空对象。

该对象是 Observation，不是 Data Dict。它只在当前周期内供 Pipeline 投影使用，不逐周期写入
Result。

规则：

- Environment 显式透传 Sampler 的完整 `time` 和 `price`；
- `intent.approved.<instrumentId>` 是该标的的获批绝对目标持仓，`0` 表示平仓；
- 未出现在 `intent.approved` 中的标的保持原仓位；
- 每个目标标的都必须存在本周期 execution bar，否则执行失败；
- `equity = cash + sum(positions.<instrumentId> * price.<executionPeriod>.<instrumentId>.close)`；
- orders 中存在对应标的就表示实际成交，不另外输出 status；quantity 是正数，方向由 side 表示；
- fee 已从 cash 扣除；
- 首周期初始化 cash 和空 positions，orders 为空对象；
- Environment 不生成 signal。

`marketValue` 可由 positions 和 close 得到，`notional` 可由 quantity 和 price 得到，
`cumulativeFees` 可由历史 `execution.orders.*.fee` 累加，因此不作为基础输出。

## 6. Pipeline

Engine 按以下顺序执行，空 stage 只表示跳过，不能改变顺序：

```text
apply Pipeline config.observationInput -> Universe -> Signal Graph -> Target -> Constraint -> Pipeline output
```

每个阶段都能读取前序阶段已经写入的 DataKey。兼容性由实际 Module ports、DataKey binding 和
schema 决定。

### 6.1 Universe

Universe 在投影后可见的标的范围内，确定本周期实际参与决策的标的。

#### 预期输入 DataKey

```json
{
  "price": {
    "<period>": {
      "<instrumentId>": {
        "open": "<number>",
        "close": "<number>",
        "high": "<number>",
        "low": "<number>"
      }
    }
  }
}
```

#### 预期输出 DataKey

```json
{
  "universe": {
    "selected": {
      "<instrumentId>": true
    }
  }
}
```

`universe.selected` 只能包含投影后 `price` 中存在的标的，值固定为 true；本周期没有入选标的
时输出空对象。

### 6.2 Signal Graph

#### 预期输入 DataKey

省略。Signal 可以按实际策略绑定投影后存在的 `time`、`price.*`、账户字段以及
`universe.selected`。它需要什么，由具体 Signal Module 的输入 ports 决定。

#### 预期输出 DataKey

省略。趋势方向、评分、事件或其他信号结构都由具体 Signal Graph 决定，不规定统一的
`signal.*`。具体 Signal Graph 发布时仍须声明真实输出 DataKey 和 schema，下游必须选择与该
输出兼容的 Target Module。

从 Target 开始，下面只描述本工作流选择的一组具体后序 Module 合同，不是 Pipeline 的公共
DataKey 约定。

### 6.3 Target

#### 预期输入 DataKey

省略。由所选 Signal Graph 和 Target Module 共同确定。

#### 预期输出 DataKey

```json
{
  "intent": {
    "requested": {
      "<instrumentId>": "<number>"
    }
  }
}
```

`intent.requested.<instrumentId>` 是该标的的绝对目标持仓；没有目标时输出空对象。

### 6.4 Constraint

#### 预期输出 DataKey

```json
{
  "intent": {
    "approved": {
      "<instrumentId>": "<number>"
    }
  }
}
```

Constraint 可以综合目标、账户、持仓、价格、购买力、杠杆和其他风险因素。具体输入由所选
Constraint Module 的 ports 和 wiring 声明，本协议不固定其输入 DataKey 集合。
`intent.approved` 只保留通过综合约束的目标；没有通过的目标时输出空对象。Constraint 完成后
的 Data Dict 就是 Pipeline 的最终输出；真实成交和账户更新属于下一周期 Environment。

## 7. Analysis

Analysis 在 Pipeline 完成本周期 Data Dict 后运行。具体输入由所选 Analysis Modules 和 Graph
wiring 声明；需要读取本周期已完成的 Pipeline 数据时使用 `source: currentPipeline`，其他
Engine 保留输入按其公开 source 合同绑定。本协议不固定 Analysis 的输入 DataKey 集合。

Analysis 的输出只进入 Result，不进入下一周期状态。本协议也不固定其输出 DataKey 或指标
集合；收益、回撤、风险、归因等具体计算由更下一级的 Analysis 资源声明。Visualizer 只按
Result 中实际声明的 DataKey 选择展示内容。

## 8. 周期时序

```text
Sampler(t)
  输出 Sample: time(t) 和 price(t)
        |
        v
Environment(t)
  执行 intent.approved(t-1)
  输出 Observation(t)
        |
        v
Pipeline(t)
  config.observationInput: whitelist -> blacklist -> 初始 Data Dict(t)
  Universe -> Signal Graph -> Target -> Constraint
  输出 intent.approved(t)
        |
        v
Analysis(t)
  按所选 Analysis Graph 的声明读取输入并输出 Result 字段
```

必须保证：

- 当前完整 bar 可以参与当前决策；
- 当前 target 不能在同一周期成交；
- `intent.approved(t)` 最早在 `t+1` 执行；
- 第一周期没有 `last.*`；
- 最后一周期 target 是未执行意图，不能伪造订单或收益。
- Observation 不写入 Result，完成后的 Data Dict 才写入 Result。

## 9. 应用层实现状态

Basic Workflow v2 已由普通应用层资源实现，不包含 TradeEngine 特殊执行路径：

- Dataset Adapter 校验并封存上述目录、CSV 表头、绝对时间、严格时序和 OHLC 约束；
- `basic-price-map-sampler` 按 `decisionPeriod` 生成 cycle，并对多个周期、多个标的做可见数据对齐；
- `basic-multi-asset-bar-account` 在 Module lifecycle state 中维护 cash 和 positions，在下一根
  execution bar 的 open 执行上一周期获批目标，并按当前 close 估值；
- `basic-price-map-universe`、`basic-neutral-score-map`、
  `basic-score-map-position-target` 和 `basic-absolute-position-map-constraint` 提供一组可替换的
  Pipeline BuiltIn 与 scaffold；
- `application_components/basic_workflow` 提供与策略身份无关的账户、券商规则和绩效计算内核；
  普通 BuiltIn Module 负责端口与状态适配，组件目录只组织可归档资源，不形成 Engine 注册表；
- 协议 registry 不固定 Analysis 资源；具体 Analysis Graph 自行声明输入、输出和指标；
- Visualization preset 只选择 Result 中实际存在的 close、equity、position 和 approved intent。

协议标识为 `trade.basic-workflow/2.0.0`，profile 为 `multi-instrument-bar-position`。Dataset、
Sampler、Environment、Pipeline 和 Visualizer 均通过 Engine 公开的归档、编译和 Runtime 接口工作；
兼容性仍由 Engine 对真实 schema、DataKey binding 和 wiring 做最终验证。
