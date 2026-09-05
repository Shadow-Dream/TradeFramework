# K 线打开与收藏预热优化报告

问题原文：

> 那没问题。现在先把打开K线这个事情的速度提到最高。并且我收藏的那些不要等我点进去了才开始准备，我点收藏那一刻就要加入队列帮我去准备缓存。当然，我如果点进去的话，计算需要提前到队列的最前面。

- 问题记录时间：2026-08-28 12:42:47 EDT（UTC-04:00）
- 回答完成时间：2026-08-28 13:55:49 EDT（UTC-04:00）
- 基线提交：`cae76fbf4578081a47a37f036b7beae46f2fb9c3`
- 本轮源码输入摘要：`sha256:cd75807d4832dd36af514d95342c3bcf59d0cf3c7009cb7124eb6c5c1de60366`

## 已确认事实

1. 点击收藏后，`set_watchlist()` 立即为新增品种建立持久化日线缓存任务；HTTP 子系统随后启动或唤醒后台 Worker。实现位置为 `application_protocols/basic_workflow/market_service.py:2797` 与 `application_subsystems/basic.py:51`。
2. 打开尚未完成的品种时，`open_chart()` 会标记该任务为交互优先；队列领取任务时先选交互任务，再按原始入队时间排序。实现位置为 `application_protocols/basic_workflow/market_service.py:1239`、`:1293`、`:3664`。
3. 已经开始运行的任务不会被强行中断；所选品种会排到所有尚未运行任务的最前面。这避免产生半写入 Dataset 或 Sample Result。
4. 后台任务执行的是正式 Engine 路径：Dataset Version → Sampler → 不可变 Sample Result/DataKey 时间线 → Visualizer 投影。它同时预热基础 Candle 投影并保存默认 Sample Visualization；没有为了画图启动 Backtest，也没有把原始 K 线直接交给浏览器硬画。实现位置为 `application_protocols/basic_workflow/market_service.py:1365`。
5. 热打开只验证当前品种的不可变 Sample Result，并直接读取缓存中的 K 线摘要；不再重新读取完整 Dataset，也不再逐一打开全部收藏品种的归档。旧缓存会在第一次打开时自动迁移。实现位置为 `application_protocols/basic_workflow/market_service.py:868` 与 `:3664`。
6. 大型市场快照和 22 个图表 Signal Module 的精确目录均加入带文件指纹失效机制的内存索引；工作区打开不再下载完整市场目录或完整 Signal Module 总表。实现位置为 `application_protocols/basic_workflow/market_service.py:2517`、`:2657` 与 `web/basic_workflow_workspace.js:2342`。

## 确定性计算

方法：在同一源码摘要上依次执行语法检查、服务测试、Engine Result 隔离运行时测试、网页契约测试、四股票隔离浏览器实验和真实预览三股票实验。

- 完整 Basic 服务回归：`22 passed in 275.63s`。
- 网页与子系统契约：`25 passed in 15.48s`。
- Engine Result/隔离临时 Signal 运行时：`34 passed in 134.90s`。
- 四股票队列实验从 0 个收藏开始，经浏览器依次点亮 AAPL、AMZN、MSFT、NVDA，随后立即打开 NVDA。供应商实际调用顺序为 `AAPL → NVDA → MSFT → AMZN`，证明 NVDA 从队尾升到当前运行任务之后的第一位。4 个任务全部完成，生成 4 份图表缓存，Backtest 数量为 0，浏览器控制台与 API 错误均为 0。
- 上述队列实验使用每只 120 根确定性 K 线，并人为给供应商增加 0.6 秒延迟；缓存完成后的三个完整出图耗时为 308ms、345ms、346ms。
- 真实预览使用 2,478–2,513 根日线。服务重启后的首次完整出图为 1.082 秒；随后两只热缓存完整出图为 0.731 秒和 0.811 秒。对应热 `charts/open` 接口为 97ms 和 103ms，模块目录热读取为 11ms 和 15ms。
- 全部浏览器实验均未访问 `/api/backtests`、`/api/backtest-jobs`、旧 `/instruments/open` 或旧全局 Visualization 接口。

## 解释、反证与不确定性

- “打开优先”指排队优先，不是杀掉当前正在写不可变资源的任务。若要抢占正在运行的任务，就必须增加可恢复检查点和事务回滚；当前没有用这种风险换取不到一秒的队列优势。
- 服务重启后的第一张图仍比后续图多约 0.27–0.35 秒，主要是首次校验 22 个精确 Module 归档和操作系统文件页缓存。目录内容未变化时，后续请求通过文件指纹缓存降到约 11–15ms。
- 真实供应商下载速度不受本系统控制；缓存尚未生成时，打开耗时仍包含供应商响应与一次 Sampler 物化。反证条件是：任务状态已经是 `completed` 且 Sample Result 身份不变时，系统仍触发供应商下载、Dataset 读取或 Backtest；现有单元测试和浏览器请求记录均未出现该情况。
- 本轮没有发布新的不可变 Module 版本，也没有改变 Sampler、Signal 或 Visualizer 的端口与计算语义；优化发生在缓存资源、队列和读取路径。

## 单一下一实验或 Proposal

下一轮只做一个生产式延迟实验：固定 30 个已经完成预热的真实股票，分别统计完整出图、`charts/open`、基础 Sample projection 的 p50/p95/p99。若 p95 仍高于 1 秒，再仅针对最大分量优化；不改 Engine 计算语义。
