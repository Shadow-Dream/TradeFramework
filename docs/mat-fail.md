# Basic 图表 materialization 失败诊断

- 原始问题：`The chart could not be materialized.`
- 问题时间：2026-08-27（America/New_York；会话接口未提供该消息的精确时分）
- 回答时间：2026-08-27 16:27:12 EDT（America/New_York，UTC-04:00）
- 诊断模式：Backtest failure diagnose + Basic Dataset causality audit

## 已确认事实

用户预览显示了 `The chart could not be materialized.`，但没有附带页面 detail、Backtest ID 或 Result ID。预览的 `EngineServiceHandler.log_message()` 不记录 HTTP 请求异常，systemd journal 在失败时段也没有对应结构化错误，因此不能从旧请求恢复第一次失败的精确资源身份；本报告不虚构该 Backtest ID。

预览进程当时健康，但它是由 `watchfiles` 在 2026-08-27 15:34:23 EDT 热重载的。Python 热重载只替换运行代码，不调用 `builtin_implementations.resources.install()`；新增的 `basic-ohlcv-map-sampler` 只有 `scripts/prepare_engine_preview.py` 的受控安装流程会发布到预览控制状态。

同一时刻的真实 EODHD AAPL 响应已在仓库外临时检查：24 根唯一日线，`time >= eventTime`，OHLC finite/positive 且满足 bounds，volume finite/non-negative。随后在全新临时 Engine 控制根中安装当前 BuiltIns，并执行真实 `sync → v3 Dataset publication → Pipeline → Sampler selection → prepare/submit`，成功选择 `basic-ohlcv-map-sampler@1`。再通过隔离的真实 Engine HTTP handler、Backtest worker、Result 投影和无头 Chrome 执行实时 provider 路径，主图成功出现，页面状态为 `24 daily bars · Data through 8/27/2026, 4:15:00 PM`，`pageErrors=[]`。

为修复预览资源状态，已按仓库规定的控制锁流程短暂停止 30809，执行 `scripts/prepare_engine_preview.py`，成功安装 50 个 Engine-owned BuiltIn resource，然后重新启动 `trade-engine-preview.service`。2026-08-27 16:27 EDT 直连 `/basic-workflow` 返回 303 到登录页，符合受保护页面的健康状态。该过程没有清空受管 Dataset/Result，也没有修改生产状态。

## 确定性计算

实时输入和临时完整 open 的精确证据如下：

| 项目 | 值 |
|---|---|
| Provider | `eodhd-demo-us` |
| 请求后原始 SHA-256 | `efd6d16278b95abcee95a83e69b435a38ddca03ea15019eb93c47b96a1c2e1a7` |
| Bar 数 | 24 |
| available-time 范围 | 2026-07-27T20:15:00Z 至 2026-08-27T20:15:00Z |
| event-time 范围 | 2026-07-27T20:00:00Z 至 2026-08-27T20:00:00Z |
| Volume 范围 | 25,869,800 至 132,489,100 |
| Dataset ID | `basic-market-268a944ccfb176f578e529ab` |
| Dataset Version | `basic-market-268a944ccfb176f578e529ab@sha256:ac57fb679115fb3aa17f34917da12a62d2b877bfe4c90fac383e3457ee674f2d` |
| Dataset/bar content digest | `sha256:268a944ccfb176f578e529ab79b7e7dc4c21fa12460ce5cd340fb572f92b7bb6` |
| Sampler | `basic-ohlcv-map-sampler@1` |
| Pipeline | `pipe_01M12E8PVEFBD763GCZJR22FD0@2` |
| Pipeline content digest | `sha256:9d46c219a13c7a3fa073769ea0aff9717ca751377487bcb5ed55bcfc93664ba4` |
| Prepared snapshot hash | `sha256:f760977140afe50d6f8a3ff4c4ef86b55a181d944253df7905174450e46d4e35` |
| 实时隔离浏览器 | 1 个主 Pane，canvasCount=7，chart state hidden，pageErrors=0 |
| 预览刷新 | 50 个 BuiltIn 安装成功；重启后 HTTP 303 |

这些临时资源属于诊断控制根，不是用户旧失败 Result 的身份，也未发布到生产。EODHD 的 raw OHLC、split-adjusted volume 与 current-vintage 边界仍按 [官方 EOD API 说明](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes/) 保留；本次只证明 materialization 可运行，不证明 point-in-time 严格回测。

## 解释、反证与不确定性

证据最强的解释是：预览运行代码已认识 `eodhd-demo-us` 和 v3 Dataset，却尚未在其不可变资源目录安装新 `basic-ohlcv-map-sampler`，所以 materialization 在 Sampler 精确选择处失败。支持证据是：真实数据、全新安装后的 open、worker、Result 和 Chrome 全部通过；预览只有热重载记录；执行规定的 BuiltIn 安装后新增资源已发布。

这仍是归因而非旧请求的直接证明，因为旧页面没有提供 detail，服务也没有保存该异常。竞争解释包括浏览器持有旧 snapshot、一次性 provider/network 错误，或旧预览 Result 轮询失败。若刷新后同一 AAPL open 仍显示相同错误，便可反证“只缺 BuiltIn”；届时页面 detail 与 Network 中 `/api/subsystems/basic/instruments/open` 或 Result status 的结构化响应将成为新的第一权威失败。

因果检查没有发现泄漏：每根 bar 的 `eventTime` 是 regular close，Sampler decision axis 使用 `time=eventTime+15m`。但 EODHD 仍是 current-vintage，且 raw OHLC 与 split-adjusted volume 是 provider-native 混合口径；这些是研究质量边界，不是本次页面 materialization 的失败原因。

## 单一下一实验或 Proposal

在现有预览只做一次无缓存复验：硬刷新 `http://10.130.130.66:30809/basic-workflow`，选择 AAPL 并打开日线，不添加任何指标。预测结果是页面显示 24 daily bars 且主 K 线出现；若失败，不要连续重试，保留页面 detail，并从浏览器 Network 导出第一次失败的 open/Result JSON。该一次性结果足以确认预览资源刷新是否消除了故障，且不同时改变 Dataset、标的或指标配置。
