## 已确认事实

用户原始问题：

> 还是开不了Pipeline Module 'universe' input 'price' requires object, but DataKey 'price' is object.
> 确认能开后再跟我说解决
>
> 另外还是需要解决一下性能的问题。比如Tradingview中计算布林带，基本是一秒不到就出现。在我们这个系统中需要比较长的时间，这是什么原因

- 问题记录时间：2026-08-27 17:21:17 EDT（UTC-04:00，按首次对应服务错误日志）。
- 回答时间：2026-08-27 18:04:46 EDT（UTC-04:00）。
- `object` 对 `object` 的报错文字只显示了 JSON Schema 的顶层类型，隐藏了对象内部字段差异。真正原因是 Basic 托管 Pipeline 过去只以 `period=day` 为身份键：旧 v2 Pipeline 已冻结旧 `price` 对象合同，EODHD/OHLCV v3 再次打开时却错误复用了它。
- 已将托管 Pipeline 身份改为 `period + profile`。legacy 继续使用 `day`；OHLCV v3 使用 `day-ohlcv-v3`。旧状态中没有 `profile` 字段的记录会规范化为 legacy，不修改原冻结 Pipeline。已有错误缓存若指向另一 profile 的 Pipeline，会视为 cache miss 并重新物化。
- 修复位置为 `application_protocols/basic_workflow/market_service.py:1572` 和 `application_protocols/basic_workflow/market_service.py:1633`；兼容回归在 `tests/application_protocols/test_basic_market_service.py:516`。
- 原 BB 性能路径确实重复计算：upper、middle、lower 的临时模块依赖相同，但三个输出 path 分别触发一个隔离 Result Runtime。实测三次请求分别约 1,051、1,202、1,262 ms。
- 现在按相同 `temporaryModules` 依赖图合并所有输出 path，一次 Runtime 返回三条 BB 线；同时共享进行中请求、保留已完成结果，并将不可变 Result slice 写入按登录用户隔离的 `sessionStorage`。限制为 24 条、4,000,000 字节。切换图表和加载已保存 Visualization 不再清除有效缓存。
- 性能实现位于 `web/basic_workflow_workspace.js:452`、`web/basic_workflow_workspace.js:1350`；三条不同 BB path 合并及缓存恢复回归在 `tests/web/test_basic_workflow_subsystem.py:1265`。
- 已用生产 HTTP Handler、真实资源仓库、真实 Backtest worker、真实 Result Runtime 和真实 Chrome，预置 legacy Pipeline 后打开 120 根 OHLCV AAPL 工作区。legacy Pipeline `pipe_01M12KPZJ5V2QFFPHCBAG8VNZF` 与 v3 Pipeline `pipe_01M12KQ226GEKJ1RAA89TAFWST` 不同；工作区、蜡烛图和 `BB 20 · 2` 均成功显示。

## 确定性计算

- 最终浏览器链：首次打开 6,076 ms；状态为 `120 daily bars`；无 Pipeline Schema 错误。
- 优化后 BB 从提交到显示为 1,159 ms，其中唯一 Result 投影请求为 625 ms；其余保存、响应处理和重绘合计 `1,159 - 625 = 534 ms`。
- Result Runtime 请求数从 3 降为 1，减少 `2 / 3 = 66.7%`。
- 单次投影与优化前最慢请求比较，从 1,262 ms 降至 625 ms，下降约 `50.5%`。这不是严格的端到端 A/B，因为优化前记录的是三次并发请求的各自耗时，不能把三者相加当成用户等待时间。
- 刷新同一工作区为 840 ms；Backtest、Dataset Version、Visualization 身份均复用；Result 投影请求数为 0。浏览器缓存实际大小为 95,775 字节、2 条，远低于上限。
- 首次打开与缓存刷新相差 `6,076 - 840 = 5,236 ms`。这部分主要是首次 Dataset/Pipeline/Backtest/Result 物化，不应归因于 BB 公式本身。
- 最终 Dataset Version 为 `basic-market-2b469ff734b38264df584ad5@sha256:429683549003dc6c6d88ace7538d6a59876998e1804bbb17edb61bb1e4ba7696`；Backtest 为 `bt_01M12KQ2J4QKSZE1WVSZJ5X6AH`。
- 最终组合回归：52 passed，其中服务层 14 passed，Web/API/OHLCV 组合 38 passed。`node --check` 与相关文件 `git diff --check` 通过。

## 解释、反证与不确定性

- TradingView 的交互式指标通常直接在已加载 K 线上以长期驻留、优化过的运行时计算，并做增量更新。当前 Trade Engine 则先保存不可变 Visualization revision，编译临时模块图，启动一次性隔离 Python Runtime，验证 Result archive，流式重放 cycles，再通过 JSON/HTTP 返回并重建图表。这些步骤提供可复现性、合同隔离和审计证据，但形成约 0.5–1 秒级固定开销。
- 所以“我们的布林带公式更慢”并不是主要解释。优化前最明显的问题是同一公式被三个输出重复启动三次；该问题已消除。现在 625 ms 的核心投影仍主要包含进程启动、合同编译和归档验证，而 534 ms 是 Visualization CAS、HTTP 和前端重绘的合计。
- 首次工作区 6.076 秒与 TradingView 点击一个已加载图表的指标不是同一口径。可比较口径是工作区已打开后的 BB 1.159 秒；它已接近但仍未稳定达到 1 秒内。
- 浏览器持久投影缓存按用户、按标签页会话隔离。刷新、返回和同标签页重新进入可命中；新标签页或浏览器重启仍会重新做一次投影。服务器侧 Dataset/Backtest materialization cache 仍会复用，因此不会重跑完整 Backtest。
- 刷新时仍会出现一次预期的 Visualization CAS 409，用于取得服务器当前 revision；一次实测约 137 ms。它不触发 Result 重算，但仍是可继续去掉的固定开销。另有无害的 favicon 404，不影响产品断言。
- 最终浏览器使用确定性的 EODHD 合同兼容 OHLCV fixture，以隔离供应商网络波动；因此这些耗时说明 Engine/应用本身的计算成本，不代表外部 EODHD 下载延迟。
- 运行中的 30809 服务已自动加载 Python 修复，未认证探测返回正常 303 登录跳转。当前容器中长期运行的共享 Chrome 网络服务发生 CDP/导航超时，因此没有借用用户账号重新操作共享标签页；最终“能打开”的结论来自同一生产 Handler 和真实浏览器的隔离持久状态测试，而不是 mock UI。

## 单一下一实验或 Proposal

下一轮只做一个受控实验：为 Result projection 设计内容寻址的服务器缓存，键严格包含 `Result contentDigest + canonical paths + exact temporary Module identities/config/bindings`，分别测 20、120、2,520 根 bar 的 cold/warm BB 延迟和缓存一致性。目标是让新标签页和浏览器重启也能复用投影，并验证 warm p95 小于 300 ms。该能力涉及 Engine Result 服务的缓存身份与失效规则，本轮未越权修改；应先形成 Proposal 和合同测试，再决定实现。
