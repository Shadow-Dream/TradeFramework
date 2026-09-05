# Projection Worker 之后的性能优化项

## 1. 问题与时间

用户原始问题（原文）：

> 其他性能改进项呢

- 问题记录时间：2026-08-28 15:43:17 EDT（UTC-04:00；会话不提供消息自身时间，采用本轮开始分析时间）
- 回答/报告完成时间：2026-08-28 15:44:38 EDT（UTC-04:00）
- 性能证据：`/tmp/proj-perf11.json`，SHA-256 `0371c0c8f695da45d25844e4e1e4daa10651db982768b32b0736a996eb9683ee`
- 测试场景：2,500 根确定性日线、BB(20, 2)、真实 Chromium、正式 HTTP/Engine/Sample Result/Visualization 路径；Backtest 数量为 0。

## 2. 结论

还有较大的优化空间。常驻 Projection Worker 已经消除了最明显的重复 Result 读取与 Runtime 冷启动，但现在首次添加 BB 的 1,077 ms 已经由三个相近的部分组成：

| 部分 | 实测耗时 | 说明 |
|---|---:|---|
| 保存 Visualization revision | 140.6 ms | 当前先保存，成功后才发起 projection |
| temporary Signal projection HTTP | 636.9 ms | 其中 Engine Worker 为 315.3 ms，其余约 321.6 ms 是外围校验、对象组装、序列化和 HTTP 等成本 |
| 前端安装与整图更新等剩余关键路径 | 约 299.5 ms | 当前仍会销毁并重建全部 Pane、series 和 drawing 绑定 |

同页删除后重加 BB 已命中浏览器 projection cache，不再请求 projection，但仍需 415 ms；扣除 114.1 ms 的保存请求后，前端完整重建约占 300.9 ms。这证明下一阶段不能只继续压缩 Python 指标公式。

2,500 根 K 线上的 price selector + BB 完整 Module 调用实测约 41.2 ms。即使把公式变成零成本，也解决不了当前数百毫秒的结构性开销。

## 3. 优先级 P0：应当马上做

### 3.1 Trade Engine 列式 Visualizer Projection 合同

当前 BB projection 返回约 804 KiB 的 Result slice；HTTP 响应约 879 KiB。逐 cycle JSON 会重复携带嵌套 DataKey 路径、对象键和 cycle 外壳，而 Visualizer 实际只需要一条共享时间轴和少量序列。

建议在主引擎新增版本化的 `VisualizerProjectionV2`：

```text
result identity
shared time axis
series identity + number/null arrays
optional null bitmap / style-independent metadata
```

它仍由同一个 sealed Result、temporary Signal Module 和 Engine projection 产生；只是 Engine 在输出边界把已经校验的逐 cycle 数据编码成列，不允许浏览器重新计算指标。必须用现有逐行 projection 作为 oracle，逐点做 exact-equal 测试。

这是剩余 Engine 侧最有价值的一项：它同时减少服务端对象构造/JSON 编码、传输体积、浏览器 JSON 解析和前端 DataKey 遍历。具体收益需实现后实测，不能把约 321.6 ms 全部预先算作可消除成本。

### 3.2 Chart Core 增量 reconcile

当前添加或删除一个指标仍会 `dispose` 整张图，然后重新创建主 K 线、所有指标 Pane、series、drawing controller 和时间范围。应让通用 Chart Core 比较前后 canonical Visualization：

- 保留未变化的 chart、Pane、series 和 drawing controller；
- 只创建或删除本次变化的指标实例；
- 参数变化时只替换对应 series 数据；
- 保持 visible range、crosshair 和主图滚动位置；
- 一个 Pane 中最后一个 series 删除时才移除 Pane。

这不是 Basic 子系统特化。Backtest Visualizer、Standalone Chart 和 Basic Workspace 应调用同一个 reconcile API。它是热路径的最大改进项；当前证据显示即使 projection 完全命中缓存，仍有约 301 ms 可优化。把热添加目标设为 150–250 ms 是合理的工程目标，但还不是已实现结果。

### 3.3 Visualization 保存与 projection 并行

添加指标时，浏览器 draft 已包含精确的 temporary Module、config、bindings 和 projection paths。可以同时发起：

```text
CAS save Visualization ─┐
                       ├─ 两者成功且 revision/openSeq 仍匹配 → 安装图层
Engine projection ─────┘
```

若 CAS 冲突、用户删除指标、参数改变或页面切换，应取消或丢弃旧 projection，不能显示未被正式保存的图层。该优化不改执行语义，理论上可从首次路径隐藏约 100–140 ms 的保存等待，是实现成本较低的快速收益。

## 4. 优先级 P1：稳定性和多用户吞吐

### 4.1 Engine 通用 exact projection cache + single-flight

Basic 目前已有持久化的精确 projection cache，但它还不是 Trade Engine 所有 Visualizer 共用的一等能力。应将其上移到 Engine，key 至少包含：

- sealed Backtest/Sample Result digest；
- projection paths；
- temporary Module definition/version/archive digest；
- graph、bindings 和 exact config；
- projection 输出合同版本与 Engine release identity。

同一个 key 并发到达时只运行一个 projection，其余请求等待同一个 future。磁盘 cache 是跨进程重启的持久层，Worker frame cache 是内存热层；两者不能使用不完整的 ticker/period key。坏缓存必须隔离并通过相同 Engine 路径重算，不能切到另一套实现。

