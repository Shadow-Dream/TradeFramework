# 基础指标目录与首轮可视化接入报告

- 原始问题：`先不要管这些了。开subagent先尽可能将容易接入的指标先接进去吧。注意参考tradingview的形式，比如SMA不要20一个50一个，这种最好支持传入参数 参考tradingview的指标列表形式，不要像现在一样散在外面`
- 问题时间：2026-08-27 14:04:23 EDT（America/New_York，UTC-04:00）
- 回答时间：2026-08-27 14:27:31 EDT（America/New_York，UTC-04:00）

## 已确认事实

TradingView 官方文档确认，其图表通过单一 “Indicators, metrics, and strategies” 入口或 `/` 快捷键打开指标目录；目录包含 Favorites、Technicals 等分类，点击条目后加载实例。指标添加到图表后，通过图例中的 Settings 管理参数。官方 Inputs 文档还明确用一个 MA 条目配合可修改的 Source、Length 演示参数化，而不是把 MA20、MA50 拆成两个目录项：

- https://www.tradingview.com/pine-script-docs/primer/first-steps/
- https://www.tradingview.com/pine-script-docs/concepts/inputs/
- https://www.tradingview.com/support/solutions/43000506677-how-do-i-remove-or-customize-an-indicator/

本轮已经把 Basic workspace 原来的 SMA20、EMA20、BB20 三个散列按钮替换为一个 `Indicators` 入口和集中式目录。目录提供 Favorites、Technicals、搜索、参数表单；图表栏只显示已加载实例，并为每个实例提供 Settings 与 Remove。

当前 renderer 只正确渲染 K 线所在的单一价格 Pane，因此首轮开放的最大安全集合是：

| 目录项 | Module | 参数来源 | 主图输出 |
|---|---|---|---|
| SMA | `sma-indicator` | `configSchema.period`，默认 20，整数且 ≥1 | 1 条线 |
| EMA | `ema-indicator` | `configSchema.period`，默认 20，整数且 ≥1 | 1 条线 |
| WMA | `wma-indicator` | `configSchema.period`，默认 20，整数且 ≥1 | 1 条线 |
| Bollinger Bands | `bollinger-bands-indicator` | `period`、`k`，默认 20、2 | 上/中/下 3 条线 |

同一目录项可以创建任意多个实例。例如 SMA 20 与 SMA 50 分别生成 `basic-indicator-sma-1`、`basic-indicator-sma-2`；周期不再进入指标类型身份。编辑参数保留原实例 ID、精确 Module version 和输出 DataKey。旧的 `basic-indicator-sma20` 等已保存实例仍可识别、编辑和删除。

参数默认值、类型和上下界来自已安装 Module 的 JSON Schema；UI 只增加 `Length`、`StdDev` 展示名。写入仍使用当前 canonical revision 的 CAS：成功后清 projection cache 并重绘一次；409 只接受服务器 current record，不做本地合并；画线保存排队、在途或失败时禁止提交指标写入。

## 确定性计算

- 目录条目数：4；固定周期条目数：0。
- 同图添加 SMA20、SMA50、WMA34、BB(30, 2.5) 时，共产生 4 个指标 Module 实例、1 个共享 close selector、6 条 `series.line`。
- 删除一个实例只删除它自己的 Module 与输出线；共享 selector 只在最后一个指标删除时移除。
- period=0、period=2.5 和额外配置字段都在保存请求前失败。
- 身份冲突、重复 DataKey、缺失输出线和错误 close selector 均 fail closed。
- 桌面浏览器布局冒烟测得目录弹层为 720×361 px，位于 1440×1000 viewport 内；目录列、参数列和已加载图例均无裁切。

验证结果：

| 验证组 | 结果 |
|---|---:|
| Basic workspace + Module forms | 28/28 通过 |
| BuiltIn + Visualization contracts | 10/10 通过 |
| Visualization repository/result/service | 14/14 通过 |
| 合计 | 52/52 通过 |

`node --check web/basic_workflow_workspace.js` 通过。第一组测试包含纯 spec add/update/remove、多实例、旧实例兼容、非法参数、CAS 成功/409/失败以及真实 Engine visualization compile。

## 解释、反证与不确定性

RSI、MACD、ROC、ATR、Stochastic 没有在本轮强行接入，因为当前 workspace 只绘制 candle Pane，且指标线复用 K 线价格尺度。把振荡器直接叠到价格尺度会压缩主图或产生错误视觉语义。只要后续 renderer 能遍历并同步多个 Pane，这个限制即可被反证并解除。

VWMA 与 OBV 还缺 Basic Dataset 的 volume；ATR、Stochastic 还缺 OHLC 标量选择器。它们不能只靠增加前端目录项得到正确结果。

现有 Bollinger `k` 契约没有非负约束，因此 UI 忠实接受 Engine Schema 允许的任意有限 number；如果要禁止负数，应发布新的 Module 契约版本，而不是只在页面偷偷加限制。

浏览器布局冒烟已完成，但共享已登录 Chrome 的 DevTools 端点在真实预览写入试验期间无响应，因此本报告不宣称完成了“真实登录会话中的点击保存”验收。CAS、资源编译和重绘路径已经由自动测试覆盖，真实页面仍需一次人工点击确认。

## 单一下一实验或 Proposal

在已登录预览页只做一个受控实验：同一标的从目录连续添加 SMA 20 与 SMA 50，确认两条线与两个图例同时存在；把 SMA 20 改成 37，确认实例身份不变；最后删除两者，确认共享 selector 随最后一个实例消失。该实验通过后，再单独立项多 Pane renderer，首个副图只接 RSI，避免同时改变指标口径和 Pane 基础设施。
