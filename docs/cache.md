# Basic Pipeline 多版本与 Visualization 缓存报告

- 原始问题：

  > Pipeline scaffold received duplicate Module: Universe/basic-price-map-universe
  >
  > 另外这个Visualization结果应该做个缓存才对，不然每次进来都要重新构建

- 问题时间：2026-08-27（America/New_York；会话接口未提供该消息的精确时分）
- 回答时间：2026-08-27 16:56:17 EDT（America/New_York，UTC-04:00）
- 目标 observable：相同用户 session、相同标的/周期和相同 bar 内容第二次进入时，精确复用 Dataset Version、Backtest 和当前 Visualization；bar 内容变化时才创建新组合。

## 已确认事实

错误来自 application-layer scaffold 的版本归并规则，不是 `Universe/basic-price-map-universe` 被同一 Pipeline 放了两次。预览安装 BuiltIn 后，同一 `kind/moduleId` 合法存在多个不可变版本；原 `_definition_index()` 只按 `(kind,moduleId)` 建索引，见到第二个版本便抛出 `duplicate Module`。这与 Engine “精确版本冻结”的资源模型冲突。

修复后，新 managed Pipeline 对每个 required Module 确定性选择最高数值版本，并把该精确版本写入 Pipeline draft；同一 `(kind,moduleId,version)` 若重复仍 fail closed。已有 managed Pipeline 不会被新安装版本推动：应用先加载它持有的精确 Pipeline Version，再按该 Pipeline 实例中冻结的 Module version 找回定义并重建期望 scaffold。冻结版本缺失、Pipeline digest 被篡改或 scaffold 行为变化仍要求显式 rotation，不能因缓存绕过。

Basic materialization 现有持久缓存，状态文件为 application-owned `materializations.json`。缓存 key 包含：登录 session 的二次摘要、provider、instrument、period 和稳定 bar data digest。稳定摘要包含 provider、完整 instrument 描述、period、全部 bar 以及 v3 adjustment/revision policy；特意排除 `retrievedAt`、`asOf` 和 raw artifact hash 等非行情内容时间戳。因此同一批 bar 的重新下载会命中，任一 OHLCV、时间键、标的描述或口径政策变化都会失效。

缓存记录不保存原始 bars，也不保存 session token；只保存 session 摘要、稳定数据摘要和已经过 API 投影的 materialization response。命中前会重新验证：canonical Dataset Version 与 lineage、managed Pipeline 精确引用和 content digest、Sampler/Environment/Analysis 精确版本、Backtest composition、Visualization save request、Prepared digests，以及当前 Job 的 `jobId/backtestId`。任何缺失或篡改都报错，不会静默选择 latest 或提交替代 Backtest。

重复进入仍会下载一次 provider 数据以判断是否更新，但不会重新发布 Dataset、归档 Pipeline 或执行 Backtest。completed、queued、running 和 failed job 都按同一身份返回；尤其 failed job 不会被隐式重试。不同登录 session 不共享 Backtest/Visualization，以免跨用户暴露私有结果。

Web 对已保存 Visualization 的 revision-0 CAS 探测仍保持不合并语义；命中 current revision 后直接加载服务器当前 spec。原来的红色文案 `Visualization already changed elsewhere` 已改为正常状态 `Loaded saved Visualization`。如果 conflict 没有有效 current record，仍按错误处理。

## 确定性计算

自动测试结果如下；测试组有覆盖交叉，因此不求和成虚假的唯一总数：

| 验证组 | 结果 |
|---|---:|
| Protocol scaffold + Basic market service/cache | 21/21 通过 |
| Dataset、v3 Adapter、BuiltIn、Backtest E2E、资源边界 | 23/23 通过 |
| HTTP subsystem、Basic API、Web workspace、20 指标 Pane | 39/39 通过 |
| Web 缓存文案与工作区 focused rerun | 27/27 通过，1 条既有 escape deprecation warning |
| Python compile、Node check、diff check | 通过 |