### 4.2 通用预热与交互优先级

Worker 的 frame cache 在 Engine 重启后会丢失。若已有持久 Candles cache，打开图表可能不触发 base projection，于是第一次添加指标仍会遇到冷 frame。

应提供 Engine 级 `prepare projection source` 请求：

- 打开 Visualizer 或收藏 snapshot 完成后，后台预热 sealed Result 的基础帧；
- 用户真正打开图表或添加指标时，将相同 key 提升到队首；
- 后台任务与交互任务 single-flight，不重复执行；
- 页面关闭只取消订阅，不随意杀掉仍被其他页面等待的共享任务。

这项主要改善重启后的 p95 和排队稳定性，而不是已经预热场景下的单次公式速度。

### 4.3 多 Worker 分片

当前常驻 Worker 串行处理请求。单用户一次 projection 已明显加速，但多个页面或多个用户同时添加指标仍可能排队。可以建立小型 Worker pool，并按 Result digest 做粘性分片，使同一个 Result 尽量命中同一份 frame cache；交互任务优先于收藏后台预热。

每个请求仍必须进入一次性隔离 executor，不能为了并发复用 Signal Module instance。该项主要提高吞吐和 p95，不会显著降低单个无竞争请求的 p50，因此应晚于列式合同和增量绘图。

## 5. 优先级 P2：小收益与大数据扩展

### 5.1 缓存纯编译证明

可以按全部 immutable digests 缓存 Module definition 校验、temporary graph 编译计划、DataPath 解析和 archive 验证证据。不得缓存 Module object、initialize 后的状态、cycle cursor 或用户代码全局状态。当前这些工作是数十毫秒级，值得做，但不是第一瓶颈。

### 5.2 浏览器 Worker 与可转移列数据

列式合同落地后，可在浏览器 Web Worker 中解码、归一化大数组，再通过 transferable buffer 交给 Chart Core，避免大 Result JSON 在 UI 主线程形成长任务。它改善操作响应和滚动流畅度；若仍传逐行嵌套 JSON，仅把解析搬线程并不能消除对象分配。

### 5.3 压缩与二进制编码

HTTP gzip/Brotli 可快速降低网络字节数，但当前本机测试主要不是带宽瓶颈，并且压缩后的逐行 JSON仍需要完整解析。它可作为低风险补充；Arrow/自定义二进制应在列式合同稳定之后评估，不应先用传输格式锁死错误的数据形状。

### 5.4 视口投影与分层抽样

2,500 根日线尚可一次加载；分钟/逐笔数据不能无限返回全历史。Visualizer 可按可见时间范围请求列式窗口，并为远距离缩放使用 Engine 生成的确定性 OHLC 聚合层。拖动到新区域时预取相邻窗口。

这只能改变呈现数据量，不能改变 Signal 需要的 warm-up 和完整计算边界。指标计算仍由 Engine 负责；聚合 K 线也必须有版本化、可验证的 Visualizer 语义。

### 5.5 派生 Signal 列缓存

对于多个 Visualizer 复用同一组 SMA/EMA/ATR 中间列，可缓存 exact Module 输出列，key 绑定输入列 digest、Module archive、config 和运行合同。它对复杂指标链和大数据有价值，但失效规则和状态型 Module 边界更复杂，优先级低于完整 projection cache。

## 6. 建议实施顺序

推荐下一轮按以下顺序推进：

1. 保存与 projection 并行，先稳定拿到约 100 ms 的快速收益。
2. Trade Engine 列式 Visualizer Projection V2，解决最大的 Engine/传输数据形状问题。
3. 通用 Chart Core 增量 reconcile，解决缓存命中后仍约 300 ms 的整图重建。
4. 将 Basic 的 exact projection cache/single-flight 上移到 Engine，并补重启预热。
5. 有并发压力证据后再做 Worker pool；有分钟级大数据需求后再做视口投影。

目标应分开验收：

| 场景 | 下一阶段目标 | 一致性门槛 |
|---|---:|---|
| 2,500 bars 首次添加 BB | p50 < 0.7 s | 与当前逐行 Engine projection 逐点相等 |
| 浏览器命中 projection cache 后重加 | p50 < 0.25 s | Visualization revision、Pane、visible range 正确 |
| Engine 重启后第一次添加 | p50 < 0.9 s | persistent cache 或正式预热命中，无隐藏 fallback |
| 4 个并发不同指标 | 不因单 Worker 串行线性放大 | 每请求独立 executor，输出可复现 |

## 7. 不值得优先做的项

- 先改 BB/SMA 数学、NumPy、WASM：完整 Module 图只有约 41 ms/2,500 bars，收益上限太低。
- 在浏览器直接算指标：会破坏 Signal Module 与 Engine 输出权威。
- 让常驻 Worker 复用 Signal instance：会造成状态和用户代码进程全局泄漏。
- 只按股票代码缓存：无法区分 Dataset、Result、Module/config、bindings 和合同版本。
- 遇到 cache miss 时从原始 snapshot 直接画：这是语义 fallback，不是性能优化。

下一步最重要的两个结构性改动是“列式 Engine projection”和“增量 Chart Core”。前者缩短冷路径，后者缩短热路径；二者都保持 `Sampler → Result Data Dict → Signal → Visualizer` 的正式链路。
