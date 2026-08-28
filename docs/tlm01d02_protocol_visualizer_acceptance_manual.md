# TLM01D02 协议与 Visualizer 线性验收手册

本手册已按 2026-08-25 的严格 Visualizer 合同更新，适用于 Preview 环境，入口为
`http://10.130.130.66:30809`。全部步骤必须按编号执行，不跳步、不并行运行回测。

旧的 OHLC-only Result 不再是 Candle-compatible Result。Candles 申请的单个 DataKey 必须在
对象内同时声明并提供 `eventTime/open/high/low/close`；它不会读取 cycle `decisionTime`，也不会
从其他 DataKey 猜时间。不可变的旧 Result 和旧 Sampler Version 不做迁移或静默补值，必须使用
包含上述显式合同的新 Sampler Version 重新运行 Backtest 后再验收。

本手册依赖的
`artifacts/agent-workspaces/tlm01d02-context-bootstrap-v2/PROPOSAL.md` 已于 2026-08-21 发布到
Preview。Pipeline、Environment、Analysis 的验收版本为 `v2`。原 Sampler `v3` 产生的
OHLC-only Result 只保留为历史证据，不得用于当前 Candles 验收；Sampler 必须选择从当前代码
重新发布、且输出 bar schema 明确包含必填 `eventTime` 的 immutable Version。
资源装载下拉框只应显示 current version；如果显示旧版本或同时显示多个 version，应先强制刷新
页面并停止验收。

## 固定验收口径

| 项目 | 固定值 |
|---|---|
| Pipeline | `TLM1D02 D13 Causal Strategy` `v2`；禁止选择旧 `tlm1d02@1` |
| Sampler | `TLM01D02 Basic Workflow causal context Sampler` 的 current strict-event-time Version；不得选择旧 `v3`；参数保持为空，由单标的 Dataset 自动推断 instrument |
| Environment | `TLM1D02 Market Environment` `v2`；禁止选择旧版本 |
| Analysis | `TLM1D02 D13 Performance` `v2`；禁止选择旧版本 |
| 正式小时窗口 | `2024-08-23T14:30:00Z` 至 `2026-08-20T20:00:00Z` |
| 历史上下文 | Dataset 保存正式窗口前的日/周行情事实；Sampler 在首个正式小时 cycle 通过 `bootstrapHistory` 端口一次性交给 Module |
| 行情时段 | 美股常规交易时段（RTH），不含盘前盘后 |
| 每个 Dataset | 小时 3469 行、日 1080 行、周 225 行；无 `bars.csv`、无 warm-up observation；共 3469 cycles |
| 纳指口径 | Nasdaq Composite (`^IXIC`)，不是 Nasdaq-100 (`^NDX`) |

Preview 中的三个 Dataset 已安装为本轮修复后的 current sealed version，不需要用户上传或 Replace。
旧 Version 会继续保持 sealed，但不会出现在 Backtest 顶层装载下拉框中：

| 界面名称 | Dataset ID | current Dataset Version content hash |
|---|---|---|
| `TLM01D02 Eval · S&P 500 · RTH 1h/day/week` | `tlm01d02-eval-spx-rth-20240823-20260820` | `sha256:614fbf3236db19a0971d5bc1a210efd369374f479b979b4e92eea890b604c9b9` |
| `TLM01D02 Eval · Nasdaq Composite · RTH 1h/day/week` | `tlm01d02-eval-nasdaq-composite-rth-20240823-20260820` | `sha256:d3ad4595d6e211a0a88dc369fe630025e98422346ef9255bdb51c5e81a85464f` |
| `TLM01D02 Eval · NVIDIA · RTH 1h/day/week` | `tlm01d02-eval-nvda-rth-20240823-20260820` | `sha256:6b32a133894a3fdec26f29caa015c036db5a6bb4d3954e90dcde76619189ea20` |

Visualizer 目录必须精确提供以下 11 个定义：`ohlc.candles`、`series.line`、
`series.scatter`、`series.histogram`、`overlay.markers`、`overlay.priceLine`、
`drawing.horizontalLine`、`drawing.trendLine`、`drawing.rectangle`、
`drawing.brush`、`drawing.text`。本手册使用的显式绑定如下：

