# Basic 指标可视化性能诊断

原始问题：`这个计算效率显然比Tradingview官方的低很多啊，这是什么原因`

问题时间：2026-08-27（会话未提供精确时分，America/New_York）
回答时间：2026-08-27 11:33:20 EDT（UTC-04:00）

## 1. 结论

判断成立。当前首轮实现是一条“正确性、隔离、可复现优先”的通用 Engine Result 投影链，并不是 TradingView 式的低延迟指标热路径。主要时间没有花在 SMA、EMA 或布林带的数学公式上，而是花在重复的 Result 全量读取、完整性校验、一次性进程启动、逐周期契约校验、JSON 序列化和整图重建上。

最主要的可避免问题是投影粒度错误：前端按 Visualizer 实例生成请求，而不是按 Pane 的公共依赖闭包批量计算。SMA 和 EMA 各一条线尚且各算一次；布林带虽然来自同一个 Module 实例，却因为 upper、middle、lower 是三个 `series.line`，被独立完整计算三次。

安全校验本身有合理价值，但“同一不可变 Result、同一 Module 配置、同一次图表渲染重复做五遍”没有必要。现状可概括为：安全成本是设计选择，重复成本是首轮接入缺陷。

## 2. 本地证据与确定性计算

`web/chart_core.js:1248` 为 Pane 中每个 Visualizer 单独建立 dependency plan；`web/basic_workflow_workspace.js:1001` 再用 `Promise.all` 为每个 plan 分别调用 Result API。缓存键在 `web/basic_workflow_workspace.js:982` 中包含 `visualizerId`，而每次指标修订确认后又在 `web/basic_workflow_workspace.js:1150` 清空整个投影缓存。

服务端只要请求包含临时 Module，就会从 `engine/service/result_projection.py:40` 进入一次性 Result Runtime。`engine/runtime/result_runtime.py:543` 显示每次请求都会创建临时目录、写完整 spec，并在 `engine/runtime/result_runtime.py:596` 启动新的 `python -m engine.worker.result_runtime` 进程。

每个 worker 在 `engine/runtime/result_stream.py:654` 从第一周期遍历到最后周期。读取器同时计算整份归档 SHA-256，只有读完整档才能确认摘要。每个周期先执行基础 Result validator，再逐个调用临时 Module，最后对加入派生数据后的完整 Data Dict 再做一次 validator；对应代码位于 `engine/runtime/result_projection.py:96`。

设历史周期数为 `N`，最终同时显示 Candles、SMA、EMA 和三条布林带时，单次重绘的确定性工作量如下：

| 项目 | 当前实现 | Pane 合并投影的理论下限 |
|---|---:|---:|
| HTTP Result 投影 | 6 次 | 1 次 |
| Result 整档读取与摘要校验 | 6 次 | 1 次 |
| 一次性 Python worker | 5 个 | 1 个 |
| 收盘价选择器调用 | `5N` | `N` |
| 指标 Module 调用 | `5N` | `3N` |
| 临时 Module 总调用 | `10N` | `4N` |
| 布林带完整历史计算 | 3 遍 | 1 遍 |

六次投影由一次 Candles 基础投影、一次 SMA、一次 EMA 和三次 Bollinger 输出投影组成。它们虽并发启动，但会竞争相同归档 I/O、CPU、进程启动和 JSON 解析资源，而且 `renderCurrentChart` 要等全部 promise 完成才开始绘图，因此慢请求决定可见延迟。

公式实现也不是最优，但属于次级因素。`builtin_implementations/pipeline/sma_indicator.py:11` 每根 bar 都重新 `sum(window)`；`builtin_implementations/pipeline/bollinger_bands_indicator.py:18` 每根 bar 都重新遍历窗口计算均值和方差；`builtin_implementations/pipeline/common.py:22` 还使用 `list.pop(0)`。这使 SMA/BB 为 `O(N × period)`，而不是维护滚动和的 `O(N)`。period 为 20 时，这部分通常仍小于五个 Runtime 的重复启动、校验和归档扫描。

绘图端也采用全量路径：`web/basic_workflow_workspace.js:1484` 销毁并重建图表，`web/chart_core.js:2834` 对每条序列调用 `setData` 写入全部点，而不是只挂载新增指标或更新最后一根 bar。

## 3. 与 TradingView 的可证实差异

TradingView 没有公开其官方指标服务的全部内部实现，所以不能严谨声称它一定使用某种语言、SIMD、WASM 或特定服务器架构。可以从官方文档确认三点：

1. Pine 首次加载时按历史 bar 顺序执行，并把每根 bar 必需的状态提交到内部 time series；实时阶段只针对最新 bar 的更新继续执行。
2. 相同 ticker、timeframe、输入参数等唯一配置的脚本结果通常会临时缓存，再次使用相同配置时可直接加载缓存，而非总是重跑全部历史。
3. TradingView 的 Lightweight Charts 官方 API 明确区分全量 `setData` 与增量 `update`，并提示频繁用 `setData` 替换全部数据会显著影响性能。

官方依据：[Pine Script execution model and caching](https://www.tradingview.com/pine-script-docs/language/execution-model/)，[Lightweight Charts incremental update](https://tradingview.github.io/lightweight-charts/docs/next)。

因此，能够确认的差异不是“TradingView 不按 bar 计算”，而是它会保存时序状态、复用相同配置的结果，并为最新数据提供增量更新路径。当前 Basic 首轮则在每次开关后清空缓存，从头验证历史，并按输出线重复计算。TradingView 私有实现是否还有编译、向量化或分布式缓存优势，只能视为合理推断，不能当作已证实事实。

## 4. 瓶颈排序与单一下一实验

按预期收益排序：

1. P0：把一个 Pane 的 Candles 和全部指标输出合成一个 Result projection。现有后端请求已经支持多个 paths 和多个临时 Module，不必改指标语义；理论上可将 6 次归档扫描降到 1 次、5 个 worker 降到 1 个、`10N` 次 Module 调用降到 `4N`。
2. P0：投影缓存改用不可变 Result digest、精确 Module 身份、config、bindings 和 paths 组成的内容键；Visualization revision 变化时只失效改变的依赖，不再无条件清空全部缓存。
3. P1：保留现有 chart/series，新增或删除指标时只改相关 series；新 bar 使用 `series.update`，不再 dispose chart 后对所有序列 `setData`。
4. P2：在不改变输出语义的前提下，将 SMA 和 BB 改为滚动和/滚动平方和，或增加受契约约束的批量时序执行接口。公式层优化应排在去重之后。

单一下一实验：只实施“Pane 合并投影”，其余代码和语义保持不变；对同一不可变 Result、同一三指标组合各运行冷启动 5 次，分别记录浏览器请求数、worker 数、归档扫描时间、Module compute、validator、序列化、网络与 draw 时间，并与当前基线比较中位数。通过条件是请求从 6 降到 1、worker 从 5 降到 1、输出逐点完全相等，并且总延迟至少下降 50%。在获得这组数据前，不应先改公式或牺牲 Engine 校验边界。
