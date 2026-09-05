# Snapshot 与 DataKey 缓存问题

## 1. 问题与结论

用户原始问题（原文）：

> 我很奇怪为什么非要即时跑Pipeline和回测，按理来说snapshot下载下来，data dict跑一次不就行了，为什么非得每次跑，这说明就是缓存没做好，没有任何别的理由

- 问题/分析记录时间：2026-08-28T12:15:10-04:00（America/New_York；会话不提供消息自身时分，以本轮首个诊断进程时间记录）
- 回答/报告完成时间：2026-08-28T12:17:33-04:00（America/New_York）
- 分析基线：`release/basic-subsystem-v1`，提交 `cae76fbf4578081a47a37f036b7beae46f2fb9c3`

这个判断基本正确。严格地说，snapshot 下载后还必须由指定版本的 Sampler 跑一次，才能从 provider bars 得到正式的 cycle、decisionTime 和 DataKeys；但对于相同 Dataset 内容、Sampler 版本和参数，这一步只应执行一次。之后打开图表、添加指标和执行 Backtest 都应复用同一个不可变 DataKey timeline。

当前系统之所以即时跑 Pipeline 和完整 Backtest，不是数据正确性所必需，而是 Visualizer 目前只能以 completed Backtest Result 作为权威数据源。缓存又建在 `backtestId/Result` 层，没有建在 `Dataset + Sampler → DataKey timeline` 层。因此，这是资源模型和缓存层级的问题，不是指标计算或行情 snapshot 的必然要求。

## 2. 当前代码实际缓存了什么

现有系统有三类名字相近但含义不同的缓存：

| 名称 | 实际内容 | 何时产生 | 能否避免首次 Backtest |
|---|---|---|---|
| bar snapshot | provider bars 发布后的 immutable Dataset Version | 星标后台任务 | 不能 |
| materialization cache | Dataset/Pipeline/Sampler/Environment/Analysis 引用，以及已提交 Backtest Job/Backtest ID | 首次 `open_instrument()` | 不能；它是在提交 Backtest 后才记录 |
| projection cache | completed Backtest Result 的 paths/temporary Signal 投影结果 | Backtest 完成并首次 projection 后 | 不能 |

关键代码事实如下：

1. `market_service._capture_snapshot_job()` 只执行下载、`_publish_dataset()` 和 `_record_published_bar_snapshot()`。它没有运行 Sampler、没有生成 cycles/data，也没有创建 Backtest。
2. 对应测试名称明确是 `test_watchlist_snapshot_jobs_publish_bars_without_a_backtest`。这正是当前产品行为，而不是偶发 cache miss。
3. `open_instrument()` 虽然会先读取已保存 Dataset，但如果没有当前 owner 的 materialization record，就进入 `_uncached_open_instrument()`。
4. `_uncached_open_instrument()` 随后解析 managed Pipeline、Sampler、Environment、Analysis，prepare submission，并调用 `job_manager.submit()` 创建完整 Backtest。
5. 只有上述工作完成并返回后，`_record_materialization_cache()` 才把 Backtest Job/ID 写进 `materializations.json`。
6. `project_result_cached()` 要求该 owner 已拥有一个 materialized Backtest，而且 Backtest 必须是 completed immutable Result。cache key 还直接包含 `backtestId` 和 `resultContentDigest`。

所以当前页面显示的 `Snapshot cached` 只代表“K 线已下载并发布成 Dataset”，不是“图表所需 DataKey 已经物化”。这两个状态在 UI 上被表达得过于接近，也加深了缓存已经准备好的错觉。

## 3. 为什么仍会重复运行

当前 materialization cache 的主索引包含 owner digest、provider、instrument 和 period，并比较 data digest。它能保证同一 owner 在同一内容上复用已经创建的 Backtest；前一轮实测的 0.47–0.59 秒热打开就是这个缓存有效的结果。

但它有三个结构性限制：

1. 第一次打开之前，自动 snapshot 没有 DataKey materialization，必然提交一次完整 Backtest。
2. 缓存的核心对象是 Backtest Job，而不是 Sampler 输出。同一份行情只要换 owner，现有测试就明确要求创建另一个 Backtest。
3. projection 也绑定具体 `backtestId`；即使两个 Backtest 的 Dataset、Sampler 和输出 data 完全相同，也不能共享同一个 projection artifact。

因此“每次跑”需要区分：稳定 owner、稳定 snapshot、已成功完成过 Backtest 时，当前代码会命中；首次打开、另一 owner、内容变化、失败 Job 或 materialization identity 失效时，都会重新提交。无论是哪一种，根本问题都是没有可独立复用的 Sample/DataKey Result。