| 图层 | Data | Time | Time Domain | Price Scale / Target |
|---|---|---|---|---|
| Hour Candles | `tlm1d02.market.bar.hour`（对象内 `eventTime`） | 不适用；禁止使用 `decisionTime` fallback | `tlm1d02.market.hour` | `market-price` |
| Day Candles | `tlm1d02.market.bar.day`（对象内 `eventTime`） | 不适用；禁止使用 `decisionTime` fallback | `tlm1d02.market.day` | `market-price` |
| Week Candles | `tlm1d02.market.bar.week`（对象内 `eventTime`） | 不适用；禁止使用 `decisionTime` fallback | `tlm1d02.market.week` | `market-price` |
| Research Line | `tlm1d02.research.currentPrice` | `time` | `tlm1d02.research` | `research-price` |
| Drawing | 无 DataKey 读取 | 无 | 继承目标 | 必须显式选择目标 Visualizer instance |

每个 Visualizer 的 Result 投影请求只能包含该 instance 的 input-port DataKey。不得请求
`cycles`、`cycles.data`、其他 Visualizer 的 DataKey 或 pane 级 union；Drawing 没有 input port，
因此添加、选择、拖拽或删除 Drawing 时不得产生 Result data POST。

## 线性操作步骤

1. 在浏览器地址栏输入 `http://10.130.130.66:30809` 并回车。预期进入 TradeEngine 登录页；使用现有 Preview 账号登录后，左侧依次能看到 `Data`、`Visualizer` 和 `Backtest`，不能看到 `Mining`。

2. 点击左侧 `Data`。预期页面标题为 `Data Filesystem`，根目录中出现上表所列三张 `TLM01D02 Eval` Dataset 卡片；不要点击 `Replace`。

3. 依次双击三个 Dataset 卡片并查看 current sealed evidence。预期每个 current Version 只显示 `hour/<instrument>.csv`、`day/<instrument>.csv`、`week/<instrument>.csv`、`basic_workflow.json` 和 capability 文件，不能出现 `bars.csv`、`warmup_observation` 或 TLM 专属 capability；查看完成后返回 `Data Filesystem`。

4. 点击左侧 `Visualizer`。预期 `Visualizers` 文件系统精确出现上文列出的 11 个定义；本页是只读目录。

5. 点击左侧 `Backtest`。预期看到 `Backtest Entry` 五节点图，节点顺序为 Dataset、Sampler、Environment、Pipeline、Analysis，底部按钮显示 `Build`。

6. 在 `Pipeline` 下拉框选择唯一显示的 `TLM1D02 D13 Causal Strategy · v2`。预期下拉框中不出现 `v1`。

7. 在 `Dataset` 下拉框选择 `TLM01D02 Eval · S&P 500 · RTH 1h/day/week`。预期 Dataset 节点显示 current sealed evidence，而不是 `No sealed evidence available`。

8. 在 `Sampler` 下拉框选择 current strict-event-time Version。若只有旧 `v3`，立即停止验收并先发布新 Version。不要打开 `Configure`，也不要填写 `sourceInstrumentId`、`evaluationStart` 或 `evaluationEnd`；预期 Sampler 保持零参数，instrument 由所选单标的 Dataset 自动推断。

9. 在 `Environment` 下拉框选择唯一显示的 `TLM1D02 Market Environment · v2`。预期下拉框中不出现旧版本或另一个同义 Environment。

10. 在 `Analysis` 下拉框选择唯一显示的 `TLM1D02 D13 Performance · v2`。预期下拉框中不出现 `v1`。

11. 点击底部 `Build`。预期状态先变为 `Checking exact Backtest configuration…`，随后标题变为 `Build complete`，说明文字为 `Configuration built · ready to run` 或 `Cached Build reused · ready to run`，按钮变为 `Run Backtest`。

12. 点击 `Run Backtest`。预期 `Backtest Jobs` 新增一张 S&P 500 job 卡片，状态依次经过 `queued`、`counting`/`running`，进度最终为 `3,469 / 3,469 cycles`，状态为 `completed`；任何 `failed` 或红色错误都判定本轮失败。

13. 在 `Backtest Filesystem` 双击名称同时包含 `TLM1D02 D13 Causal Strategy`、`S&P 500` 和 `Result` 的最新卡片。预期进入 Result 页面；顶部上下文栏依次显示 Backtest ID、Dataset、Sampler、Pipeline、Environment 和 Analyzer 的精确资源身份，不显示 Cycles、Annualized、Sharpe、Max Drawdown 或 TZ。

