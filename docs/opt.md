# Basic 图表性能优化方案

## 1. 问题与结论

用户原始问题（原文）：

> 是否有什么优化的手段

- 问题/分析记录时间：2026-08-28T11:47:58-04:00（America/New_York；会话不提供消息自身时分，以本轮首个诊断进程时间记录）
- 回答/报告完成时间：2026-08-28T11:50:17-04:00（America/New_York）
- 分析基线：`release/basic-subsystem-v1`，提交 `cae76fbf4578081a47a37f036b7beae46f2fb9c3`
- 依据：前一轮三次独立冷环境实测及一次不可变 Result 内部计时；完整原始分解见 `docs/perf2.md`。

有，而且优化空间主要在 Engine 的固定开销，不在指标公式。建议分三层实施：

1. 先做不改变执行语义的并行、缓存和增量渲染。BB 首次添加可以从约 1.15 秒降到约 0.7–0.9 秒。
2. 再复用 Backtest/Result Runtime 的不可变准备产物，并使用常驻 supervisor 管理预热、单次使用的隔离 worker。冷指标 projection 有机会从约 689 ms 明显下降，冷打开也可减少约 2–3 秒准备开销。
3. 最终在主引擎内新增一等 `Sample Timeline`（或 `Sample Result`）资源，让画图只运行 Dataset + Sampler，不再为了取得 K 线 DataKey 强制完成 Pipeline、Environment、Analysis 和 Backtest Result。收藏股票的后台 snapshot 随后可直接预物化这个资源，把大多数打开操作变成当前已经实测为 0.47–0.59 秒的热路径。

## 2. 先优化哪里

| 操作 | 当前冷耗时 | 最大成本 | 不应优先优化的部分 |
|---|---:|---:|---:|
| 打开图表 | 5,999 ms | Backtest 4,085 ms；其中 kernel preparation 2,886 ms | Basic Pipeline/Environment/Analysis 实际 cycle 计算仅约 27.5 ms |
| 添加 BB | 1,146 ms | temporary Result projection 689 ms；Visualization 保存/编译 285 ms | BB 数学仅 0.669 ms/120 bars |

因此，换一个更快的均线算法、用 WebAssembly 写 BB、减少一次加法，都不会带来肉眼可见的改善。需要优化的是进程、合同、归档、物化和渲染边界。

## 3. 第一阶段：低风险、保持当前主引擎语义

### 3.1 Visualization CAS 保存与 projection 并行

当前添加指标的关键路径是：

```text
保存并完整编译 Visualization（约 285 ms）
  → temporary Signal projection（约 689 ms）
  → 重画（约 113 ms）
```

draft 已经包含精确 temporary Module、参数、bindings 和 projection paths，因此保存请求与 projection 可以同时启动。只有两者都成功、且 CAS revision 仍是当前 revision 时才安装并显示；如果保存冲突、参数又发生变化或用户删除指标，就中止或丢弃旧 projection。这样不显示未经确认的图层，也不改变 Engine/Visualizer 合同。

理论关键路径由 `285 + 689` 变为 `max(285, 689)`，预计直接节省约 250–300 ms。

### 3.2 projection single-flight 与精确内容寻址缓存

现有浏览器和服务器缓存已经证明有效：相同 BB 重加可从约 1.15 秒降到 0.42–0.58 秒。下一步不是做模糊的“股票缓存”，而是：

- 相同 projection key 同时只有一个真实计算，其余请求等待同一 future，避免多标签页/多 Pane 冷 miss 重复启动 Runtime。
- 缓存 key 必须包含 Result digest、projection paths、temporary Module definition/version/archive digest、instance graph、input bindings 和 config。
- 采用原子写入和有界 LRU/TTL；读取时仍校验 result digest，坏缓存直接隔离并重算。
- 收藏股票完成新 snapshot 后，可在后台预热默认指标的精确 projection；非默认参数仍按内容寻址正常计算。

### 3.3 Visualization 编译缓存与增量图表更新

