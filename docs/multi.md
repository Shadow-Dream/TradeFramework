# Basic 主页四股稳定性实测

- 用户原始问题：`自己再测，实测主页三个股以上。现在这套东西太脆弱了，老是出bug，继续改`
- 问题接收时间（本轮首次记录）：2026-08-28 08:32:07 EDT（UTC-04:00，America/New_York）
- 回答时间：2026-08-28 09:13:50 EDT（UTC-04:00，America/New_York）

## 已确认事实

1. 已用真实 Chrome 从主页依次打开 AAPL、META、AMZN、MSFT，共 4 只股票；随后关闭首个浏览器上下文，以同一用户重新登录，在第二个独立浏览器上下文逐只复开，共 8 次完整操作。
2. 最终 8/8 次均出现 K 线 canvas 和 `daily bars` 完成状态；没有出现 Pipeline duplicate、`price requires object`、materialization failure 或 `queued · 0%` 长挂。每只图表有 7 个 canvas，股票标题、记录数与 K 线形态未串线。
3. 四只冷开各提交且只提交一个独立 Job/Backtest；四个 Job、Backtest、Dataset Version、Visualization 身份两两不同。第二登录会话全部返回 `materializationHit=true`，并逐字段复用各自冷开的 Job、Backtest、Dataset、Pipeline Version 与 Visualization，没有新增计算。
4. Job 启动排队时间分别为 14ms、14ms、41ms、15ms；最终运行态审计为四个 Job 全部 `completed`，全局 `active=0`。
5. 调度器现在保存每个 executor Future，并设置 2 秒启动看门狗：第一次确认无执行容量时，取消尚未开始的提交、替换 executor，并用同一个 Job/Backtest 重投；替换后的 executor 仍不启动时，该 Job 明确失败并释放队列，不再永久保持 queued。
6. materialization 缓存仍以稳定 user owner 隔离。已深验的响应按 `controlRoot + 响应内容摘要` 做有界进程内记忆；相同响应不重复打开全部不可变归档，缓存 JSON 任一内容改变都会生成新摘要并重新深验。篡改 Pipeline digest 的回归用例会被拒绝。
7. Workspace Catalog 从 `Visualizers + 全部 Pipeline/Environment/Analysis Modules` 收窄为 `Visualizers + Signal Modules`；指标编辑未使用的 Environment/Analysis 仓库不再随每次进图加载。Pipeline、Module、Dataset、Backtest 与 Visualization 的资源语义和冻结身份未改变。
8. 最终相关完整回归：`80 passed, 1 warning in 305.01s`。warning 是既有的 Python 无效转义弃用提示；无测试失败。最终服务 PID 3846870 正常监听，`/login` 返回 HTTP 200。

| 股票 | records | 冷开完成 | 热开完成 | 热开 POST | 排队 |
|---|---:|---:|---:|---:|---:|
| AAPL | 2,512 | 16.123s | 4.415s | 3.645s | 14ms |
| META | 2,512 | 16.240s | 4.520s | 3.736s | 14ms |
| AMZN | 2,513 | 17.127s | 4.437s | 3.672s | 41ms |
| MSFT | 2,513 | 16.146s | 4.568s | 3.876s | 15ms |

## 确定性计算

- 冷开完成时间中位数：`(16.146 + 16.240) / 2 = 16.193s`；平均值：`65.636 / 4 = 16.409s`。
- 热开完成时间中位数：`(4.437 + 4.520) / 2 = 4.4785s`；平均值：`17.940 / 4 = 4.485s`。
- 相对同轮冷开，热缓存中位完成时间减少：`1 - 4.4785 / 16.193 = 72.3%`。
- 相对本轮收窄 Catalog 前的热开中位参考值 5.3825s，最终中位数减少约 `16.8%`；热开 POST 中位从约 4.755s 降至 3.704s，减少约 `22.1%`。
- 四次热开复用四次冷开的精确身份，因此新 Job 数为 `0`；最终四个目标 Job completed，占目标 Job 的 `4 / 4 = 100%`。
- 最终截图拼图 SHA-256：`0e0123dc9a2584ba9ef64270c970aaf31592858e779b0817cf7424a4d571f15c`。截图中横轴年份可见，未出现 TradingView 超链接标志。

## 解释、反证与不确定性

- 这次实测证明的是主页四股“顺序冷开 + 跨登录顺序热开”。它不是四股同时冷提交的并发压力测试；并发容量仍为 1。调度器的 pending executor 饥饿与永久饥饿由两个确定性单元测试覆盖。
- “冷开”表示该新测试用户没有 materialization 缓存；底层 K 线 Dataset 已可能由此前 snapshot/materialization 保存，因此本轮不能解释成四次远端行情下载基准。它仍真实执行了四个新的约 2,512-cycle Backtest。
- 登录页在未认证时探测 `/auth/session` 返回一次预期 401；浏览器记录中 page errors 为空，除该精确登录探测外没有 origin 4xx/5xx。
- 热开 4.42–4.57 秒明显好于冷开的 16.12–17.13 秒，但仍没有达到 TradingView 的亚秒级体验。现在剩余的大头是 Signal Module/Visualizer Catalog 的服务端装载校验和 Result/Visualization 投影，不是 Job 排队，也不是 K 线 canvas 绘制。
- 若 Future 已进入 `running` 但线程在首次持久化 `running` 状态前卡死，Python 线程不能安全强杀；当前看门狗会保守等待而不重复执行同一个 Backtest。现有真实故障是 Future 尚未开始，已由可取消恢复路径覆盖。

## 单一下一实验或 Proposal

做一个内容寻址的 Basic Catalog 快照：服务启动时一次性验证并缓存 Basic 所需的 Visualizers 与 Signal Modules，响应携带内容摘要；浏览器按摘要跨页面复用，资源发布或服务重启后自动换摘要并重新验证。仍用本报告同一套 4 股冷开 + 第二登录热开矩阵作为唯一受控实验，成功门槛设为：热开中位数低于 2 秒、0 个新增 Job、四组冻结身份完全一致、完整相关回归全绿。