14. 点击 `Add Chart`。在新出现的 `Custom Chart 1` 的 `Data Display` 下拉框选择 `Candles`，按上表填写 Hour Candles 的 `Data`、`Time Domain` 和 `Price Scale`，保留默认颜色，点击 `Add Visualizer`。预期短暂出现 `Loading chart data`，随后出现小时蜡烛图，视图工具栏标记为 `Intraday`。若 `Data` 候选中没有该 key，检查 Result schema；禁止改用 cycle `decisionTime` 绕过。

15. 再点击页面顶部 `Add Chart`。在 `Custom Chart 2` 中选择 `Candles`，按上表填写 Day Candles 的三个显式参数，点击 `Add Visualizer`。预期出现按日去重的蜡烛图；同一完成日线在多个小时 cycle 中重复携带时只能显示一根蜡烛。

16. 再点击 `Add Chart`。在 `Custom Chart 3` 中选择 `Candles`，按上表填写 Week Candles 的三个显式参数，点击 `Add Visualizer`。预期出现按周去重的蜡烛图，界面没有 duplicate-time 或 Lightweight Charts 错误。

17. 再点击 `Add Chart`。在 `Custom Chart 4` 中选择 `Line`，按上表分别填写 `Data`、`Time`、`Time Domain` 和 `Price Scale`，保留默认颜色和宽度，点击 `Add Visualizer`。预期出现策略当前研究价折线；缺少任一字段时 `Add Visualizer` 必须保持不可用，不能自动使用 `decisionTime`。

18. 查看 Result 顶部动作栏和四张图的视图工具栏。预期不存在 TZ 按钮或时区输入框；页面只保留 `Back to Backtest`、`Save Layout`、`Add Chart` 以及每张图自身的范围、适配和比例控制。

19. 在 `Custom Chart 1` 右上点击 `Open Chart`。预期新标签页只打开这一张小时图，地址包含当前 `backtestId` 和 pane identity；Drawing target 下拉框显示唯一的 `Candles · <visualizer instance id>`，不能只显示 DataKey 或含糊的 callback 名称。

19.1 选择 `Horizontal Line`，显式选择该 Candles instance 作为 target，然后在图表价格位置单击。预期立即出现一条水平线和一个 Drawing tag；切回 `Select`，点击该线并上下拖拽，线的 visualizer instance id 保持不变，保存的 `price` 随指针更新。

19.2 选择 `Trend Line` 和同一 target，依次在图表两个位置单击。预期第二次单击后才提交趋势线；切回 `Select`，拖动任一端点，instance id 保持不变且只更新对应的 `{time, price}`。按 `Escape` 必须取消尚未完成的两点操作，不能提交半条线。

19.3 选中刚创建的 Drawing，分别验证工具栏 `Delete` 与键盘 `Delete`/`Backspace` 可删除；未选中 Drawing 时删除操作不得影响 Candle。tag 的移除叉只在 hover 或 keyboard focus 时出现。完成后关闭新标签页并回到 Result 页面。

20. 点击 `Save Layout`。预期没有红色错误；当前 Result 的 visualization spec 被保存并保留 4 个 panes。

21. 点击 `Back to Backtest`。预期回到 `Backtest Entry`，上一轮 job 仍为 `completed`。

22. 只在 `Dataset` 下拉框改选 `TLM01D02 Eval · Nasdaq Composite · RTH 1h/day/week`，不要修改 Sampler 或其他资源。预期底部提示配置已变化并要求重新 `Build`。

23. 点击 `Build`。预期再次得到 `Build complete` 和 `ready to run`，按钮变为 `Run Backtest`。

24. 点击 `Run Backtest`。预期 Nasdaq Composite job 最终显示 `3,469 / 3,469 cycles` 和 `completed`；出现 `failed` 即判定本轮失败。

25. 在 `Backtest Filesystem` 双击名称同时包含 `TLM1D02 D13 Causal Strategy`、`Nasdaq Composite` 和 `Result` 的最新卡片。预期顶部上下文栏显示当前 Backtest ID、Nasdaq Composite Dataset 以及本轮精确的 Sampler、Pipeline、Environment 和 Analyzer 身份。

26. 点击 `Add Chart`，在 `Custom Chart 1` 选择 `Candles`，按上表填写 Hour Candles 的三个显式参数，点击 `Add Visualizer`。预期出现 Nasdaq Composite 小时蜡烛图。

27. 点击 `Add Chart`，在 `Custom Chart 2` 选择 `Candles`，按上表填写 Day Candles 的三个显式参数，点击 `Add Visualizer`。预期出现无重复时间错误的日蜡烛图。

