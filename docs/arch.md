# Basic Subsystem 主引擎血缘与绕行路径发布前审计

## 1. 已确认事实

用户原始问题（原文）：

> 非常好。现在基础功能已经有了。确认以下几个点之后我们先Push一版：
> 1、请确认当前这个Subsystem的所有功能都是基于主引擎进行开发的。包括Visualizer，Signal，以及临时Signal Module Instance
> 2、请确认无特化实现的、设计之外的Fallback/Shortcut/Patch。特别是，这些线必须是Sampler -> Signal -> Visualizer出来的，而不是直接拿原始数据硬画

- 问题/审计开始记录时间：2026-08-28T09:25:46-04:00（America/New_York）
- 回答/报告完成时间：2026-08-28T10:06:21-04:00（America/New_York）
- 审计基线：本地 `main` 基于 `911bc5c37d8113024c4a34242d8f07f578ddf188`
- 审计方法：逐层源码血缘、全仓绕行关键字扫描、20 项真实已安装合同编译、真实 Result Runtime 临时 Signal E2E，以及 Web/Engine 发布门禁。

结论分为两个精确层次：

1. 计算、资源与可视化语义确认走主引擎。Dataset、Sampler、Pipeline、Environment、Analysis、Backtest、Result、Signal Module Definition、Visualization revision、Visualizer contract 编译和临时 Signal Result Projection，全部使用主 Engine 的公开 repository/service/compiler/runtime 边界，没有 Basic 私有执行器。
2. 不能把“所有 Subsystem 代码都在 Engine 内核”当成正确目标。行情 Provider、星标列表、后台 Snapshot 调度、缓存编排和页面交互属于 Application Protocol/Subsystem 层；它们只负责采集、发布普通 Dataset、组合精确 Engine 请求和展示 Engine 结果，不复制 Engine 计算语义。

实际计算血缘如下：

```text
Provider evidence
  → Dataset Adapter → immutable Dataset Version
  → installed Sampler → Result.time + Result.price DataKeys
  → temporary basic-price-bar-selector Signal
  → temporary technical-indicator Signal
  → Engine Result Projection Runtime
  → series.line / series.histogram / series.scatter Visualizer
  → generic TradeChartCore host renderer
```

关键代码证据：

- `application_subsystems/basic.py:61-99` 只是 HTTP adapter，将 open 和 projection 请求交给 `market_service`，没有行情计算或绘图。
- `application_protocols/basic_workflow/market_service.py:2354-2405` 将 Provider 证据发布为普通不可变 Dataset；`2506-2589` 通过 `pipeline_service` 创建或读取精确不可变 Pipeline；`2689-2860` 通过 `prepare_backtest_submission` 和 Job Manager 提交正常 Backtest。
- `market_service.py:1814-1874` 的缓存 miss 仍调用主引擎 `engine.service.result_projection.write_backtest_result_slice`；缓存 identity 包含 Backtest Result digest、精确 projection request 和每个 Module content digest，缓存不生成替代结果。
- `web/basic_workflow_workspace.js:1098-1169` 将共享 selector 和每个指标保存为 `kind: "Signal"` 的临时 Module Instance；每个技术图层的 `params.dataKey` 绑定该临时 Signal 的 output DataKey。
- `engine/service/result_projection.py:15-54` 只接受已封存 Result；存在 temporaryModules 时加载已归档 Module Definition，并进入隔离 Result Runtime。
- `engine/runtime/result_projection.py:45-125` 用主引擎 `ModuleInvoker.from_authority` 初始化、逐 cycle 调用 Module、写入 output DataKey，再以最终合同验证结果。
- `engine/service/visualizations.py:25-74` 在保存 revision 前调用主 Visualizer compiler；`engine/compiler/visualization.py` 同时编译 Result DataKey、temporary Module plan 和 Visualizer input contract。
- `web/chart_core.js` 是通用 Visualizer host。Basic workspace 本身没有 `setData()`、`addLineSeries()`、`addCandlestickSeries()` 或任何 SMA/RSI/BB 前端公式；它只请求 Engine projection 后调用 `TradeChartCore.drawFinancialPane`。

20 个技术指标实现均继承公共 `strategy_devkit.module_sdk.SignalModule`。这些实现不 import `engine`、Subsystem、Provider 或 Web；它们作为普通 BuiltIn Module 经 `module_publication.publish_module` 安装、版本化和归档。

本次审计确实发现并修掉一条隐式退路：OHLCV v3 Result 在完整 `basic-price-bar-selector` 定义缺失时，close 类指标原先可能退回 v2 `basic-price-close-selector`。现在 v3 必须精确解析完整 selector，否则 fail closed；只有明确的 v2 Result 才允许使用 v2 selector。两条路径本身仍都是正常 Signal Module，不是浏览器 shortcut。

## 2. 确定性计算

全 20 项目录从临时干净 Engine repository 安装真实 BuiltIn definitions，再由当前 workspace 逐项添加并交给主 Visualizer compiler，得到：

| 验证项 | 确定结果 |
|---|---:|
| 技术指标 Module Instance | 20 |
| 共享 selector Signal Instance | 1 |
| 技术指标 output DataKey | 33 |
| 实际技术 Visualizer 图层 | 31 |
| OHLCV selector output DataKey | 11 |
| Pane（含主 Pane） | 12 |
| 编译后 Visualization contracts | 89 |
| 技术图层直接绑定 `price.*` | 0 |
| 技术图层绑定临时 Signal output | 31/31 |

