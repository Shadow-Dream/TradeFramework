# Indicator 添加性能分析

问题原文：

> 现在加载非常快，但是添加indicators还是非常慢。
>
> 如何解决？

- 问题记录时间（本轮诊断开始）：2026-08-28 14:30 EDT（UTC-04:00）
- 回答完成时间：2026-08-28 14:37 EDT（UTC-04:00）
- 代码基线：`cae76fbf4578081a47a37f036b7beae46f2fb9c3`
- 性能原始记录：`sha256:f14c6191eb22daba399a0477f0bf13b7e8d375b5e3dc8df4392f8f2bf7cdb657`
- 诊断夹具：2,500 根确定性日线、BB(20, 2)、正式 HTTP/Engine/Visualizer 路径、Backtest 数量 0

## 已确认事实

1. 添加指标时，前端先同步保存一个新的 Sample Visualization revision，然后才执行临时 Signal 投影和图表重绘。实现位置为 `web/basic_workflow_workspace.js:1710`。
2. 临时 Signal 没有启动 Backtest。它读取已经封存的 Sample Result/DataKey 时间线，加载精确归档 Module 版本，在隔离 Result Runtime 中执行 `initialize → invoke* → finalize → close`。实现位置为 `engine/service/sample_result_projection.py:15`、`engine/runtime/result_runtime.py:672`。
3. 当前每次添加、更新或删除指标都会调用 `disposeChart()`，销毁全部 Pane、蜡烛、指标、Drawing controller 和时间轴订阅，再从头创建整张图。实现位置为 `web/basic_workflow_workspace.js:2083`、`:2096`。
4. Visualizer dependency planner 已经会为每条指标线选择最小的 Signal 祖先子图；添加第二个无依赖指标时，不会在 Engine 中无条件重算第一个指标。因此主要问题不是缺少 Module 依赖裁剪。
5. Sample projection 已有浏览器级和服务器内容寻址缓存。同页第二次添加同配置时不发送 projection 请求；新页面会命中服务器 projection 缓存。

## 确定性计算

第一次添加 BB 的端到端时间为 1,688ms：

| 分量 | 耗时 | 占端到端比例 |
|---|---:|---:|
| 保存 Visualization revision | 127.8ms | 7.6% |
| Sample projection HTTP | 1,019.3ms | 60.4% |
| 其中：隔离 Engine projection | 715.8ms | 42.4% |
| `paneTimeInfo` | 216.5ms | 12.8% |
| `drawFinancialPane` | 209.1ms | 12.4% |

同一页面删除后再次添加相同 BB，projection 完全命中浏览器缓存，仍耗时 617ms：Visualization 保存 112.8ms，全图重建约 459.8ms。说明热路径的主要瓶颈已经从服务器转成浏览器整图销毁与重建。

新页面命中服务器 projection 缓存时，添加 BB 为 896ms：Visualization 保存 136.8ms、projection 响应 168.7ms、整图重建约 461.7ms。

2500 根数据的 Signal 微基准：价格选择器 + BB 的完整 Module 调用仅 40.75ms；其中公式、输入校验、输出校验都已包含。它只占首次端到端耗时约 2.4%，所以优化 BB 公式本身几乎不会改善体感。

同一投影在主进程内执行仍需 498.3ms，而隔离 Runtime 为 715.8ms。单纯把进程改成长驻只能直接省约 217.5ms；剩余时间主要是重复读取/验证 Sample Result、逐周期 JSON 处理和写出约 804KB Result slice。因此“只做常驻进程”不是完整答案。

## 解释、反证与不确定性

最有效的解决方案应按以下顺序实施：

1. **增量 Visualizer renderer**：用 `paneId + visualizerId` 维护 Chart/Series 实例映射。添加 BB 时只创建三条 LineSeries 并设置其数据；删除时只移除这三条线；新增 oscillator 时只创建对应副 Pane。主蜡烛、Drawing controller、可视区间和已有指标都不重建。预计先消除约 400–460ms。
2. **常驻但仍隔离的 Projection Worker**：Worker 以 `sampleResultId + resultContentDigest + Engine runtime identity` 为身份，首次完整验证封存 Sample Result 后缓存只读 Data Dict frames；每个请求仍创建全新的精确 Module instances 并执行完整生命周期，绝不复用 Module 可变状态。进程崩溃应让请求失败，不允许静默回落到另一套公式或浏览器计算。
3. **紧凑的 Engine projection slice**：当前为三条 BB 线向浏览器传回约 804KB 的逐周期嵌套 Result JSON。应增加由 Engine 生成并校验的时间列 + series 列式投影合同，或至少直接流式发送已封存的预序列化缓存，避免服务器缓存命中后仍反序列化再序列化整份 JSON。这不是浏览器硬算指标，Signal 结果仍来自 Engine。
4. **并行保存与计算**：Visualization revision 保存和只读 projection 可同时开始；只有两者都成功后才提交 UI canonical state。这样可隐藏约 100–130ms，同时保留失败回滚和 CAS 冲突语义。
5. **低优先级推测预热**：打开指标参数窗口时，按当前默认参数预热一次精确 projection；用户修改参数后 debounce 预热。此项只能改善常用默认值，不能代替前三项。

基于本次分量测量，完成第 1 项后，同页缓存指标预计可从约 617ms 降到 150–250ms；第 1–4 项全部完成后，首次新参数指标预计为 350–600ms。该数字是工程推断，必须由同一基准复测；其反证是增量 renderer 后 `paneTimeInfo + drawFinancialPane` 仍接近 400ms，或常驻 Worker 后冷 projection 仍接近 716ms。

不会采用的方案：在浏览器直接读取原始 OHLC 计算指标、跳过 Module 端口/Schema 校验、复用上次 Module 实例状态、用近似结果先画后换。这些都会破坏与 Engine 的一致性。

## 单一下一实验或 Proposal

先只实现增量 Visualizer renderer，不同时改 Projection Runtime。验收实验固定为 2,500 根数据和 BB(20, 2)：

- 同页缓存命中添加 BB 小于 250ms；
- 主 Candle series 不重新创建或 `setData`；
- 原可视时间范围、Drawing 和已有指标实例保持不变；
- Signal projection 内容摘要与当前实现完全一致；
- 所有失败继续 fail-closed，不增加 fallback/shortcut。

这一步能用最小改动验证当前最大的独立瓶颈。通过后再做常驻 Projection Worker；否则先检查 Visualizer 的 series 更新接口，而不是修改 Signal 计算。
