# Basic Workflow v2 应用层实现验收

原始问题（原文）：

> 可以开始实现。这些应该都是应用层的模块实现，不需要修改TradeEngine代码

- 提问时间：2026-08-17（会话接口未提供具体时刻，America/New_York）
- 回答与验收时间：2026-08-17T11:26:37-04:00
- 实现基线：`46798f44e140ade71083092be2b0fc86e71bcecc`
- 实现范围：应用协议、BuiltIn、Dataset Adapter、专项测试和协议文档

## 1. 已确认事实

本次实现没有修改 `engine/**`。最终 `git diff --name-only -- engine` 无输出，所有运行路径都使用
TradeEngine 已公开的 Dataset、Sampler、Module、Graph、Backtest 和 Result 接口。并行进程留下的
`web/*` 与 `tests/web/test_backtest_submit.py` 工作树变更不在本次范围内，也没有被改写。

协议身份提升为 `trade.basic-workflow/2.0.0`，profile 为
`multi-instrument-bar-position`。主要应用层落点如下：

- `dataset_adapters/basic_workflow.py`：封存 `<period>/<instrumentId>.csv`，发布普通不可变 Dataset；
- `dataset_adapters/basic_workflow_conformance.py`：严格校验目录、字段、时间、OHLC 和 v2 descriptor；
- `builtin_implementations/sampler/basic_price_map_sampler.py`：输出 `time` 与递归 `price` map；
- `builtin_implementations/environment/basic_multi_asset_bar_account.py`：在 Module lifecycle state 中维护
  cash/positions，执行上一周期 intent，并输出 account/orders；
- `builtin_implementations/pipeline/*_map_*.py`：提供 Universe、占位 Signal、Target 和一个具体 Constraint；
- `application_protocols/basic_workflow/scaffolds.py`：通过普通 ports、DataKey 与 wiring 组合 Pipeline；
- `application_protocols/basic_workflow/registry.py`：只登记 Dataset、Sampler、Environment 和四类 Pipeline
  角色，不固定 Analysis；
- `application_protocols/basic_workflow/visualization_presets.py`：显式选择实际存在的 close、equity、
  position 与 approved intent，不假定 Analysis 指标。

在全新临时 release 中调用公共 BuiltIn installer 得到以下精确资源引用。这里的 `version=1` 只表示
临时空仓库中的首个不可变版本，不代表已经向用户的目标 release 发布：

| 资源 ID | 类型 | 版本 | contentDigest |
|---|---|---:|---|
| `basic-price-map-sampler` | Sampler | 1 | `sha256:bf5b04f5ceb218673c9aebdcd7346f951a3fd56d19948c4cb1065315af19dd97` |
| `basic-price-map-universe` | Universe Module | 1 | `sha256:3187ab94eeb0df9b317741b1b3a87e5242ccffe638bba67483cf7c518e391d33` |
| `basic-neutral-score-map` | Signal Module | 1 | `sha256:9b3aa826ac3885d6d1e69cb51ebad08572410b0aea3e7e4225b72506bfbe4d10` |
| `basic-score-map-position-target` | Target Module | 1 | `sha256:9b925ce6db74146ddd0d56ef26be65850a99ec36fe4a72d4ac156251dbadba43` |
| `basic-absolute-position-map-constraint` | Constraint Module | 1 | `sha256:de174fd769b98568f23b8c4765dd79dc752f4555dab05c5419fc0e7b59bc7d81` |
| `basic-multi-asset-bar-account` | Environment Module | 1 | `sha256:55eaa10cf9be5b6abeae4b66d92e37a2199dcb45a05d8c496ac83d67eaf25c27` |
| `basic-multi-asset-paper-environment` | Environment Graph | 1 | `sha256:1a6477c6e614ac4afb7b4d41dbba791064c8ab7f020a25bb16585ee22e949381` |

专项测试运行两次，最终均为 17/17 通过，耗时分别为 26.093 秒和 26.506 秒。覆盖真实 Dataset
publication、真实 Sampler runtime、BuiltIn immutable archive、Graph/Pipeline 编译、三周期真实
Backtest、可替换 Signal Graph、状态化账户和抽象 Analysis。`git diff --check` 通过，旧 v1 DataKey/ID
扫描只剩内部 wire 名称和一条故意拒绝 `protocolVersion=1.0.0` 的负向测试。

## 2. 确定性计算

