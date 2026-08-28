# Basic 图表、缓存与自动 Snapshot 验收报告

用户原始问题：

> 你自己不能登录？自己做的网页，而且还是测试阶段，非要卡这种莫名其妙的东西干啥

问题记录时间：2026-08-28 03:18:00 EDT（UTC-04:00）

回答记录时间：2026-08-28 04:03:00 EDT（UTC-04:00）

## 1. 已确认事实

我可以登录，也已经这么做了。验收使用临时测试账号，通过 30809 的正常登录表单进入真实 Basic 页面，没有绕过页面认证。验收结束后先调用正常 logout，再精确删除该账号及其 session；数据库复查剩余临时用户数为 0。

登录后的第一条真实浏览器失败被完整保留：`POST /api/subsystems/basic/instruments/open` 返回 HTTP 400，错误为 `Pipeline Module 'universe' input 'price' requires object, but DataKey 'price' is object.`。报错只展示最外层 JSON 类型，掩盖了内部 bar schema 的差异。旧 v2 Pipeline 冻结的 Universe 需要不含 volume 的精确 OHLC bar，而一度被装入的通用合同允许额外 volume；两者最外层虽都是 object，递归合同并不兼容。

修复后 v2 和 v3 使用分离合同：v2 `BAR_SCHEMA` 精确为 `eventTime/open/close/high/low`；v3 使用 volume 必填的 OHLCV；Environment 输入使用可兼容两者的 optional-volume 上界；v3 使用独立 `basic-ohlcv-price-map-universe`，不再修改 v2 Universe 的含义。

浏览器随后暴露了两个独立问题，均已修复。第一，Result projection 错从 `get_backtest_meta()` 读取并不存在的 `resultContentDigest`，导致已完成 Result 被误报为未完成；现改用实际包含不可变 digest 的 `get_backtest_result_view()`。第二，无 volume 的 v2 快照也被 UI 强制绑定 OHLCV bar selector；现只有 close 类指标在 v2 上走 `basic-price-close-selector`，volume/HLC 依赖指标明确禁用并显示“requires an OHLCV snapshot”，具备 v3 OHLCV 时才使用完整 bar selector。

Visualization 现在先读取当前已保存 revision，再决定是否创建，刷新时不再用 `expectedRevision=0` 制造一次可预期的 409。模块目录与市场数据同时加载，且同页并发调用共享一个 catalog Promise，不会重复获取目录。

## 2. 确定性计算

- 最终全新账号、正常登录、同一浏览器 session 的冷启动：19,348 ms，`materializationHit=false`，图表成功生成。
- 在页面目录中点击 Bollinger Bands，默认参数为 Length 20、StdDev 2；9,417 ms 后显示 `BB added.`。
- 同一 session 刷新：9,811 ms，`materializationHit=true`；状态精确显示 `Loaded saved Visualization · Materialization cache hit`，BB 20/2 保留。
- 刷新后有 7 个 canvas，Pane 为 `AAPL · day`，chart state 已隐藏，session projection cache 为 1,722,967 bytes；相关 `/api/` 请求没有 4xx/5xx，`pageErrors=[]`。
- 自动 snapshot 使用真实页面给未收藏且无数据的 AA 点星。Watchlist API 返回 HTTP 200、`accepted=true` 和 queued job；页面在 3,327 ms 显示 `Fetching automatic snapshot…`，8,939 ms 显示 `Snapshot cached · 8/27/2026, 4:00:00 PM`，状态为 completed。
- 完整回归集合覆盖协议、BuiltIn、v2/v3 Dataset、market service、HTTP API 和 Basic Web，共 `72 passed`，耗时 308.58 秒；最后的并行加载改动另复跑 `25 passed`。`node --check`、`py_compile`、`git diff --check` 均通过。
- 最终图表截图为 1280×1031 PNG，SHA-256 `1f6d8411e2dfc07eebbd176cbcc8aa701417299c14b49d91180c728732cd8274`；自动 snapshot 页面截图为 785×749 PNG，SHA-256 `35412e1fb9910ffdddae76c139e60ff7f83caf7a7ec2c31182b8d04e0a16e64b`。截图仅保存在 `/tmp/bw-chart.png` 与 `/tmp/bw-home.png`，不进入仓库。

## 3. 解释、反证与不确定性

原始报错不是“object 不能传给 object”，而是错误信息丢失了递归 schema 差异。若只改错误文案、只重建 Pipeline 或只清缓存，都会再次失败；真实浏览器先后暴露 Universe、projection digest 和 selector 三个边界，说明必须逐层修正并实际点击验证。

TradingView 的布林带接近瞬时出现，主要因为它在已驻留的价格数组上直接做增量/向量计算并立刻绘图。当前 Basic 首次添加 BB 会把 temporary Module 作为普通 Engine projection，在 2,512 个 cycle 上执行、验证合同、保存 Visualization revision，再返回图层，因此 9.4 秒不是布林带公式本身的计算时间。

缓存已经真实生效：刷新没有重新提交 Backtest，materialization 命中；已有指标 Result slice 从浏览器 session cache 恢复，BB 与 Visualization revision 都保留。但 9.8 秒仍明显慢于 TradingView。一次暖加载的可分离成本中，严格缓存命中校验约 3 秒，market catalog 响应约 1.2–3.7 秒，约 1.4 MB 的 Module/Environment/Analysis 目录约 2–3 秒，其余为 JSON 解析和 canvas 重建。也就是说，功能性缓存已完成，但“接近一秒”的性能目标尚未完成，不能把这两个结论混为一谈。

自动 snapshot 当前保存的是 provider 的当前版本日线，适合图表和指标接线；它不提供历史 revision vintage，不能据此宣称严格 point-in-time 回测。无 volume 的 Nasdaq v2 快照不会伪造 volume，量价指标需使用 v3 OHLCV 数据。

## 4. 单一下一实验或 Proposal

下一轮只做一个受控性能实验：为 workspace 增加 instrument-scoped bootstrap 和带版本指纹的 catalog cache，使缓存打开不再下载 2.3 MB 全市场目录与约 1.4 MB 全 Module 目录；保持现有不可变 Result、session 所有权和 schema 校验不变。以同一 AAPL/BB 结果连续打开 20 次，对比端到端 p50/p95，验收目标先定为暖打开 p95 小于 2 秒。若仍超过目标，再根据分段计时决定是否增加专用的向量化指标 projection，而不直接修改 Engine 执行语义。
