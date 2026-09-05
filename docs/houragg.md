# 美股分钟线聚合为 TradeEngine 小时线

原始问题：那就自行聚合呗，保证正确即可

问答时间：2026-09-02 04:27:13 EDT（UTC-04:00，America/New_York）

## 1. 已确认事实

已实现 `scripts/prepare_us_hourly.py`，把一个 ticker 的 1 分钟 OHLCV CSV/ZIP 转换成 TradeEngine Basic Workflow v3 的小时 Dataset draft。它只写普通 workspace，不写 Engine 管理的 Dataset 根，也没有发布 Dataset Version。

转换契约如下：

- 输入时间戳按供应商本地的 **1 分钟区间起点**解释。FirstRate 官方转换工具也明确说明其聚合 bar 使用区间起点标记，例如 09:00 的 5 分钟 bar 覆盖 09:00–09:04；免费样本页说明股票数据采用美国东部时间且零成交量 bar 会省略。[FirstRate 转换工具](https://tools.firstratedata.com/)、[免费样本说明](https://firstratedata.com/free-intraday-data)
- 交易日和开闭市时间来自固定版本 `exchange_calendars==4.13.2` 的 XNYS 日历。该项目把 XNYS 定义为纽约证券交易所 regular trading calendar，并提供可查询的 session open/close。[exchange_calendars 文档](https://github.com/gerrymanoim/exchange_calendars/blob/master/README.md)
- 每个 session 从开盘时刻开始按 60 分钟分桶：正常日为 09:30–10:30 至 15:30–16:00，共 7 桶；13:00 提前收盘日为 4 桶，最后一桶 12:30–13:00。
- 聚合规则固定为 `open=first`、`high=max`、`low=min`、`close=last`、`volume=sum`。不插值、不前向填充；重复、乱序、非法 OHLCV、缺失整日或缺失整桶都会失败退出。
- 输出 `eventTime` 是 bar 结束时刻；`time` 是最早可用时刻，默认等于 bar 结束时刻，可通过显式非负 lag 后移。输出列严格为 `time,eventTime,open,close,high,low,volume`。
- 所有本地 naïve 时间先按 `America/New_York` 定位，再转换成 UTC，因此 DST 不通过手写固定偏移处理。

本次生成的 draft 位于 `artifacts/dataset-workspaces/aapl-hourly`，包含原始 ZIP、派生 CSV 和完整 `evidence.json`。这是测试数据，未注册或发布到 Engine。

## 2. 确定性计算

输入是 2026-09-02 下载的 FirstRate 官方免费 AAPL 1m 样本：

- 原始 ZIP：2,419,183 bytes；SHA-256 `c1015f2b8c3bce28a9e343b437273d6d01b325bd47a37b1c7367b84b9777412e`。
- 输入共 193,829 行，范围 `2022-09-30 04:00:00` 至 `2023-09-29 19:49:00`，时间严格递增、无重复、OHLCV 全部有效。
- XNYS 日历共 251 个 session；日历范围的规范化 SHA-256 为 `0965b79b669e7abf878d38ef7820bd29f72b0bb8967d01bdd30c15939780aba6`。
- RTH 理论分钟数为 97,530；样本 RTH 实际也是 97,530，缺失为 0。另有 96,299 行盘前/盘后数据被确定性剔除。
- 两个提前收盘日是 `2022-11-25`、`2023-07-03`。因此输出数为 `249 × 7 + 2 × 4 = 1,751` 根小时 bar。
- 输出 CSV SHA-256 为 `06a0ddbeed7f0ea00d32d3447d53c1380916bb2900b4772ce4caf5bd680f3c66`；Basic Workflow v3 conformance 通过，首个可用时间为 `2022-09-30T14:30:00Z`，最后一个为 `2023-09-29T20:00:00Z`。
- 用相同原始 ZIP 在独立 workspace 重跑，输出 SHA-256 仍为 `06a0ddbeed7f0ea00d32d3447d53c1380916bb2900b4772ce4caf5bd680f3c66`。
- 另写独立复算检查逐 session 枚举每一分钟，并逐 bar 比对 `eventTime,open,high,low,close,volume`；1,751 根 bar 的字段差异和时间戳差异均为 0。

测试执行结果：新增聚合器测试 5 项，加既有 v3 Dataset/Sampler 测试 3 项，共 8 项全部通过。覆盖正常日、半日市、DST 切换、扩展时段剔除、ZIP member 选择、v3 conformance，以及重复/乱序/非法 OHLC/缺桶的 fail-closed 行为。

## 3. 解释、反证与不确定性

- “聚合正确”不等于“原始价格口径适合正式回测”。免费 AAPL 样本页面没有对这个具体 ZIP 给出可验证的复权版本标识，因此本次 evidence 明确记录为 `provider-undocumented`。它适合验证接入与聚合，不应冒充未复权的正式研究数据。购买后应明确选择 `unadjusted` 1m，并用对应参数重新生成。
- 默认 `time == eventTime` 表示市场 bar 在最后一分钟结束时即可用于下一次决策，不表示 FirstRate 的历史文件当时已经发布。若研究的是“通过该供应商文件实际可得”的延迟，应依据合同或端点文档设置 `--availability-lag-seconds`；不能用当前下载时间替代历史可用时间。
- XNYS 日历来自固定版本的开源交易日历，而不是 NYSE 的逐日签名数据产品。为了防止库升级悄悄改变历史，本次同时记录版本和完整 schedule digest。遇到供应商记录与日历冲突时，脚本拒绝缺失 session/bucket，由人工核对临时休市或供应商缺口。
- 当前规则面向美国股票 regular session，不把盘前盘后混入同一小时，也不适用于 24 小时资产、期货或其他交易所。NASDAQ 美股在常规股票交易日可使用同一 XNYS session 约定，但跨市场产品必须更换准确日历。
- FirstRate 声明零成交量 bar 会省略。本次 AAPL RTH 恰好每分钟都有 bar，因此没有触发该差异；未来某 ticker 若整小时无成交，脚本会拒绝生成虚构 OHLC，而不是前向填充。

## 4. 单一下一实验或 Proposal

下一步只做一个同构扩展：取得用户指定 ticker 的 **未复权 FirstRate 1m ZIP**，对每个 ticker 分别运行同一脚本，生成独立的 Basic v3 Dataset workspace，并按此次相同门槛核对原始哈希、XNYS schedule digest、完整 session/bucket、OHLCV、DST、半日市、重跑哈希和 conformance。

所需输入只有准确 ticker 清单和对应下载文件或授权链接。输出仍保持为 draft；待各 ticker 的 evidence 通过并由用户确认后，再通过正常 UI 让 Engine 验证和提交不可变 Dataset Version。
