# Trade Engine queued · 0% 二次故障报告

用户原始问题：

> Trade Engine queued · 0%
> 还是这样

- 提问时间：2026-08-28（会话未提供精确时分），America/New_York
- 回答时间：2026-08-28 08:24 EDT（UTC-04:00）

## 已确认事实

1. 本次第一失败现场是 META：Job `job_01M144G6FWMV8JE740SMP1VD3F`，Backtest `bt_01M144G6FWBNHVDD4F2ZW72C8Z`，Dataset `basic-market-acd53406b2ef6b3c7470cc16`，Pipeline `pipe_01M0Z5PMRPR8S77Y5SSJB0MDQ1`，snapshot hash `sha256:72e7835419f6f0ad766fb5f7136ae1487afb16b7c04fcd8913bffbb78a7d58aa`。
2. 该 Job 于 2026-08-28 08:11:19.932 EDT 提交，只有 `submitted` 事件，没有 `started` 事件；当时活动 running Job 为 0、最大并发为 1、磁盘可用约 23 GB。因此这次不是上次的磁盘写满故障。
3. 服务恢复时保留了该失败身份，并在 08:19:56.609 EDT 将其终结为 `failed / interrupted`，错误为 `Engine stopped before this Backtest job started.`；它没有被改写成成功 Result。
4. JobManager 原实现提交到 `ThreadPoolExecutor` 后立即丢弃 Future，没有开始超时、没有 Future 异常回收，也无法区分“合法等待并发槽位”和“没有 running Job 却无人领取”。这使单 worker 丢失调度能力时，持久 Job 可以永久停在 queued。
5. 现实现保存每个 Job 的 Future，并设置 2 秒 dispatch watchdog。仅当 Job 仍 queued、Future 尚未开始、且没有合法 running Future/Job 占用容量时，才取消未开始的 dispatch、替换失效 executor，并以相同 Job/Backtest/冻结 request 继续；不会创建第二个 Backtest，也不会并发重复执行。异常 Future 也会尝试把 Job 写成终态并记录错误。
6. 真实 Chrome 已使用 META 完成冷启动和第二个独立登录会话缓存重开。新 Job `job_01M1453K39VBYT77XQATQT6TJ4`、Backtest `bt_01M1453K3AFXD839CX1KG00PCP` 均已 completed；第二次打开复用相同身份并显示 `Loaded saved Visualization · Materialization cache hit`。
7. 完整 JobManager 与 submission 回归为 `30 passed`；Python 编译和 `git diff --check` 通过。服务当前 active，登录页 HTTP 200，活动 Job 数为 0。

## 确定性计算

- 旧 META Job 无 started 状态持续 `08:19:56.609551 - 08:11:19.932124 = 516.677427` 秒，即约 8 分 36.68 秒。
- 新 META Job 提交于 `12:21:55.434023Z`，开始于 `12:21:55.448753Z`：排队时间为 0.014730 秒。
- 新 META Job 完成于 `12:21:59.917235Z`：执行时间为 4.468482 秒，总提交到完成时间为 4.483212 秒，完成 2,512/2,512 cycles，progress=1。
- 冷启动点击到图表可用为 16.255 秒；第二个独立登录会话缓存命中后为 5.443 秒。两次返回完全相同的 Job ID 和 Backtest ID。
- 新旧 META 使用相同 Dataset ID 和 Pipeline ID；旧 Job 根本没有进入 Runtime，所以故障位于 dispatch 边界，而不是指标计算、Dataset 读取或 Pipeline cycle 执行阶段。

## 解释、反证与不确定性

已确认的直接原因是“持久 queued 记录没有对应的执行开始，而且管理器没有保存或监督 Future”，这是可复现的调度饥饿失效模式。测试用一个永不领取任务的 executor 确定性复现后，watchdog 在不更换 Job/Backtest 身份的情况下恢复并完成。

更底层的 ThreadPool worker 为什么在运行一小时后没有领取 META Future，旧进程没有暴露 Future 状态或 Python 线程栈，因此不能把它进一步断言为某一种 CPython 内部故障。竞争解释包括 worker 非正常退出、idle 信号与实际 worker 状态不一致，或前一任务完成后的清理阻塞。新的机制不依赖猜中这三者中的哪一个：合法 running 占满时只继续等待；没有 running 且 Future 仍可取消时才恢复。

成功的新 META 运行反证了“META 数据本身必然导致 queued”以及“当前 Pipeline 无法执行”。它没有证明任何策略收益或指标正确性；本次只验证调度、Result 完成和 Visualization 物化，没有改变 Engine 的 Pipeline/Environment/Analysis 执行语义。

你当前浏览器仍持有旧 META Job 的轮询状态。该 Job 已是不可变的 failed/interrupted；刷新当前 Workspace 后，应用会按失败缓存失效规则创建新 Job。刷新不是清除证据，也不会复活旧 Job。

## 单一下一实验或 Proposal

保持当前服务不重启，固定同一 META Dataset、Pipeline、Sampler、Environment 与 Analysis，间隔执行一组有界的 20 次冷/热打开；唯一验收量是任何“没有 running 占用”的 queued 区间不得超过 2 秒，并要求所有 recovery 事件保留原 Job/Backtest 身份。若出现一次超过 2 秒且没有 recovery 事件，则否定本次 watchdog 已覆盖线上失效模式。
