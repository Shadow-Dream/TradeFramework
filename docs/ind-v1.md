# Basic 基础指标首轮可视化接入报告

原始问题：`是否可以接入一些基础指标到子系统里面，开始第一轮的可视化接入测试`

问题时间：2026-08-27（会话未提供精确时分，America/New_York）
回答时间：2026-08-27 10:56:50 EDT（UTC-04:00）

## 1. 已确认事实

首轮已经接入 Basic 工作区，范围限定为视觉分析层，不改变既有 Pipeline、Environment、Analysis Graph 或回测执行语义，也未接入或发布 Wickra 草稿。

接入的三个开关是 SMA(20)、EMA(20) 和 Bollinger Bands(20, 2)。当前 Basic renderer 只绘制一个 K 线面板，因此首轮全部采用主图叠加；原候选 RSI(14) 需要独立副图，放到支持多 Pane 后的后续轮次更可靠。

实现增加了指标工具栏、启用状态和可访问状态提示。每次开关生成一份新的 Visualization schemaVersion 3 spec，并使用现有 `expectedRevision` 比较交换保存。保存期间指标编辑与画线编辑互斥；普通失败保留最后确认版本，修订冲突只加载服务器当前版本，不自动合并或重试。

指标采用已归档的不可变 BuiltIn Module 精确版本。未标协议的通用指标只有同时满足 `builtin: true` 和显式白名单时才可进入 Basic catalog；其他协议定义和普通用户定义继续被排除。

Basic 的 `price` 是动态双层映射。直接把 `price.day.US-AAPL.close` 接到必填数值端口会被 Engine 正确判定为“可能缺失”。因此新增了协议内普通 Signal Module `basic-price-close-selector`：它读取必填根 `price`，按 `decisionPeriod` 和 `instrumentId` 精确选择收盘价，再把必填数值输出交给三个通用指标。数据流为：

```text
Result.price
    └─ basic-price-close-selector
         └─ indicator.source.close
              ├─ sma-indicator ────────────── 1 条 series.line
              ├─ ema-indicator ────────────── 1 条 series.line
              └─ bollinger-bands-indicator ── 3 条 series.line
                                                   └─ 现有 Candles 主图
```

## 2. 确定性计算

首轮一共增加 3 个用户可见开关、4 个根级临时 Module 实例和 5 条线图层。共享价格选择器只保存一份；单独启用任意一个指标时为“1 个选择器 + 1 个指标 Module”，关闭最后一个指标时选择器一并移除。

每个指标实例的输入、输出、配置、Module kind、moduleId 和不可变 version 都进入 Visualization spec。SMA 和 EMA 各使用 period 20；布林带使用 period 20、k 2，并只绑定 middle、upper、lower 三个输出。所有线继承当前 Candles 的 `timeDomainId` 与 `priceScaleId`，时间读取固定绑定 Result 的 `time` DataKey。

干净临时安装中的已验证精确身份为 `Signal/basic-price-close-selector/1`、`Signal/sma-indicator/1`、`Signal/ema-indicator/1` 和 `Signal/bollinger-bands-indicator/1`。实际工作区从 catalog 确定最新的规范正整数版本，并把选中的精确版本写入 spec；不会使用未固定版本的引用。

验证结果：

- `node --check web/basic_workflow_workspace.js`：通过。
- `python3 -m py_compile builtin_implementations/pipeline/basic_price_close_selector.py`：通过。
- `tests/web/test_basic_workflow_subsystem.py`：21 项通过；覆盖纯 spec 组合、三指标接线、最新精确版本选择、协议隔离、CAS 保存、冲突回退、Engine 编译和选择器失败关闭。
- Basic 协议、BuiltIn 安装、市场服务、Visualizer 联合回归：50 项通过，1 项既有失败；失败位置为 `tests/engine/contracts/test_visualizer.py::VisualizerContractTests::test_rectangle_brush_and_text_have_exact_persisted_state_contracts`，原因是测试把 `trade.basic-workflow` 协议 ID 当作 Visualizer ID 查表，触发 `KeyError`。该文件不在本轮修改范围内。

## 3. 解释、反证与不确定性

后端编译器已经使用真实安装后的归档 Module Definition 证明整条临时依赖链和五条 Visualizer 数据契约成立；前端 VM 行为测试也证明输入 spec 不被原地修改、成功确认后才提升 canonical revision、冲突不合并、失败不改变当前图。

这仍不是浏览器中的最终视觉验收。本轮没有启动真实市场服务、写入用户控制库或生成截图，因此尚未观察真实行情下的颜色辨识、线条重叠、首 20 根预热空值以及窄屏工具栏体验。布林带的三条线会分别请求可复现的 Result projection；首轮正确性优先，尚未对相同临时依赖的请求合并做性能优化。

Strategy Development 约束使本轮保持应用层组合：新增的是普通可归档 Module 和 Visualization 临时实例，没有修改 Engine compiler、worker、Pipeline、Environment 或 Analysis Graph，也没有发布 Wickra。

## 4. 单一下一实验 Proposal

Proposal：只进行一次真实 Basic 工作区视觉验收。选择一个已有完整日线 Result 的股票，按 `SMA 20 → EMA 20 → BB 20·2 → 逐一关闭` 的固定顺序操作；每一步记录 Visualization revision、Result projection 是否成功、五条线的实际显示、20 根预热区和一次页面重载后的状态恢复，并保留一张最终截图。通过条件是无诊断、无跨协议资源、重载后开关状态与服务器 revision 一致；失败时停止，不自动改参数、不接入 RSI、不发布 Wickra，以该单一失败证据进入下一轮诊断。