## 4. 正确的缓存层级

应把正式资源链改成：

```text
Provider snapshot
  → immutable Dataset Version
  → Engine Sampler
  → sealed Sample Result / DataKey Timeline
       ├─ Candles Visualizer
       ├─ temporary Signal → Indicator Visualizer
       └─ Pipeline + Environment + Analysis → Backtest Result
```

其中 Sample Result 的内容寻址 key 至少包含：

```text
Engine release identity
+ protocol/profile version
+ Dataset Version/content digest
+ Sampler ID/version/definition digest
+ Sampler parameters
```

它应保存：

- exact cycle IDs、decision/event/available times；
- Sampler 输出的 DataKey declarations；
- 每个 cycle 的正式 `data`；
- Dataset、Sampler 和 protocol lineage；
- canonical content digest、bar/cycle count 和边界时间；
- completed/failed 状态及原子发布证据。

相同 key 的并发请求只允许一个构建任务，其余等待同一任务。相同 snapshot 内容不做任何计算；内容改变才创建新的 Sample Result。旧版本保持不可变，直到没有引用后按明确保留策略清理。

授权和计算去重也应分开。用户是否有权读取某份行情仍由 access policy 判断，但在许可允许的范围内，相同 Dataset/Sampler 的纯计算 artifact 没有必要按 owner 重跑。至少在同一个 owner 内，更不能因为新浏览器 session 或重新进入页面而重算。

## 5. 星标后台任务应完成到什么程度

星标任务不应停在“下载并发布 Dataset”。正确流水线是：

1. 下载或检查 provider snapshot。
2. 比较稳定 data digest；无变化立即完成。
3. 有变化则发布新的 immutable Dataset Version。
4. 后台调用主 Engine Sampler，构建一次 sealed Sample Result。
5. 预建默认 Candles projection/Visualization materialization。
6. 状态只有到第 4 步完成后才显示 `Chart cache ready`；只完成下载时应显示 `Snapshot saved · chart cache building`。

打开图表时只读取已经完成的 Sample Result。若后台仍在构建，页面可以等待这个同一个任务，但不能另行提交重复计算。

添加指标时也不再运行 Sampler、Pipeline 或 Backtest，只执行：

```text
sealed Sample Result
  → temporary selector Signal
  → temporary indicator Signal
  → exact Result projection cache
  → Visualizer
```

指标参数或 Module version 改变时，只让对应 temporary Signal projection 的精确 cache key 变化，不使基础 K 线 timeline 失效。

## 6. 与主 Trade Engine 一致性的边界

这不是让网页直接读取 snapshot 画图，也不是在 Basic Subsystem 内实现第二套 Sampler。Sample Result 必须成为主 Engine 的一等资源，由正式 Dataset/Sampler contracts、归档身份、DataKey schema 和 Runtime 生成。

因此仍然满足：

- Candles 来自正式 Sampler 输出；
- indicator lines 来自 `Sampler → temporary Signal → Visualizer`；
- Backtest 使用同一个不可变 Sample Result，再运行自己的 Pipeline/Environment/Analysis；
- 缓存损坏或身份不匹配时 fail closed，不 fallback 到 provider raw bars；
- 不同 Dataset/Sampler/Engine release 不会错误复用。

当前“必须先有 Backtest Result 才能 Visualize”只是一项实现耦合，应当删除，不应把它解释为 Trade Engine 的必要语义。

## 7. 优先级修正

前一份性能方案把 Visualization 并行和 Projection Worker 放在较前位置。它们仍然有价值，但用户指出的问题优先级更高。建议调整为：

1. P0：主 Engine 的 Sample Result/DataKey timeline 资源与内容寻址缓存。
2. P0：星标 snapshot 后台构建 Sample Result，并把 UI 的 snapshot-ready 与 chart-cache-ready 状态拆开。
3. P1：Visualizer 和 temporary Signal projection 直接消费 Sample Result，不再要求 Backtest ID。
4. P1：Visualization save 与 indicator projection 并行、增量图表更新。
5. P2：只有实测仍证明进程启动是主要残差后，再考虑预热的一次性 Projection Worker。

在 Sample Result 尚未实现前，可以让星标后台任务提前提交现有中性 Backtest，把等待从前台挪到后台；但这只是过渡措施，仍然浪费 Pipeline/Environment/Analysis 计算，不能作为最终缓存架构。

最终结论：当前不是“完全没有缓存”，而是缓存建在错误的对象上。它缓存了已完成的 Backtest materialization，却没有缓存 snapshot 经 Sampler 得到的正式 DataKey timeline。用户提出的“snapshot 下载后 data dict 只跑一次”正是应当建立的核心不变量。