Dataset/Sampler oracle 使用 3 个 CSV、共 5 行：day/QQQ 两行、day/SPY 两行、week/SPY 一行。
`decisionPeriod=day` 的时间并集严格得到三个 cycle：

1. `2026-01-02T21:00:00Z`
2. `2026-01-05T21:00:00Z`
3. `2026-01-06T21:00:00Z`

第二个 cycle 中 QQQ 没有同日新 bar，因此仍读取不晚于 decision time 的 1 月 2 日 bar；week/SPY
也保持 1 月 2 日的最新可见完整 bar。这验证了“时间对齐”而不是前视填充。

Environment 独立 oracle 从 cash=1000 开始。第二周期执行 QQQ 目标 1、SPY 目标 2：

```text
QQQ notional = 1 * 22 = 22
QQQ fee      = 1 + 22 * 100 / 10000 = 1.22
SPY notional = 2 * 12 = 24
SPY fee      = 1 + 24 * 100 / 10000 = 1.24
cash         = 1000 - 22 - 1.22 - 24 - 1.24 = 951.54
equity       = 951.54 + 1 * 21 + 2 * 13 = 998.54
```

第三周期只把 SPY 目标改为 0，实际卖出 2 股，fee=`1 + 28*1%=1.28`；QQQ 保持 1 股：

```text
cash   = 951.54 + 28 - 1.28 = 978.26
equity = 978.26 + 1 * 23 = 1001.26
```

真实 Backtest oracle 从 cash=100000 开始，首周期只生成 QQQ/SPY 各 2 股的获批意图，不成交；
第二周期在 open 60/200 执行上一周期意图：

```text
cash(t2)   = 100000 - 2 * 60 - 2 * 200 = 99480
equity(t3) = 99480 + 2 * 71 + 2 * 301 = 100224
```

第三周期没有新增成交，Result 中存在 `price`、`portfolio.account`、`execution.orders` 和
`intent.approved`，且不存在协议强加的 `analysis.*`。这同时验证了 t 的 intent 最早在 t+1 执行。

## 3. 解释、反证与不确定性

当前协议的递归结构明确包含多个 period 与 instrument，因此选择多标的 profile，并用 major v2 隔离
旧单标的草案。旧 v1 Basic Workflow 源资源被新 ID 取代；已经归档在某个 release 中的不可变资源
不会被本次工作树编辑删除，但全新 release 不会再由 installer 安装旧 v1 组合。这是发布前唯一需要
产品确认的兼容策略。

协议对 Constraint 保持抽象；`basic-absolute-position-map-constraint` 只是可替换的具体下级实现，
不能解释成协议只允许绝对仓位限制。Analysis 同理：协议 registry 不指定 Analysis ID，E2E 仅使用
Engine 的 neutral Analysis 完成运行请求。`basic-multi-asset-paper-environment` 是 executionPeriod=day
的具体 preset，底层 Module 本身接受任意合法 period；其他执行周期应发布另一普通 Environment Graph。

下列现象任一出现即可推翻本次“因果闭环成立”的结论：Sampler 输出未来 bar；首周期出现订单；
`intent.approved(t)` 在 t 成交；缺失 execution/valuation bar 时静默继续；Analysis 产生协议未声明的
固定指标；或 `engine/**` 出现本次 diff。现有负向测试覆盖 v1 descriptor、非法 OHLC、缺失执行 bar
和不兼容 wiring，均按预期 fail closed。

按仓库 README 的正确根目录命令运行全库测试，结果为 873 tests / 863 passed / 5 failed /
1 error / 4 skipped，耗时 817.749 秒。六项异常均不在本次应用层 diff：两项是
`engine/archive/dataset.py` 现有 822 行超过 800 行阈值；其余分别是 Engine owner 测试缺少并行
Agent 新增参数、sandbox 错误文本差异、job recovery 预期未抛错、Jupyter API 返回 400。按照用户
边界未修改 TradeEngine，也未代修并行进程负责的 Agent/Web 代码。

## 4. 单一下一实验或 Proposal

Proposal：先由用户确认“全新 release 仅安装 v2，还是同时保留旧 v1 BuiltIn ID”这一兼容策略；确认后
再以本报告列出的精确 v2 资源内容执行一次目标 release 的普通应用层发布，并用同一三周期 fixture
复核发布后 resource digest 与 `equity=100224`。本轮没有执行发布，也没有改变任何 Engine 语义。
