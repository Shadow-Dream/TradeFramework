# Basic 图表横轴与 Logo 验收报告

用户原始问题：

> 这个横轴好像看不到年份？另外左下角trading view那个超链接logo影响操作了，可以删掉那个

问题记录时间：2026-08-28 04:25:00 EDT（UTC-04:00，按本轮开始执行日志记录）

回答记录时间：2026-08-28 04:34:21 EDT（UTC-04:00）

## 1. 已确认事实

Basic 当前只有日线周期，但 Result 的 daily decision time 带有 16:00。原页面直接采用 `paneTimeInfo.showTime`，因此 Lightweight Charts 将日线横轴格式化成“月/日 时:分”，没有显示年份。现在 Basic 日线显式使用日期模式，横轴与十字光标日期均显示完整 `MM/DD/YYYY`；未来真正的日内周期仍可保留时分。

本地 vendor 是 Lightweight Charts 5.2.0，其 layout 默认 `attributionLogo=true`，并公开支持 `attributionLogo=false`。现已在共享 `createFinancialChart` 默认 layout 中关闭该项，没有用 CSS 覆盖、隐藏或拦截点击。

按 strategy-development 边界，本次只修改图表展示层与测试，没有修改 Dataset、Sampler、Pipeline、Environment、Analysis、Backtest 或不可变 Result。

## 2. 确定性计算

- 格式器确定性断言：2017-01-02 → `01/02/2017`；2026-07-11 → `07/11/2026`。
- 图表构造参数断言：`layout.attributionLogo === false`。
- focused tests：`5 passed`；`chart_sparse_points_smoke.js` 通过；两个 JavaScript 文件 `node --check` 与 `git diff --check` 通过。
- 真实 Chrome 登录后打开 AAPL 2,512 根日线，横轴可见 `01/03/2017`、`01/02/2018` 至 `01/02/2026`；页面不存在 TradingView 文本或超链接，相关 API 无 4xx/5xx，`pageErrors=[]`。
- 验收截图 `/tmp/bw-axis.png` 为 1600×1111 PNG，SHA-256 `0f686403b767889594caf81bf168bafb6e2cf3362391e99d509de906a2a7a563`，不进入仓库。临时测试账号已正常 logout 并删除，剩余数为 0。

## 3. 解释、反证与不确定性

问题不是数据缺少年份，而是日线被错误选择了日内格式。截图中数据仍覆盖 2016–2026；修复格式选择后，不需要改时间戳或 Result。若未来 Basic 开放分钟线，`state.period !== "day"` 会允许其继续显示时间，这构成当前规则的明确边界。

左下角链接确实来自图表库的 attribution layer，而不是本项目绘图工具。关闭官方 option 后 DOM 中没有 TradingView 链接或文字，左下角操作区域不再被该链接占用。

## 4. 单一下一实验或 Proposal

当前请求已完成，不需要扩大实现范围。唯一建议的后续实验是在 Basic 新增分钟周期时，用同一格式断言验证：日线保持四位年份，分钟线显示日期与时分；在分钟数据真正接入前不预先增加分支。
