# TradeEngine MCP 与 Agent UI 合同

> 状态：资源工具与实时 UI 工具已实施
>
> 日期：2026-08-16

MCP 是 Kanna Agent runtime 到 TradeEngine authority 的受限桥，不是第二控制面。Agent
UI 的 Tool 状态来自 Kanna Transcript；Engine 资源事实仍由当前认证 API、repository
verification 和 runtime contract 产生。

## 1. Turn 级调用链

~~~text
Browser prompt
  → Kanna 创建 Turn
  → 服务端捕获不可变 UiTurnContextV1 / 精确 TradeContextV1
  → Agent 服务以 bridge bearer 向 Engine 申请 grant
  → Engine 绑定 owner/chat/turn/contextDigest/scopes
  → 一次性 0600 文件把 grant 交给 stdio MCP
  → MCP 调 exact Engine tool endpoint
  → Tool call/result 进入 Kanna Transcript
  → Turn 终态撤销 grant
~~~

浏览器 Cookie、CSRF、DeepSeek key 和 Codex token 都不会传给 MCP。Engine 仅持久化
grant 的 SHA-256 hash；MCP 以 `O_NOFOLLOW` 校验 grant 文件为 owner-owned、单链接、
普通 `0600` 文件，读取后立即删除。即使 grant 文件发布失败，Coordinator 也按 turnId
撤销可能已经在 Engine 创建的 capability。

## 2. Exact tools

只开放以下 scope 与同名工具：

- `trade_context_get`：解析本 Turn 精确 Context；
- `trade_catalog_find`：有界搜索公开资源摘要；
- `trade_dataset_inspect`：读取已验证 Dataset Version 的有界记录与能力；
- `trade_validate`：调用 Engine 的 Pipeline、Environment、Analysis、Module 或
  Backtest composition 验证；
- `trade_backtest_get`：读取 Backtest 元数据和 Result availability；
- `trade_result_query`：对 Result 做有界 describe/field/cycle projection；
- `trade_proposal_create`：验证并返回 display-only `AnalysisBriefV1` / `ProposalV1`。
- `trade_ui_state_get`：读取 Kanna UI Hub 的实时 tabs、Context、documents 与 operations；
- `trade_ui_document_get`：读取当前打开文档的 revision、digest 和有界内容；
- `trade_ui_document_patch`：用 operationId、baseRevision、baseDigest 做 CAS replace patch。

请求为 exact JSON；未知字段、未知 tool、未知 scope、过期/撤销 grant 都稳定失败。结果
递归移除 `path`、`localPath`、`absolutePath`、workspace/control/archive/manifest path 和
绝对路径字符串。catalog、Dataset、Result 与总 payload 都有硬上限。

明确不存在：

- 任意 REST passthrough；
- Save、Publish、Run、Submit 或 Apply；
- shell/file MCP；
- 路径参数或未打开文件访问；
- 对 `.runtime`、control、archive、release、live 的直接读取；
- Engine 离线后的本地排队或自动重放。

## 3. Context 与结构化产物

`TradeContextV1` 单独存于每个 Turn，不拼进用户的权威 prompt 记录。reference 必须是
明确 kind/id/version/digest，最多 32 项，不接受 `latest`。Tool grant 保存这一 Context
的 detached copy 和 digest，避免页面刷新、排队或 native resume 时引用漂移。

`trade_proposal_create` 接受的结构由 `trade_agent_bridge/contracts/` 权威验证：

- confirmed fact 带精确 Engine reference 或合规 external source；
- calculation 带 method、result 和引用；
- Proposal 仅含标题、摘要、建议动作和引用；
- exact fields，并有单项和总大小限制。

通过后，Kanna 写入专用 review artifact Transcript entry。React 卡片只显示内容和精确
TradeEngine deep link，没有 Apply、Execute 或 Run 控件。验证失败只显示 Tool error，
不会伪造成功产物。

## 4. UI 状态投影

Kanna WebSocket snapshot 是 Session UI 的事实源。页面展示：

- Turn 状态：Running、Waiting for user、Interrupted、Failed、Reauthentication required、
  Completed；
- 当前 Tool/Command、开始时间和最后事件时间；
- 每次 Tool call/result 的完成或失败状态；
- backend、实际模型和 matching native session 的恢复状态；
- retry、resume、cancel 或 reauthenticate 的明确动作。

浏览器断开不取消 Turn；重连从 Event Store snapshot 恢复。服务重启把 active Turn 写成
Interrupted，不自动重放。MCP 的失败不会被折叠成“Agent 没回复”，而是以稳定 code 和
retryable 标志进入 Turn/Tool 状态。

## 5. Engine 内部路由

这些路由只允许 Agent 服务的 bridge bearer，不是浏览器 API：

~~~text
POST /api/agent-tools/grants
POST /api/agent-tools/call
POST /api/agent-tools/grants/revoke
~~~

UI tools 由 Engine 通过同一个 bridge bearer 调 Kanna 进程内 Hub：

~~~text
POST /api/internal/ui-tools/call
~~~

该 Kanna internal route 拒绝 Origin、浏览器 Cookie 和未知 tool，只接受配置的 bridge
bearer。旧 handoff/exchange 与 Engine SSE 已删除；浏览器只使用认证 `/ws/ui`。
