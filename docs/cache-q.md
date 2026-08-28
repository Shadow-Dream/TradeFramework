# Basic Workflow 缓存与队列故障报告

用户原始问题：

> 现在缓存功能还是没有效果。不仅没有缓存，现在点进去还一直卡在Trade Engine queued · 0%

- 提问时间：2026-08-28（会话未提供精确时分），America/New_York
- 回答时间：2026-08-28 07:13 EDT（UTC-04:00）

## 已确认事实

1. 首个故障现场已经保留：Job `job_01M13ZSJ7XTW3292YRX8DA5BQ6`、Backtest `bt_01M13ZSJ7Y9JZGBPV1PK2J55W9` 于 2026-08-28 06:49:03 EDT 提交，长期停在 `queued / 0%`，同时服务报告无 `running` Job。
2. 服务重启恢复时，该 Job 被确定性终结为 `failed / interrupted`，错误为 `Engine stopped before this Backtest job started.`；它没有被篡改成成功，也没有被缓存继续复用。
3. 故障发生时根文件系统可用空间为 0。只清理本项目此前浏览器验收留下的 13 个可重建 Chrome 临时配置目录后，可用空间恢复到约 248 MB；未删除 Dataset、Result 或用户文件。
4. 原缓存所有者错误地使用每次登录都会变化的 `tokenHash`。现已拆分为：准备提交仍绑定临时登录会话，Materialization 与 Result projection 缓存绑定稳定 `userId`。因此同一用户重新登录可以命中，其他用户仍不能读取。
5. 失败的缓存 Job 现在会被判为失效；用户再次显式打开时会创建新的物化任务，不再永久返回失败记录。
6. 命中路径不再为缓存文件里的每条记录重新打开全部不可变归档，也不再重建整个 Module 目录和托管 Pipeline；只有选中的记录会执行完整资源一致性校验。
7. 定向回归为 `14 passed`，另有 Python 编译、Node 语法检查和 `git diff --check` 通过。真实 Chrome 使用同一用户的两个独立登录会话完成了登录、点击 AAPL、等待 Result、渲染 K 线的完整流程。

## 确定性计算

- 优化前的缓存会话：点击股票到图表可用为 21.043 秒；缓存命中接口在市场状态返回后仍耗时约 11.697 秒。
- 优化后的缓存会话：点击股票到图表可用为 5.991 秒；缓存命中接口耗时约 1.275 秒，Result projection 响应约 0.17 秒。
- 缓存接口耗时下降约 `(11.697 - 1.275) / 11.697 = 89.1%`；点击到出图耗时下降约 `(21.043 - 5.991) / 21.043 = 71.5%`。
- 冷启动会话创建 Job `job_01M140ZY37Y4CBAAJFKJMDNJ6W` / Backtest `bt_01M140ZY3791P0Q8FXM9KR618Z` 并完成 2,512 个周期；第二个独立登录会话复用完全相同的 Job、Backtest、Dataset Version 与 Visualization，没有创建新 Job。
- 第二次页面最终状态为 `Loaded saved Visualization · Materialization cache hit`；验收结束时活动 Job 数为 0。

## 解释、反证与不确定性

`queued · 0%` 的直接状态是“SQLite 已记录 queued，但执行线程未进入 running”。磁盘当时恰好为 0 可用空间，而且服务重启后把该记录识别为启动前中断；这强烈支持“入队后的持久化写入失败”解释。由于当时连失败状态也无法可靠落盘，没有留下更具体的底层异常，所以不能把磁盘写满宣称为唯一可能原因。

缓存此前并非完全没有文件，而是缓存键选择错误：换一次登录令牌就换一个所有者，用户看到的行为等同于无缓存。同时，旧命中路径会深度校验所有历史缓存记录和整个 Module 仓库，计算虽然没有重跑，读取却仍然很慢。此次修改只调整应用层缓存所有权、失效规则和命中校验范围；没有修改 Engine 的 Pipeline 执行语义，也没有跨用户共享 Result。

当前 5.991 秒仍不等同于 TradingView 的近即时体验。其中约 4 秒花在 Workspace 首次请求完整市场状态和三个通用 Module 仓库；它们不是指标计算时间。运行盘虽然已有约 248 MB 余量，但仍显示 100% 使用率，若其他进程继续写满，任何需要落盘的新 Job 仍可能失败；缓存命中本身不需要创建新 Backtest。

## 单一下一实验或 Proposal

下一项受控实验只做一件事：增加一个经过服务端筛选、带内容摘要的 Basic 专用 Catalog 响应，替代 Workspace 每次下载三个通用 Module 仓库和完整市场历史状态；用同一浏览器、同一缓存 Backtest 做 A/B，验收目标是“点击到可操作图表小于 3 秒”，并要求 Job/Backtest/Dataset/Visualization 身份保持完全不变。
