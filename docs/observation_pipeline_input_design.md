# Observation 与 Pipeline 输入投影设计

> 状态：已实现并上线；独立设计、旧接口清理、策略正确性与性能验收均通过

## 1. 数据边界

```text
Dataset -> Sampler -> Sample -> Environment -> Observation
                                              |
                                              v
Pipeline {
  config.observationInput: whitelist -> blacklist -> Data Dict
  Data Dict -> Universe -> Signal Graph -> Target -> Constraint -> Pipeline output
}
```

- Sample 是 Sampler 给 Environment 的本周期数据；
- Observation 是 Environment 本周期能够提供的完整视图；
- Data Dict 是 Pipeline 主动选择后实际获得并继续写入的数据。

Observation 不是 Data Dict，不逐周期写入 Result。Analysis 可以向本周期 Result Data Dict
写入字段，但这些字段不进入下一周期状态。

## 2. Pipeline 结构

删除 Input Module、Input 上传入口和 `stages.inputs`。Pipeline 自身增加 config：

```json
{
  "config": {
    "observationInput": {
      "whitelist": [
        "time",
        "price.day.SPX",
        "price.day.AAPL",
        "portfolio.account"
      ],
      "blacklist": [
        "price.day.AAPL.open"
      ]
    }
  },
  "stages": {
    "universe": [],
    "target": [],
    "constraint": []
  }
}
```

Pipeline 内部执行顺序固定为：

```text
apply config.observationInput -> Universe -> Signal Graph -> Target -> Constraint -> Pipeline output
```

`config.observationInput` 是 Pipeline 自身的版本化配置，地位与 Module instance 的 config
相同，但不属于任何 Module。它没有 identity、ports、archive、lifecycle 或独立 Runtime，也不
占用 stage。Engine 在一次 Pipeline 执行开始时读取它并完成字段投影。

这项 config 只选择字段，不执行策略代码、计算指标、改名或生成新值。

## 3. 白名单与黑名单

数组元素是 Observation 中的 DataKey 路径，不使用 glob 或正则。父路径代表整个子树。

执行规则：

1. 从空对象开始，合并 whitelist 命中的路径；
2. 再从结果中删除 blacklist 命中的路径；
3. 保留原有嵌套结构，生成 Pipeline 初始 Data Dict。

例如 whitelist 选择 `price.day.AAPL`，blacklist 删除 `price.day.AAPL.open`，最终仍保留
AAPL 的 close、high 和 low。

约束：

- 空 whitelist 生成空 Data Dict；
- 两个数组内部路径必须唯一，冻结时按 DataKey 规范顺序排序；
- 路径必须能由 Environment 的 Observation schema 解析；
- blacklist 路径必须位于 whitelist 已选择的范围内；
- blacklist 不能删除后续 Module 的 required input；
- 路径在本周期不存在时保持缺失，不生成 null 或默认值；
- 对无法在 Engine 契约子集中精确表达的局部 composition image（包括 `allOf`、
  含同级 assertion 的 union、无法证明分支互斥的 `oneOf`），Composition 必须拒绝；
  使用者应选择完整父路径，禁止以放宽 schema 代替精确投影；
- 局部投影只在 source schema 可证明为空，或能构造并由权威 validator 复验至少一个
  合法 witness 时继续；satisfiability 或 presence 只能判定为 unknown 时必须拒绝，不能
  把 unknown 当作 optional 或可满足；
- `last.*` 等 Engine source 不属于 Observation，不能写入这两个数组。

## 4. 编译与运行

Composition 根据 Observation schema 和 Pipeline `config.observationInput` 编译唯一的投影计划
与初始 Data Dict schema。后续 Module 只能绑定投影后仍存在且 schema 兼容的 DataKey。

Runtime 按编译后的路径计划直接读取 Observation。选中的只读叶值可以共享，后续写入必须
copy-on-write；未选中的 Observation 字段不能进入 Module input、Data Dict、Analysis 的
`currentPipeline` source 或 Result writer。

Observation 的生成成本仍属于 Environment；本设计减少的是 Pipeline 校验、Module 输入、
Data Dict 演进、Result 编码和存储成本。要获得最佳效果，应优先 whitelist 少量精确路径，
而不是选择大父树后再大量 blacklist。

## 5. 状态与 Result

- `last.*` 继续表示上一周期完成后的 Data Dict，不表示上一周期 Observation；
- Environment 自己需要、但不应暴露给 Pipeline 的跨周期状态必须使用 Module lifecycle state；
- Environment 仍可读取上一周期 Pipeline 明确输出的 `last.<DataKey>`，例如交易目标；
- Analysis 通过 `currentPipeline` 只能读取最终 Data Dict；需要的 Observation 字段必须先进入
  whitelist；
- Result 保存 Observation schema digest、冻结后的 whitelist/blacklist 和最终 Data Dict
  schema，但不保存每周期 Observation 值；
- 每个 Result cycle 只保存 cycleId、decisionTime 和最终 Data Dict，不保存 Observation、
  Sample 或它们的逐周期副本；
- Visualization 和临时 Result Module 只能使用 Result 中实际存在的 DataKey。

## 6. 删除 Input Module

需要同步删除：

- `Input` Module kind、SDK 基类、上传/发布 API 和仓库入口；
- Pipeline 的 `stages.inputs`、对应 topology phase、UI 节点和 BuiltIn；
- 所有把 Environment 字段搬运到 Pipeline 的空操作或投影型 Input Module。

迁移规则：

- 只做字段选择的 Input 改写为 Pipeline `config.observationInput`；
- 真正做选股的逻辑放入 Universe；
- 特征计算和策略逻辑放入 Signal Graph；
- 历史 Result archive 保持不可变；当前 Runtime 不加载旧 schema，也不提供兼容
  loader。需要继续执行的旧 Pipeline 必须按新结构重新发布。

## 7. 实施边界

| 层 | 修改 |
|---|---|
| Contracts | 删除 Input kind/stage；Pipeline 增加 config.observationInput；Result cycle 删除 Observation 副本 |
| Compiler/Composition | 编译投影路径和投影后 schema，并验证所有后序 required inputs |
| Runtime/Worker | Environment 产出 Observation；投影后才建立 Data Dict；Result writer 只接收最终 Data Dict |
| SDK/Repository | 删除 InputModule、Input 上传与发布入口 |
| Web | 删除 Input 节点，在 Pipeline config 编辑区增加 whitelist/blacklist 可变长路径编辑器 |

Pipeline version 冻结规范化 Pipeline config。Observation contract 由 Environment 决定，
因此 Composition artifact、Backtest execution snapshot 和 Result metadata 冻结同一份
Observation contract digest；Runtime 只执行 Composition 编译后的路径段计划，不重新解释
Pipeline 草稿中的路径字符串。

## 8. 验收

- whitelist 后 blacklist 的结果在编译期与 Runtime 完全一致；
- 未选择字段不能到达任何 Pipeline Module 或 Result；
- Result cycle 不包含 Observation 副本；
- 相同 Observation、投影参数和 Pipeline 版本生成相同 Data Dict；
- 大 Observation、小 whitelist 的 Result 大小和 Pipeline 工作量只随投影结果增长；
- 整个能力不识别 K 线、股票或任何策略专用 DataKey。