每次添加/删除一个指标，目前仍会完整编译整个 Visualization revision，并 dispose/rebuild 所有 chart、Pane 和 series。可拆为两个优化：

- Engine compiler 缓存不随 revision 改变的已安装 Visualizer/Module 定义验证结果，并按 `specDigest + definitionDigests` 缓存完整编译结果。
- 浏览器比较前后 canonical Visualization，只新增、更新或删除受影响的 series/Pane；主 K 线和无关指标保持实例不动。时间范围和 crosshair 只同步变更，不重新初始化全部图表。

这主要改善热路径：目前缓存命中后重复添加仍需约 93–115 ms 重画、约 279 ms 保存。目标是把已缓存指标的交互降到约 150–250 ms，而不是每次整图重建。

## 4. 第二阶段：降低 Engine Runtime 固定成本

### 4.1 常驻 supervisor + 预热的一次性 projection worker

temporary Signal projection 比不含临时 Module 的 Candles projection 多约 467 ms，而 selector + BB 全部计算只有约 1.9 ms。这说明主要成本来自一次性 Python Runtime、模块归档加载和重复合同准备。

不建议让一个 Python worker 连续执行多个用户 projection，因为 Module 或依赖库的进程级状态可能泄漏。更稳妥的是由 Engine 管理常驻 supervisor，并提前准备一组“只执行一次请求就退出”的隔离 worker：

- worker 按 Engine release/runtime identity 分池，只预加载可信的 Engine/SDK，不在接单前加载用户 Module；
- 每个 worker 只接收一个 projection，创建全新的 Module instance、临时目录和状态，完成 `finalize/close` 后整个进程退出；
- supervisor 在后台补充新的空闲 worker，因此前台不再等待 Python 解释器和 Engine 基础 import；
- immutable Module archive 的验证证据可按 digest 缓存，但用户 Module 仍在单次使用的 child 内加载；
- 超时、异常或身份不匹配时销毁 worker，不降级到进程内直接执行；
- 结果仍由主 Engine 校验 digest 和 exact schema 后才能进入 projection cache。

这样优化的是解释器和可信 Engine 的启动等待，不是绕开 Signal Module，也不复用可观察的 Module 状态。它能节省多少必须实测；当前约 467 ms 差值还包含 Module materialization、合同和归档工作，不能全部预先算作可消除成本。

### 4.2 缓存 Backtest kernel 的不可变准备产物

冷打开最大的单项是 2.886 秒 kernel preparation。应先为该聚合阶段增加更细的正式计时，再只缓存可证明纯且不可变的部分，例如：

- 已冻结资源和 exact contracts 的规范化/摘要验证结果；
- Pipeline/Environment/Analysis 编译计划；
- Module archive 的验证与导入计划；
- Dataset descriptor/conformance 和 Sampler timeline index；
- Engine release/provider catalog 的稳定索引。

缓存 key 必须绑定全部资源版本、内容摘要、Engine release identity 和 protocol version。Module runtime state、随机数状态、cycle cursor、账户/持仓状态不能复用。每次 Backtest 仍创建新状态并生成新的不可变 Result。

### 4.3 优化 Sampler 数据访问

120 cycles 的 Sampler 阶段约 486 ms，远大于指标数学。可以预建 Dataset timeline index，批量解析 CSV/列式数据，并让每个 cycle 只做有界索引读取；避免重复 schema 解析、路径查找和对象深拷贝。该优化同时服务 Backtest 和未来的 Sample Timeline。

## 5. 第三阶段：从架构上解除“画图必须完整 Backtest”

最根本的方案是在主引擎中增加一等、不可变、可校验的 `Sample Timeline`：

```text
Provider snapshot
  → immutable Dataset Version
  → Engine Sampler
  → sealed Sample Timeline / Sample Result
       ├─ Candles Visualizer
       ├─ temporary Signal Modules → indicator Visualizer
       └─ Pipeline + Environment + Analysis → Backtest Result
```

