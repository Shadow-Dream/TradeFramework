# Basic 图表与指标性能分析

## 1. 问题、结论与测试口径

用户原始问题（原文）：

> 现在我们关注加速问题。做一下性能分析，打开图表、添加indicator两件事情，耗时主要集中在什么地方。

- 问题/分析记录时间：2026-08-28T10:43:02-04:00（America/New_York；会话不提供消息自身时分，以本轮首个诊断进程时间记录）
- 回答/报告完成时间：2026-08-28T11:01:06-04:00（America/New_York）
- 被测版本：`release/basic-subsystem-v1`，`cae76fbf4578081a47a37f036b7beae46f2fb9c3`
- 测试对象：120 根确定性日线、真实 HTTP Handler、真实 Dataset/Pipeline/Backtest、真实 Backtest worker、真实临时 Signal Result Runtime、真实 Visualizer compiler 和无头 Chrome。
- 唯一替换边界：外部行情 Provider 使用本地确定性 fixture，避免把供应商网络波动算成 Engine 耗时。三次独立冷环境用于延迟中位数，第四次读取 Result 自带的 `executionChain.timings`。

结论很明确：

1. 首次打开图表的主耗时是完整 Backtest 冷物化，不是浏览器画 K 线。中位数 5,999 ms 中，Backtest worker 本身为 4,085 ms；其中 2,886 ms 又集中在 kernel preparation。
2. 首次添加 BB 的主耗时是临时 Signal Result projection。中位数 1,146 ms 中，Result projection HTTP 为 689 ms，Visualization 保存/编译为 285 ms，前端规划与重画为 113 ms。
3. 布林带数学本身不是瓶颈。120 bars 的 selector + BB 完整 SDK 调用微基准为 1.921 ms，BB `update()` 计算仅 0.669 ms，只占首次添加总延迟约 0.058%。
4. 缓存已经有效：同标签页热打开为 474 ms；浏览器结果缓存命中后重复添加 BB 为 415 ms。冷路径慢、热路径接近交互目标。

## 2. 确定性计算

### 打开图表

三次独立冷打开为 5,997、5,999、6,054 ms，中位数 5,999 ms。按页面状态和服务端计时分解如下：

| 阶段 | 中位耗时 | 占冷打开 | 说明 |
|---|---:|---:|---|
| 页面导航、认证、Catalog 及其他残差 | 470 ms | 7.8% | 总时间减去以下串行状态阶段；Catalog 请求与页面资源存在并行 |
| 创建资源至 Job queued | 759 ms | 12.6% | `/instruments/open` 中位数 757 ms |
| queued 至完成 Result 验证 | 4,335 ms | 72.3% | 包含 4,085 ms Backtest worker 和约一个轮询检测间隔 |
| Result view 验证至开始持久化 | 14 ms | 0.2% | Result view 与当前 Visualization 查询 |
| Candles Visualization 持久化至可见 | 420 ms | 7.0% | 保存/编译、Candles projection、图表规划和绘制 |

`/instruments/open` 的 757 ms 内部主要为：

| 服务端工作 | 中位耗时 |
|---|---:|
| Managed Pipeline 解析/创建 | 263 ms |
| Prepared Backtest 合同冻结 | 142 ms |
| Dataset 发布 | 61 ms |
| Job durable submit | 36 ms |
| fixture Provider 构造 120 bars | 0.81 ms |

Provider 数值仅用于隔离 Engine 成本；真实首次下载还会额外叠加外部网络延迟。它不能解释本实验中的 6 秒，因为这里 Provider 不到 1 ms。

Backtest worker 的 4,085 ms 可由封存 Result 的正式执行证据继续拆分：

| Backtest 内部阶段 | 耗时 | 占 Backtest | 占冷打开 |
|---|---:|---:|---:|
| Kernel preparation | 2,886 ms | 70.7% | 48.1% |
| Graph build | 49.7 ms | 1.2% | 0.8% |
| 120-cycle loop | 517 ms | 12.7% | 8.6% |
| 其余进程启动、Result 收尾、归档/目录验证 | 631 ms | 15.4% | 10.5% |

