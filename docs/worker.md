# Projection Worker 一致性分析

## 1. 问题与直接回答

用户原始问题（原文）：

> 引入常驻 Projection Worker，这会不会影响和原始TradeEngine的一致性

- 问题/分析记录时间：2026-08-28T11:58:31-04:00（America/New_York；会话不提供消息自身时分，以本轮首个诊断进程时间记录）
- 回答/报告完成时间：2026-08-28T12:00:30-04:00（America/New_York）
- 分析基线：`release/basic-subsystem-v1`，提交 `cae76fbf4578081a47a37f036b7beae46f2fb9c3`

会有潜在影响。如果“常驻 Projection Worker”表示同一个 Python 进程连续执行多个 projection，甚至复用 Signal Module instance，那么它不能天然保证与原始 Trade Engine 一致。进程级全局变量、第三方库缓存、随机状态、环境修改、未完全释放的文件/线程以及 Module import 副作用都可能跨请求泄漏。

但常驻的是 supervisor、每个 projection 仍使用全新的单次执行 worker，就可以保留原始隔离语义，同时去掉用户请求到达后再启动 Python 和导入 Engine 基础代码的等待。推荐采用这个方案，而不是通用的可复用 Module worker pool。

## 2. 原始 Runtime 当前提供的保证

当前代码路径是：

```text
Engine service
  → 生成严格 spec.json 和独立临时目录
  → 启动 python -m engine.worker.result_runtime
  → 编译 temporary Module plan
  → 为本 projection 创建 ResultCycleProcessor
  → 为每个节点创建新的 ModuleInvoker / Module instance
  → 流式验证 Result、逐 cycle 调用 Signal、验证最终 DataKeys
  → finalize / close / 卸载归档 Python package
  → 子进程退出
```

`engine/runtime/result_runtime.py` 明确把它定义为 disposable Python process；`engine/runtime/result_projection.py` 每次创建新的 invokers 和 execution root；`engine/runtime/python_module_adapter.py` 在 close 后移除该归档 package 的 `sys.modules` 项。

这里的关键不是“进程短命”四个字，而是每个 projection 从干净的执行上下文开始，生命周期和失败清理都有明确边界。

## 3. 几种方案的一致性差异

| 方案 | Module instance | Python 进程 | 一致性判断 |
|---|---|---|---|
| 当前一次性 Runtime | 每请求新建 | 每请求新建 | 基准 |
| 同一 worker、复用 Module instance | 跨请求复用 | 常驻 | 不可接受；直接改变有状态 Signal 语义 |
| 同一 worker、新建 Module instance | 每请求新建 | 常驻 | 仍有风险；模块外的进程级状态可能泄漏 |
| 常驻 supervisor + fork child | 每请求新建 | 每请求新 child | 可接受，但 supervisor 必须在加载用户 Module 前 fork，且 child 单次使用 |
| 预热的一次性 worker 池 | 每请求新建 | worker 只处理一次后退出 | 推荐；保留单请求隔离，并把启动移到后台 |

即使当前 Python adapter 会卸载归档 package，也不能证明复用同一进程完全等价。Module 可能修改 `os.environ`、启动线程、改变 locale/timezone，或让非归档前缀的依赖库保留全局缓存。这些状态不一定能由 `close()` 和 `sys.modules` 清理完整恢复。

## 4. 推荐的安全实现

推荐设计不是“复用计算 worker”，而是“维持一批已经启动、但尚未接触任何用户 Module 的单次使用 worker”：

1. 常驻 supervisor 绑定一个精确 Engine release/runtime identity。
2. 它预启动若干 child；child 只加载可信 Engine、SDK 和严格 JSON/runtime 基础设施，然后等待一个 spec。
3. child 收到一个 projection 后，执行与现在相同的 `project_result()`、`compile_temporary_module_plan()`、`ResultCycleProcessor` 和 `ModuleInvoker` 路径。
4. Module definition、archive、binding、config、input/output contracts 仍逐项验证；不增加另一套“快速指标执行器”。
5. child 创建新的 execution root、Module instances、cycle identity index 和输出文件。
6. 无论成功、合同错误、超时还是取消，child 完成清理后退出；不接受第二个请求。
7. supervisor 异步补充新的空闲 child。没有健康 child 时应明确排队或报错，不得静默切换到前端计算、原始数据直画或另一套 Module 语义。

这个结构只改变“进程什么时候启动”，不改变“一个进程执行多少个 projection”和“projection 调用哪些 Engine 代码”。

可以额外缓存的只能是已证明不可变的材料，例如 Engine release 内置定义索引、Module archive digest 验证证据和编译器的纯计划。缓存必须绑定完整定义内容摘要；Module object、SDK lifecycle state、cycle cursor 和 temporary output 不得跨请求复用。

## 5. 什么才算与原始 Trade Engine 一致

一致性不能只测一条 BB 曲线看起来一样。硬门槛应包括：

- 对相同 sealed Result、paths、Module definitions、bindings 和 config，canonical projection JSON 逐字节一致，或在明确排除非语义元数据后 strict JSON exact-equal。
- 成功、缺失 DataKey、schema mismatch、Module initialize/invoke/finalize/close 异常的类型和消息语义一致。
- 连续 `A → B → A` 时两个 A 完全一致，证明 B 没有污染后一个请求。
- 同一请求串行、并行、取消后重试、worker 崩溃后重试的结果一致。
- 不同 Module version/archive digest 绝不共享编译或执行状态。
- 临时目录、输出路径、进程环境及归档身份仍经过现有 fail-closed 校验。
- 原实现与新实现对所有现有技术指标、stateful fixture、恶意 module-global fixture 做 differential test。

时间、PID、临时目录名字可以不同，但它们不得进入 Signal 可观察输入或 canonical projection 输出。若当前 Runtime 允许 Module 读取这些进程外部状态，则应先收紧 Module sandbox/contract，不能靠“多数指标不会用”来宣称等价。

## 6. 发布门槛与性能预期

建议先把旧一次性 Runtime 保留为测试 oracle，而不是生产中的隐藏 fallback：

1. 在测试和受控 benchmark 中，对旧、新两条路径运行相同 corpus，要求输出与错误语义完全一致。
2. 进行至少数千次顺序/并发/取消/崩溃测试，并检查进程、文件、线程和内存泄漏。
3. 只有 differential gate 通过后，才把新 supervisor 作为一个明确的新 Engine runtime release 发布；生产请求只走该 release 的唯一正式路径。
4. 发布后旧实现可保留为离线一致性 oracle，不在请求失败时静默切换。

还需修正一个性能预期：实测 temporary BB Runtime 相对直接 Candles projection 多约 467 ms，但这不全是 Python 启动。它还包含 temporary plan 编译、Module materialization、archive/contract 验证和逐 cycle 最终 schema 验证。预热的一次性 worker 只能消除其中的解释器与 Engine 基础加载等待；具体收益必须通过分阶段计时确认，不能预先承诺全部降至百毫秒。

结论是：直接做“常驻、重复使用的 Projection Worker”会带来一致性风险，不应采用；“常驻 supervisor + 每请求单次使用的预热 child”在保持原执行代码和严格 differential gate 的条件下，可以做到与原始 Trade Engine 语义一致。