33 个 Signal output 多于 31 个图层，是因为 Supertrend 和 Parabolic SAR 还输出 direction，direction 属于可复用 Signal 语义但当前不单独绘图。所有已绘制技术层都能反向找到唯一 temporary Signal producer；共享 selector、指标 output、Visualizer ID 任一冲突都会在保存前拒绝。

真实 E2E 使用三根 SPY close `101, 201, 301`：

```text
Dataset → basic-price-map-sampler
price.day.SPY.close → basic-price-close-selector
indicator.source.close → sma-indicator(period=2)
indicator.basic.sma.1.sma → Result projection
实际输出 = [null, 151.0, 251.0]
```

这条测试实际运行 Backtest、封存 Result、启动隔离临时 Module Runtime 并读取投影结果，不是只检查 JSON 结构。

发布门禁第一次运行得到 `152 passed / 1 failed / 1 warning`，唯一失败是 `result_execution.py` 823 行超过架构上限 800；它与图线血缘无关，但没有被忽略。冻结 Result 配置证据和 v12 兼容请求合同已正式拆到 `engine/contracts/result_config.py`，`result_execution.py` 降至 586 行，新文件 265 行，公开导出和校验语义不变。

拆分及隐式 selector 退路修复后，最终专项复跑为：

- Result contract、repository、worker、Backtest E2E 与架构门禁：29/29 通过，305.77 秒。
- Basic Web、20 指标全量合同、技术指标 Module 与架构门禁：49/49 通过，29.18 秒；仅保留 1 条既有 Python escape deprecation warning。
- `node --check web/basic_workflow_workspace.js` 通过。
- `python3 -m py_compile` 通过。
- `git diff --check` 通过。
- 从 `HEAD` 导出到全新临时目录、仅应用暂存补丁后的发布快照门禁：153/153 通过，775.19 秒；随后 `node --check` 通过。这证明当前版本不依赖 `cs.csv`、性能 profile 或其他未暂存工作树文件。
- Agent Web：824/824 测试通过；TypeScript `tsc --noEmit` 和 Vite production build 通过。

## 3. 解释、反证与不确定性

K 线需要单独说明。当前 K 线是规范的 `Sampler → Result price DataKey → ohlc.candles Visualizer`，不是 `Sampler → Signal → Visualizer`。这里没有技术指标计算，插入一个恒等 bar Signal 只会增加 Module 启动、逐 cycle 校验和投影成本，不增加语义或可追溯性。因此本报告确认“所有技术分析线”为 `Sampler → Signal → Visualizer`，但不会虚假宣称 K 线也经过无意义的 Signal。如果产品规则字面要求每个像素层都必须经过 Signal，需要另行改变该规则或接受额外恒等层成本。

用户画线同样是纯 Visualization annotation：它以 Candles Visualizer 提供的时间/价格坐标能力为目标，不读取 Provider 原始 bar，也不属于技术 Signal。Ichimoku 的前移/后移由公共 Visualizer `offsetBars` 参数完成；Signal 只输出未位移数值，Workspace 只把显式 Module 配置映射到通用 Visualizer 参数，不在浏览器伪造交易日期。

仓库还存在 `application_protocols/basic_workflow/visualization_presets.py`，它能把已经存在于 Result 的 close/equity/position DataKey 直接交给 Visualizer。该 helper 不被当前 Basic Subsystem open/workspace 路径调用，也不是技术指标；即使未来使用，它读取的仍是 Sampler/Engine Result，而不是 Provider 原始响应。

以下行为是明确设计边界，不是隐藏 fallback：

- v2 无 volume Dataset 只允许 close 类指标，并通过精确 v2 close-selector Signal；HLC/volume 指标明确报 “requires an OHLCV snapshot”。
- projection 单图层失败会形成该 Visualizer 的 error，不会生成替代点；其他独立图层可继续显示属于错误隔离。
- 缓存 miss、digest 不匹配或 managed Pipeline 旋转会重新走正式 Engine materialization/projection，绝不从 Provider 响应直接画图。
- Basic 展示投影只接受本协议 Visualizer 和白名单 BuiltIn Signal；跨协议依赖不会被偷偷解释或执行。

可推翻本报告结论的反证包括：Basic workspace 出现指标公式或直接 `setData()`；技术 Visualizer 绑定 `price.*` 而非临时 output；temporary Module 不经 Archived Module repository；projection miss 直接读取 Provider bars；缓存键不含 Result/Module digest；或 v3 selector 缺失时继续渲染 close 指标。当前源码扫描、负向测试、编译和真实 Runtime E2E 均未出现这些反证。

## 4. 单一下一实验或 Proposal

Push 后只做一个干净检出实验：从远端发布分支重新 clone 到空目录，安装 BuiltIns，用一份 120 根 OHLCV fixture 完成 AAPL Backtest，并一次添加全部 20 个指标；验收必须同时满足 20 个 temporary Signal、31 个技术 Visualizer 全部编译与物化、31/31 图层只读 Signal output、第二次 projection 命中 digest cache、浏览器网络中不存在 Provider bar 响应。该实验同时验证“提交文件范围完整”和“当前工作树没有提供未提交依赖”，不引入新功能。