cycle loop 的 517 ms 中，Sampler 为 486 ms；Pipeline、Environment、Analysis 三个 cycle phase 合计只有 27.5 ms。ResultWriter 的显式 JSON encode + write 仅约 1.92 ms。也就是说，现有 Basic 中性 Pipeline 的业务计算只占很小部分；主要成本是不可变资源准备、Sampler 和 Runtime/Result 边界。

冷打开完成后的最后 420 ms 中，首次 Candles Visualization 保存请求为 173 ms，Candles Result projection 为 112 ms，浏览器 Visualization 规划、time info 和绘制合计约 88 ms。三者基本解释了这一尾段。

缓存路径对照：

| 打开场景 | 中位耗时 | Result projection |
|---|---:|---|
| 首次冷打开 | 5,999 ms | 112 ms，首次直接验证 Result |
| 同标签页重新打开 | 474 ms | 0 次 HTTP，命中浏览器 projection cache |
| 新标签页、服务器缓存已存在 | 589 ms | 23 ms，命中服务器 projection cache |

热打开时已经没有 Backtest。剩余最大的单请求是 `/api/subsystems/basic/market` 约 163–188 ms 和 Signal Module Catalog 约 125–150 ms；它们并行执行。Chart 规划/绘制约 59–63 ms，缓存 materialization open 约 39–40 ms。

### 添加 indicator（BB 20 · 2）

三次首次添加为 1,099、1,146、1,156 ms，中位数 1,146 ms：

| 阶段 | 中位耗时 | 占首次添加 |
|---|---:|---:|
| Visualization revision 保存与全规格编译 | 285 ms | 24.9% |
| 临时 Signal Result projection | 689 ms | 60.1% |
| 前端依赖规划、time info、整图重画 | 113 ms | 9.8% |
| DOM、事件和状态更新残差 | 59 ms | 5.2% |

Result projection 的服务端总时间为 677 ms，其中真正进入 `engine.result_projection` 的一次性 Runtime 为 538 ms。相同 120-bar Candles 的无临时 Module 直接 projection 仅约 72 ms；两者相差约 467 ms。该差值主要是临时 Module 计划/合同编译、隔离 Python 进程启动、Module archive 加载、逐 cycle 双层合同验证、整档摘要验证和投影 JSON 输出，而不是 BB 数学。

受控微基准重复 1,000 次，每次 120 bars：

| 计算 | 每 120 bars |
|---|---:|
| selector + BB 完整 SDK graph wall time | 1.921 ms |
| BB Module compute | 0.669 ms |
| selector Module compute | 0.205 ms |

完整 graph 数学/SDK 调用仅占 1,146 ms 的 0.168%；BB 公式本身占 0.058%。即使把 BB 公式优化十倍，端到端也几乎看不出变化。

缓存路径对照：

| 添加场景 | 中位耗时 | 主要剩余成本 |
|---|---:|---|
| 首次添加 BB | 1,146 ms | save 285 + projection 689 + chart 113 ms |
| 同标签页删除后重加 | 415 ms | save 279 + chart 93 ms；无 projection HTTP |
| 新标签页、仅服务器 cache 命中 | 580 ms | save 281 + cached projection 121 + chart 115 ms |
| 删除 BB | 259 ms | save 185 + chart 42 ms |

这也揭示了热路径的新瓶颈：projection 被缓存后，每次指标变更仍完整保存并编译整个 Visualization revision，然后 `renderCurrentChart()` dispose 全部 chart/pane，再为全部序列重新规划和绘制。缓存不能消除这两个固定成本。

## 3. 解释、反证与不确定性

代码路径与计时结果一致：

