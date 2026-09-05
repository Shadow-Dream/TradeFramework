# Trade Engine Projection Worker 实施与性能报告

用户原始问题（原文）：

> 那肯定是要这样做啊。但是这个功能不应该是子系统功能，应该在tradeEngine内改。TradeEngine内的visualizer也面临类似问题。

- 问题记录时间：2026-08-28 15:01:37 EDT（UTC-04:00；会话未提供消息级时间，采用本轮首个实现文件落盘时间）
- 回答完成时间：2026-08-28 15:39:27 EDT（UTC-04:00）
- 代码基线：`cae76fbf4578081a47a37f036b7beae46f2fb9c3`
- 本轮相关源码摘要：`sha256:9339af5732c8a3faa0d1f03dcf75bfabeb59c68b536ca0186946920efa8f3ec9`
- 最终性能记录：`/tmp/proj-perf11.json`，`sha256:0371c0c8f695da45d25844e4e1e4daa10651db982768b32b0736a996eb9683ee`

## 结论

Projection Worker 已实现为 Trade Engine 的通用 Result Runtime 能力，不属于 Basic 子系统。Backtest Result Visualizer 与 Sample Result Visualizer 都通过同一个 Engine session `result:projection-worker` 请求投影；Basic 只调用正式 Sample Result 服务，不拥有 Worker、进程隔离或帧缓存语义。

常驻的是只处理可信 Engine 数据的 Worker 和已验证基础帧。每次包含临时 Signal Module 的 projection 都 fork 一个一次性执行器：执行器创建全新的 `ResultCycleProcessor`、`ModuleInvoker`、Module instance 和 execution root，完成 `initialize → invoke* → finalize → close` 后退出。执行器是 Linux subreaper；若 Module 留下仍可运行的后代进程，Engine 强制清理并将本次 projection 判为失败。用户 Module 的 Python 全局状态、环境修改和子进程不能进入下一请求。

## 正式数据路径

```text
sealed Backtest Result ─┐
                        ├─> Engine Projection Worker
sealed Sample Result ───┘       ├─ 校验归档身份并缓存只读基础帧
                                └─ fork 一次性 projection executor
                                      └─ fresh Signal Modules
                                           └─ Result slice
                                                └─ Visualizer Core
```

Sample Result 本身仍由正式 Dataset Version 与 Sampler 物化。指标仍从 `Sampler → Sample Result Data Dict → temporary Signal → Result projection → Visualizer` 产生；浏览器没有用原始行情直接画指标，也没有前端指标公式。性能实验确认 Backtest 数量为 0，因为图表投影不需要策略 Backtest。

## 缓存与一致性边界

1. 冷请求仍校验 Result 的 sealed directory、文件大小、内容摘要、metadata、cycle schema、cycle ID 唯一性和前后文件身份；成功后才允许基础帧进入 Worker 私有缓存。
2. 每次命中都会重新核对 archive inode/size/mode/mtime/ctime 身份。身份变化会精确失效，不会继续使用旧帧。
3. 缓存帧从不交给 Module 直接修改。投影只复制 Module 输出会触及的容器路径；输入通过 Engine 的 validated-input authority 隔离，输出仍逐端口验证。
4. 缓存只保存基础帧，不保存 Module object、Signal 状态、cycle cursor 或 temporary output。专项测试还将新路径与旧 disposable Result Runtime 的 JSON 输出 exact-equal 比较。
5. 内存缓存有界：单个源归档上限 16 MiB、总源归档预算 32 MiB、最多 8 项 LRU。超过门限的 Result 在同一个隔离 Worker 下走正式流式投影，不存在另一套公式或隐藏 fallback。
6. Worker 由 Engine `ProcessSessionRegistry` 监管并纳入 `shutdown_result_runtimes()`。通信失败会终止该 Worker；当前请求明确失败，下一请求可建立新 Worker，不会自动重放未知状态的请求。

## Visualizer 通用优化

性能剖析发现 Backtest 页面、Standalone Chart 和 Basic Workspace 都会为同一 Pane 重复执行 renderer prepare：`paneTimeInfo()` 一次，`drawFinancialPane()` 再一次。现在由 `TradeChartCore.prepareFinancialPane()` 只准备一次，并把同一个一次性内部证明交给时间轴与绘图阶段；三套 Visualizer 调用方全部接入。这个改动不缓存绘图对象，也不改变 renderer 输出。

## 确定性性能结果

测试条件：2,500 根确定性日线、BB(20, 2)、真实 Chromium、正式 HTTP Handler、正式 Engine Module archives、正式 Sample Result/Visualization 路径。对比使用同一性能驱动改造前后的记录。

| 指标 | 改造前 | 最终 | 改善 |
|---|---:|---:|---:|
| 首次完整添加 BB | 1,797 ms | 1,077 ms | 40.1% |
| 首次 projection HTTP | 1,048.2 ms | 636.9 ms | 39.2% |
| Engine 隔离 projection | 718.4 ms | 315.3 ms | 56.1% |
| 同页删除后重加 BB | 563 ms | 415 ms | 26.3% |
| 新页面命中持久 projection 缓存 | 844 ms | 667 ms | 21.0% |

最终 Worker 状态为 `requests=2, cacheMisses=1, cacheHits=1`：后台基础图投影完成一次校验/预热，第一次 BB 临时 Signal 直接命中基础帧。输出 Result slice 为 803,523 bytes，实验期间创建 Backtest 数量为 0。

## 验证

- 最终组合回归：83 passed，覆盖 Result Runtime、Repository、Backtest E2E、Sample Result 图表物化以及三套网页 Visualizer。
- Projection Worker 专项复跑：3 passed；覆盖 LRU/身份失效、基础投影预热、连续请求 Signal 状态重置、旧 disposable Runtime 等价、Backtest/Sample 共用同一 Worker、Worker 被杀后的重建。
- Web 回归：54 passed；Node 语法检查与 `git diff --check` 通过。
- 真实浏览器性能驱动完成 8 个打开/添加/删除场景，未出现浏览器断言失败。

## 剩余性能边界

首次 2,500-bar BB 已接近一秒，但还不是稳定低于一秒。当前最大剩余项是约 804 KB 的逐 cycle JSON Result slice：Worker 约 315 ms，HTTP 编码/传输/浏览器解析使 projection 请求约 637 ms；Visualization revision 保存约 141 ms。下一轮若继续优化，单一优先实验应是 Trade Engine 的列式 Visualizer projection 合同，或在保持 CAS 的前提下并行 Visualization 保存与 projection。两者都应继续使用同一 Signal/Visualizer 语义，不能退回前端公式或原始数据直画。
