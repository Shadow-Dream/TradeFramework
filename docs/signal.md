# Visualization 临时 Signal 执行说明

## 1. 问题与直接回答

用户原始问题（原文）：

> 另外，现在Visualization功能的临时Signal是怎么跑的，是需要完整再跑一轮回测？还是用现有Data Dict来计算东西就可以

- 问题/分析记录时间：2026-08-28T12:24:34-04:00（America/New_York；会话不提供消息自身时分，以本轮首个诊断进程时间记录）
- 回答/报告完成时间：2026-08-28T12:25:14-04:00（America/New_York）
- 分析基线：`release/basic-subsystem-v1`，提交 `cae76fbf4578081a47a37f036b7beae46f2fb9c3`

当前添加 Visualization 临时 Signal 时，不会完整再跑一轮 Backtest。它直接读取现有 completed immutable Backtest Result 中每个 cycle 的 `data`，在一次 Result Projection Runtime 中按时间顺序执行 temporary Signal Modules，然后只输出 Visualizer 需要的 DataKeys。

但是它目前必须先有一个 completed Backtest Result。这是数据源身份的前置依赖，不代表 projection 本身又执行了 Pipeline、Environment、Analysis 或 Backtest。

## 2. 当前执行链

以 BB 为例，实际链路是：

```text
现有 sealed Backtest Result
  cycles[0].data
  cycles[1].data
  ...
    → temporary bar/close selector Signal
    → temporary BB Signal（状态在本次 projection 的 cycles 间连续）
    → upper/middle/lower 临时 DataKeys
    → Result projection document
    → BB Visualizer lines
```

不会执行：

```text
Sampler → Pipeline → Environment → Analysis → Backtest Result Writer
```

因为 Sampler 的基础输出已经存在于旧 Result 的 `cycles[].data` 中。

## 3. 每次 projection 具体做什么

代码中的 `ResultCycleProcessor` 会：

1. 根据 archived Module Definitions 编译 temporary Module 依赖计划。
2. 为本次 projection 创建新的 execution root、`ModuleInvoker` 和 Module instances。
3. 流式读取 sealed Result 的每个 cycle。
4. 校验原 cycle 是否符合原 Result DataKey contracts，并校验 cycleId 唯一性。
5. 从当前 `cycle["data"]` 按 input bindings 取值。
6. 按依赖顺序调用 selector、indicator 等 temporary Signal。
7. 把 Signal outputs 写入当前内存中的临时 cycle data。
8. 对加入临时输出后的完整 DataKey contract 再做验证。
9. 只把 Visualization 请求的 paths 写入 projection document。
10. 全部 cycles 完成后调用 Module `finalize()`、`close()`，清理临时执行目录并退出 disposable Python process。

这些临时输出不会写回或修改原 Backtest Result。原 Result 继续保持 sealed immutable；持久保存的是按精确 identity 建立的 projection cache record。

## 4. 为什么仍然要约 689 ms

虽然没有重新 Backtest，但当前 projection 仍然不是“在浏览器里拿一个数组跑 BB 公式”：

- 要启动一次 disposable Python Result Runtime；
- 加载并验证 exact archived Module Definitions；
- 编译 temporary Module graph、bindings 和 contracts；
- 顺序扫描并验证 sealed Result 的全部 cycles；
- 为 selector 和 indicator 创建正式 SDK lifecycle；
- 对临时输出做最终 DataKey validation；
- 写出并校验 projection JSON，再进入内容寻址缓存。

实测 selector + BB 的 120 bars 数学计算只有约 1.9 ms，而冷 projection HTTP 约 689 ms。绝大部分是上述 Runtime、归档和合同边界，不是重新执行 Backtest，也不是 BB 公式。

## 5. 缓存命中时

`project_result_cached()` 的 cache key 包含：

- `backtestId`；
- sealed Result content digest；
- projection paths；
- temporary Module instances、bindings 和 config；
- exact Module definition versions/content digests。

完全相同的请求命中服务器缓存后不会再次执行 temporary Signal；同标签页的浏览器 projection cache 命中时甚至不会发 projection HTTP。参数、Module version、input bindings、Result 内容或 paths 任一变化时，才产生新的 projection。

## 6. Sample Result 改造后的执行方式

前一轮提出的 Sample Result/DataKey Timeline 可以直接替代当前 temporary Signal 的输入来源：

```text
sealed Sample Result
  → temporary selector Signal
  → temporary indicator Signal
  → projection cache
  → Visualizer
```

temporary Signal 的 compiler、ModuleInvoker、SDK lifecycle、DataKey validation 和 Visualizer 都不需要改成第二套实现。需要改变的是 Result Projection 接口不再只接受 `backtestId`，而是接受一个受 authority 校验的 sealed data-result identity。

这样 snapshot 只运行一次 Sampler。打开 K 线不运行 Backtest；添加指标只对现有 Data Dict 运行 temporary Signals；只有用户真正执行策略时才运行 Pipeline、Environment、Analysis 和 Backtest。

结论：当前 temporary Signal 已经是在现有 Data Dict 上计算，没有再跑完整 Backtest；不合理之处在于这个 Data Dict 只能从 completed Backtest Result 获得。应把它前移为独立、可缓存的主 Engine Sample Result。