- `web/basic_workflow_workspace.js::openInstrument()` 先调用应用 open，随后等待正式 Backtest Job、读取封存 Result view、保存/加载 Visualization，再 projection 和绘制。
- `engine/worker/backtest_execution.py` 把 kernel preparation、graph build、cycle phases 和 ResultWriter 计时写进不可变 Result；本报告的 Backtest 内部分解直接读取该正式证据，不是浏览器猜测。
- `web/basic_workflow_workspace.js::persistIndicatorMutation()` 当前串行等待 `saveVisualizationRevision()`，确认成功后才调用 `renderCurrentChart()`；projection 因而不能与 save 重叠。
- `engine/runtime/result_runtime.py::write_result_projection_in_runtime()` 为含 temporary Modules 的首次 projection 创建临时目录和一次性隔离 Python 进程。
- `web/basic_workflow_workspace.js::renderCurrentChart()` 等待所有 Pane projection 后调用 `disposeChart()`，重建所有 Pane 和 series。指标没有直接拿原始数据画，但这种正确性优先的全量路径带来固定成本。

可以排除的错误解释：

- 不是 Provider 慢：本实验 Provider 只有 0.81 ms，冷打开仍约 6 秒。
- 不是 BB 公式慢：BB compute 只有 0.669 ms/120 bars，冷添加仍约 1.15 秒。
- 不是缓存完全无效：热打开从约 6 秒降到 0.47–0.59 秒，重复添加从约 1.15 秒降到 0.42–0.58 秒。
- 不是前端 Canvas 单独造成 1 秒：冷 BB 前端规划/绘制约 113 ms，最大的 689 ms 在 Result projection。

仍需保留的边界：

- 这是同一预览机器上的本地 HTTP、120 bars、三次独立冷样本，不是生产容量测试或 p95。绝对毫秒数会随机器负载变化，但三轮冷打开仅相差 57 ms、冷 BB 仅相差 57 ms，瓶颈排序稳定。
- fixture 排除了真实行情网络；真实未缓存 Provider 只会增加冷打开时间，不会减少 Engine 成本。
- Backtest 的 `kernelPreparationSeconds` 是正式聚合阶段，包含冻结资源/合同验证、Dataset/Sampler Runtime 准备及 provider count 等工作。本轮没有修改 worker 来侵入式细分该阶段，因此不把 2.886 秒武断归因于某一个函数。
- 对 TradingView 的内部实现没有可观测权限。能确定的是本系统采用不可变资源、CAS 保存、隔离临时 Runtime 和全量重画；不能把 TradingView 的具体缓存、编译或执行技术当作已证实事实。

## 4. 优先级与单一下一实验

按当前数据排序：

1. 打开图表 P0：优化/复用 Backtest kernel preparation。它单独占冷打开约 2.886 秒；其次才是 486 ms Sampler。Pipeline 数学只有几十毫秒，不应优先优化。
2. 添加指标 P0：减少一次性 temporary Result Runtime 固定成本，或确保内容寻址 projection 在交互前已经预热。首次 projection 占 689 ms，缓存后可降到 0–121 ms。
3. 添加指标 P0：在保持“只有 CAS 确认后才展示”的语义下，并行执行 Visualization save 与 Result projection。目前二者串行，冷 BB 理论关键路径可从 `285 + 689` ms 降为约 `max(285, 689)` ms。
4. 热路径 P1：按 Visualization delta 增删 series，不 dispose/rebuild 整张图；当前热 BB 仍有约 93–115 ms 图表重建。
5. 热打开 P1：缓存或收窄 Module Catalog 与 market state 投影；二者是热打开的最大初始请求，但远小于冷 Backtest。

建议只做一个下一实验：不修改 Engine 合同和确认语义，仅让“Visualization CAS save”和“相同 draft 的 Result projection”并行；两者都成功后才安装 canonical revision 并显示，任一失败则丢弃投影。以同一 120-bar BB 连续跑 20 次，要求输出逐点完全一致、CAS conflict 不显示未确认图层、冷添加 p50 从约 1.15 秒降到 0.9 秒以内。这个实验改动面小、预计收益约 250–300 ms，也能验证下一步是否真的需要更大的持久 Result Runtime 设计。