缓存测试明确覆盖：

| 条件 | 提交次数/结果 |
|---|---|
| 同 session + 同 bar 内容，连续 open | Backtest submit 从 1 保持 1；Backtest ID 与 Visualization request 相同 |
| 不同 session + 同 bar 内容 | submit 增为 2；Backtest ID 不同 |
| 原 session + 最后一根 close 改变 | submit 增为 3；Dataset Version 与 Backtest ID 均改变 |
| 改变后的 job 标记 failed，再次 open | submit 保持 3；返回同一 failed job 和原始 error |
| 安装更高 Module versions 后重开旧 Pipeline | Pipeline ID/version/digest 不变；submit 不增加 |
| managed Pipeline digest/scaffold 被篡改 | 缓存不绕过，仍在提交前失败 |

最终隔离 Chrome smoke 故意在同一控制根安装两遍全部 BuiltIn，以复刻预览的多版本现场；随后使用真实 EODHD AAPL 24 根 OHLCV 连续进入两次。两次精确身份为：

| 资源 | 两次共同身份 |
|---|---|
| Dataset Version | `basic-market-8243cc705b3b329a87e6068a@sha256:ac57fb679115fb3aa17f34917da12a62d2b877bfe4c90fac383e3457ee674f2d` |
| Backtest | `bt_01M12G32P7GGDJKYQVJYX53K6Q` |
| Visualization | `bt_01m12g32p7ggdjkyqvjyx53k6q-current` |

两次均为 1 个主 Pane、canvasCount=7、`pageErrors=[]`。第二次页面状态精确为 `24 daily bars · Data through 8/27/2026, 4:15:00 PM · Loaded saved Visualization`。预览服务已热重载当前实现，`/basic-workflow` 直连返回 303 登录重定向。

## 解释、反证与不确定性

该缓存避免的是昂贵且产生新资源身份的 Dataset/Pipeline/Backtest/Visualization 重建，不是完全离线缓存。每次进入仍调用一次有界 provider 下载，因为不读取 provider 就无法证明日线是否增加或被修订。若要求“零网络立即打开”，必须另行定义 freshness TTL 和手动 Refresh；否则会把旧行情伪装成当前结果。

缓存按 session 隔离意味着注销、session 失效或换浏览器后会创建新的 Backtest，即使数据相同。这是隐私优先的选择，不是技术限制。若未来需要跨 session 用户级缓存，SubsystemContext 必须提供稳定且非敏感的 user authority identity，不能把 token 或猜测出的用户 ID写入应用状态。

当前只保留每个 session/provider/instrument/period 的最新数据摘要，因此同一 key 的新行情会覆盖旧 cache reference；不同 session 的记录暂未设置自动过期策略。缓存记录很小且不含 bars，但长期多用户部署仍需要显式 retention/eviction 方案。

缓存 job 若已失败或精确 Result/资源被删除，会 fail closed，并要求显式 cache invalidation；本轮没有增加自动重试或隐藏 fallback。这个边界防止首次失败被新运行覆盖，但后续应提供用户可见的 `Refresh/Rebuild` 操作来主动换代。

本轮修改 application protocol、Web 状态文案和测试，没有改变 Engine stages、Module 生命周期或 Backtest 执行语义，也没有发布新的生产不可变版本。

## 单一下一实验或 Proposal

在当前预览的同一登录 session 只做一次 AAPL 双进入实验：第一次硬刷新后打开 AAPL，记录 Result 链接中的 Backtest ID；返回 Basic 首页后再次打开 AAPL。预期第二次显示 `Loaded saved Visualization`，Backtest ID 不变且几乎立即出现图表。若 ID 改变，保留两次 open response 的 `barSnapshot.contentDigest` 与 Dataset Version；digest 不同证明 provider 数据变化使缓存正确失效，digest 相同而 ID 不同才反证缓存实现。