28. 点击 `Add Chart`，在 `Custom Chart 3` 选择 `Candles`，按上表填写 Week Candles 的三个显式参数，点击 `Add Visualizer`。预期出现无重复时间错误的周蜡烛图。

29. 点击 `Save Layout`。预期保存成功且无红色错误，然后点击 `Back to Backtest` 返回回测入口。

30. 只在 `Dataset` 下拉框改选 `TLM01D02 Eval · NVIDIA · RTH 1h/day/week`，不要修改 Sampler 或其他资源。预期底部要求重新 `Build`。

31. 点击 `Build`。预期再次得到 `Build complete` 和 `ready to run`，按钮变为 `Run Backtest`。

32. 点击 `Run Backtest`。预期 NVIDIA job 最终显示 `3,469 / 3,469 cycles` 和 `completed`；出现 `failed` 即判定本轮失败。

33. 在 `Backtest Filesystem` 双击名称同时包含 `TLM1D02 D13 Causal Strategy`、`NVIDIA` 和 `Result` 的最新卡片。预期顶部上下文栏显示当前 Backtest ID、NVIDIA Dataset 以及本轮精确的 Sampler、Pipeline、Environment 和 Analyzer 身份。

34. 点击 `Add Chart`，在 `Custom Chart 1` 选择 `Candles`，按上表填写 Hour Candles 的三个显式参数，点击 `Add Visualizer`。预期出现 NVIDIA 小时蜡烛图。

35. 点击 `Add Chart`，在 `Custom Chart 2` 选择 `Candles`，按上表填写 Day Candles 的三个显式参数，点击 `Add Visualizer`。预期出现无重复时间错误的日蜡烛图。

36. 点击 `Add Chart`，在 `Custom Chart 3` 选择 `Candles`，按上表填写 Week Candles 的三个显式参数，点击 `Add Visualizer`。预期出现无重复时间错误的周蜡烛图。

37. 点击 `Add Chart`，在 `Custom Chart 4` 选择 `Line`，按上表填写四个显式参数，点击 `Add Visualizer`。预期出现策略当前研究价折线。

38. 点击 `Save Layout`。预期保存成功且无红色错误。至此才结束验收；最终应有三张 `completed` job 卡片、三份可打开的 Result，以及三份已保存的 visualization spec。

## 结果解释边界

本轮是应用协议、资源组合、Result 投影和 Visualizer 的联调验收，不是 TLM01D02 跨资产有效性结论。
准备包只有 RTH K 线，未伪造 FVRP；Sampler 的 `fvrpProxyRow` 明确为空。正式窗口前的日/周行情仍是
Dataset 事实，但不会成为 cycle，也不会带 `warmup` 标签。当前 TLM01D02 Environment 与
Analysis 还沿用 SPX 验收时的本金、点值、摩擦成本和任务语义，因此三个 Result 的收益率、Sharpe、
回撤或 episode 数只能用于判断“是否稳定产出并可视化”，不能横向排名，也不能与原
`tlm1d02-causal-market`（含 CFD 24h/FVRP）的验收指标作数值复现。

## 数据复现与证据

本地生成入口为：

```bash
python3 scripts/prepare_tlm01d02_evaluation_data.py --reuse-raw
```

证据清单位于
`artifacts/dataset-workspaces/tlm01d02-evaluation-20260821/evidence.json`。小时行情的 provider bar
起点被移动到 `min(start+1h, regularSessionClose)` 后才视为可用；正式窗口日线由已结束的小时线聚合，
历史日线是独立 provider 事实，周线再由两段日线事实合并聚合。三份最终 Draft 全量验证报告位于
`artifacts/agent-workspaces/tlm01d02-context-bootstrap-v2/validation/`，均为 3469 cycles、`completed`。
NVDA 测试窗口始于 2024-08-23，晚于其 2024-06-10 生效的 10:1 拆股，因此测试窗口内
没有拆股断点。数据定义参考 [S&P 500 官方页面](https://www.spglobal.com/spdji/en/indices/equity/sp-500/?index=&p=)、
[Nasdaq Composite 官方页面](https://indexes.nasdaqomx.com/Index/Overview/comp)、
[NVIDIA 2024 拆股 FAQ](https://investor.nvidia.com/files/doc_downloads/2024/06/nvidia-2024-stock-split_faq_investors.pdf)
及对应 Yahoo Finance chart 响应；精确请求 URL 和原始响应 SHA-256 已写入证据清单。