它不是让 Subsystem 直接读原始行情画线，也不是隐藏 fallback。它仍然要求：

- Dataset 经正式 protocol 和 conformance 校验；
- Sampler 是发布并绑定版本的主引擎 Module；
- Timeline 有内容摘要、lineage、event/available time 和 exact DataKey schema；
- temporary Signal 与 Visualizer 仍通过主引擎 compiler/runtime；
- Backtest 可以引用同一个 immutable Timeline，但创建自己的全新策略状态。

这会移除“只为了显示 K 线却必须完成中性 Pipeline、Environment、Analysis 和 Backtest Result”的结构性耦合。按当前计时，它至少能避开约 4 秒的完整 Backtest worker 冷路径；实际冷打开仍需 Dataset/Sampler/Visualizer，因此不能直接承诺 0.5 秒。

## 6. 收藏 snapshot 应怎样与缓存结合

目前“后台获取 K 线 snapshot”如果只保存 provider 原始响应，打开时仍会重新 Dataset publication、Sampler/Backtest 和 Visualization materialization，用户感知不会明显改善。正确的后台流水线应是：

1. 星标触发 provider snapshot，记录 `retrievedAt`、availability、adjustment policy 和内容摘要。
2. 内容变化才发布新的 immutable Dataset Version；无变化直接复用已有版本。
3. 后台预物化 Sample Timeline；在该 Engine 能力完成前，可以暂时后台提交当前正式中性 Backtest，以便把前台冷等待移到后台。
4. 预建默认 Candles projection 和默认 Visualization；可选预热少量默认指标。
5. 打开时原子选择“最新已完成且未过期”的版本，并明确显示数据时间；后台有更新时再切换到新的完整版本，绝不把半成品覆盖到当前图表。

这不是无限缓存：美股日线可按交易日收盘后的 provider availability 更新；盘中或更高频数据需要更短 TTL 和更严格的过期显示。

## 7. 实施顺序和验收线

结合后续对 snapshot 缓存边界的审计，优先级应修正如下。每一步都用同一 120-bar fixture 和至少 20 次冷/热重复测试：

1. 主 Engine Sample Timeline/DataKey Result 与内容寻址缓存；同一 Dataset/Sampler identity 只物化一次。
2. 收藏 snapshot 后台预物化该 Timeline，并区分 snapshot saved 与 chart cache ready；新标签页打开 p50 目标 `< 0.7 s`。
3. Visualizer 和 temporary Signal projection 直接消费 Timeline，不再要求完整 Backtest Result。
4. 并行 Visualization save + indicator projection；首次 BB p50 目标 `< 0.9 s`，CAS conflict 时不得显示未确认图层。
5. 增量 series/Pane 更新和 compiler cache；缓存命中重加 p50 目标 `< 0.3 s`，图表时间范围不得跳变。
6. 若残差计时仍证明进程启动显著，再引入单次使用的预热 projection worker；输出和错误语义必须与原一次性 Runtime 完全一致。
7. 拆分 kernel preparation 正式计时并缓存不可变准备产物；显式 Backtest 的结果摘要必须与未缓存路径完全一致。

最先应解决的是 Sample Timeline/DataKey 缓存。单纯给完整 Backtest 或 projection worker 加速只能缩短错误耦合，不能消除“已有 snapshot 仍需即时 Backtest”的问题。

## 8. 明确不采用的快捷方式

- 不允许 Subsystem 从 provider response 或 CSV 直接画 K 线。
- 不允许浏览器自己计算正式指标并冒充 Signal 输出。
- 不允许用 ticker + period 这种不完整 key 复用缓存。
- 不允许跨请求复用有状态 Signal/Environment/Analysis instance。
- 不允许缓存异常时静默 fallback 到旧协议、旧 selector 或原始价格。
- 不允许先显示未经 CAS 确认的 Visualization，再异步“补保存”。

这些做法可能让 demo 看起来快，却会破坏前面已经确认的 `Sampler → Signal → Visualizer`、不可变 Result 和可复现性边界。
