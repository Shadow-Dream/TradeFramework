# Kanna Agent 与 TradeEngine 集成实施计划

> 状态：源码、自动化验收与用户态热更新已完成；跨窗口浏览器交互待人工验收
>
> 日期：2026-08-17
>
> 目标：以 Kanna 替换 TradeEngine 旧 Agent Web/Gateway，并保留成熟的原生 Agent
> Session、状态展示和恢复能力；TradeEngine 继续作为账户、资源和验证权威。

## 实施结果

本计划已在当前源码中一次性切换完成：

- `agent_web/` 已正式纳入 Kanna fork，固定 upstream provenance、License 与依赖锁；
- 公开产品只剩 `claude-deepseek` 和 `codex-openai`，默认 DeepSeek；
- 保留服务端受控 Project 与 Project 级 Session，删除任意路径、目录浏览、Clone、
  GitHub、OpenRouter、Cloud 和 updater；
- TradeEngine Session 与双向导航已接通；Kanna `/ws/ui` 已成为 Engine、Jupyter、Agent
  的单一协作 Hub，旧手动 Context handoff 和 Engine SSE 已删除；
- Event Store 已实现 owner/project/backend 固定、request id 幂等、状态投影、重连、
  interrupted 与 matching native session resume；
- 七个资源工具、三个实时 UI 工具、Turn grant、Review Artifact 和四个 canonical
  Skills 已接通；
- 旧内嵌 Agent UI、Python Gateway、SQLite、API、smoke、requirements 和联合
  Agent/Mining 部署链已删除，没有 compatibility adapter；
- 用户态预览固定为 TradeEngine `:30809` 与 Agent Web `:30810`，支持构建、热更新和
  systemd 自动拉起。

现场门禁已证明 Claude Code + DeepSeek 能完成真实 Turn、调用
`trade_context_get` 并恢复 native session；Codex app-server 使用 Agent 私有
`CODEX_HOME`，不会借用当前 Unix 用户登录。Codex 的实际计费 Turn 仍需管理员在页面
完成 device-code 登录后按需执行。

## 1. 核心决定

Kanna 成为唯一的 Agent Web、会话和运行时入口。TradeEngine 继续作为唯一的账户、
资源和验证权威。最终版本不保留两套 Agent History、两套登录或两套后端状态。

~~~text
TradeEngine Web
  ├─ 账户、资源、Pipeline、Dataset、Backtest、Result
  ├─ /ws/ui presence、语义 Context、草稿文档和任务状态
  └─ /agent 跳转
          │ 复用 trade_session
          ▼
Kanna / TradeEngine Agent
  ├─ Session / Transcript / 状态恢复
  ├─ UI Sync Hub / CAS 文档路由
  ├─ Claude Code + DeepSeek
  ├─ Codex + GPT
  └─ TradeEngine MCP
          │ 只读 / validate / propose
          ▼
TradeEngine API
~~~

边界：

- Kanna 是 Agent Session、Transcript 和实时运行状态的唯一权威；
- TradeEngine 是用户账户、资源身份、版本、验证和执行状态的唯一权威；
- Claude 与 Codex 的原生 History 不互相混用；
- Agent 可对当前打开的 Engine 草稿或 Jupyter 文本 buffer 做 CAS patch，但不能用该工具
  隐式 Save/Publish/Run/Submit Engine 资源；
- 暂时不实现审批流程，普通 Agent 工具按当前模式自动执行；
- `AskUserQuestion` 等待用户回答属于 Agent 状态，不属于审批，应继续支持；
- 开发模式允许 Agent 修改 TradeEngine 服务端批准的源码 workspace；
- Agent 不能绕过 Engine API 修改 live resource、control 或 archive 状态。

### 1.1 Project 的产品语义

保留 Kanna 的 Project 能力，但改变其公开合同。

Upstream Kanna 的 Project 实际是带 `localPath` 的本地目录/代码仓库，路径同时作为
Claude Code 或 Codex 的 cwd。TradeEngine 产品中的 Project 则应表示一个由服务端管理的
逻辑开发工作区，例如整个 TradeEngine 或一项具体策略。两者可以在服务端映射，但不能把
文件系统路径暴露成浏览器可选择、可提交的产品字段。

第一版 Project 目录：

~~~text
Projects
├─ TradeEngine
│  └─ 用于 Engine、共享组件和跨策略开发
├─ Momentum Lab
│  └─ 对应已注册的策略工作区
├─ Strategy B
└─ Strategy C
~~~

公开合同只包含逻辑身份：

~~~ts
interface AgentProject {
  projectId: string
  label: string
  kind: "trade-engine" | "strategy"
}
~~~

约束：

- 浏览器只提交 `projectId`，不能提交 `localPath`；
- 服务端用 exact catalog 将 `projectId` 解析成 cwd；
- 页面和公开快照只显示 Project 名称，不显示绝对路径；
- 保留每个 Project 独立的 Session、History、Diff、touched files 和 Skills；
- Chat 创建后永久绑定 Project，切换 Project 必须新建 Chat；
- 不允许浏览主机目录、输入路径、打开任意文件夹或从 GitHub Clone；
- Pipeline、Dataset、Backtest、Result 等 Engine 资源通过 Context 附加，不冒充
  Agent Project；
- 第一版只发现既有 Project，不从 Kanna 直接创建策略目录；未来如需创建策略，必须调用
  TradeEngine 的正式 Strategy scaffold/catalog 合同。

## 2. 实施前基线（历史，以下缺口均已关闭）

### 2.1 Kanna 已有且已保留的能力

当前 Kanna fork 基于 upstream commit：

~~~text
08dfafcd0839e2dc451cca5ea831ef4c6d7233df
~~~

已有能力：

- append-only Event Store 和 Transcript；
- Claude/Codex native session token 持久化；
- 页面断开后的 WebSocket 重连和快照恢复；
- 服务重启后将未完成 Turn 标记为 `interrupted`；
- Tool 调用、当前运行状态、等待用户输入、取消和错误展示；
- 每个 Turn 的实际模型快照；
- Session 创建后禁止中途切换后端；
- Claude Agent SDK 与 Codex app-server 原生运行时适配。

调研时确认的主要 upstream 模块（仅作为 provenance 记录，当前权威源码是
`agent_web/`）：

- `src/server/event-store.ts`
- `src/server/agent.ts`
- `src/server/codex-app-server.ts`
- `src/client/app/KannaTranscript.tsx`

这些功能现在继续由 `agent_web/` 中的 Kanna 实现，不在 TradeEngine 中重复实现。

### 2.2 实施前缺失和冲突（已解决）

1. Kanna 仍位于 `/tmp`，只是预览 PoC，不是 TradeEngine 正式源码的一部分。
2. Kanna 使用自己的密码、Cookie 和内存 Session，不认识 TradeEngine 的
   `trade_session`。
3. Kanna 的返回按钮硬编码到端口 `30809`。
4. Kanna 中没有 TradeEngine Context、AnalysisBrief、Proposal、MCP Tools、资源引用
   和资源深链接。
5. `docs/agent_application_design.md` 中的“V1-A 已完成”指旧 Python Gateway 和旧内嵌
   Agent 页面，不是 Kanna。
6. TradeEngine 仍保留旧 `web/agent.js`、`web/agent.css`、`agent_gateway/**`、
   `/api/agent/threads|runs|events`、Gateway SQLite 和部署服务。
7. Kanna 虽然在 UI 只显示 Claude/Codex，但共享类型、WebSocket 协议和服务端仍保留：
   Cursor、Pi、OpenRouter、GitHub、任意路径 Project 创建/克隆、目录浏览、Cloud
   pairing 和任意 LLM Provider。Project 分组本身需要保留，任意文件系统入口需要删除。
8. Kanna 当前 `chat.send` 没有持久化的 `clientRequestId`，请求响应丢失时还不能证明
   exactly-once。
9. Kanna Event Store 当前没有 TradeEngine owner/account 维度。
10. 当前 `trade_agent_tools` 只是未上线、fail-closed 的框架，并且暴露了不应在第一版
    开放的 `submit_backtest`。

## 3. 目标代码结构

建议将 Kanna fork 正式纳入：

~~~text
agent_web/
  UPSTREAM.md
  LICENSE
  package.json
  bun.lock
  src/client/
  src/server/
    trade-project-catalog.ts
  src/shared/

trade_agent_bridge/
  tool_grants.py
  tool_api.py
  ui_tool_bridge.py
  contracts/

trade_agent_tools/
  client.py
  mcp_server.py
~~~

原则：

- `agent_web/` 保存 Kanna fork 和前后端 Session 实现；
- `trade_agent_bridge/` 只负责 Tool grant、Engine tool projection 和 Kanna UI Hub 的
  server-to-server bridge；
- `trade_agent_tools/` 是两个原生后端共同使用的 stdio MCP；
- 不修改 Engine 的 Pipeline、Graph、Backtest 或 Result 执行语义；
- 不把旧 Agent Gateway 作为中间层继续保留。

## 4. 实施阶段

### 阶段 1：正式纳入 Kanna 并收窄产品（完成）

#### 代码迁移

- 将当前 Kanna fork 从 `/tmp` 迁入 `agent_web/`；
- 固定 upstream commit、License 和依赖锁；
- 迁入现有的 DeepSeek、Codex、崩溃恢复和 workspace 约束修改；
- 建立 `agent_web` 独立的 typecheck、unit test 和 build 命令。

#### 删除无关功能

将公开后端合同改为：

~~~ts
type BackendId = "claude-deepseek" | "codex-openai"
~~~

从共享类型、UI、WebSocket 协议和服务端实现中真正删除：

- Cursor；
- Pi；
- OpenRouter；
- Claude/Anthropic OAuth；
- GitHub 登录、克隆和发布；
- Cloud pairing/share；
- 接受 `localPath` 的 Project open/create/clone；
- 主机目录浏览、任意 workspace 切换和路径输入；
- 任意 Provider URL、binary path 和 environment 配置；
- 已无产品入口的 compatibility alias。

保留：

- Project 模型和 Project 级 Session/History 分组；
- 一个 TradeEngine 根 Project；
- 独立私有仓库中由服务端配置批准的策略 Project；
- Agent Session、Transcript、Tool 状态和恢复；
- Claude Code + DeepSeek；
- Codex + GPT；
- 每后端模型选择；
- 本地 Git diff/touched files；
- 当前 Project scope 内必要的终端能力。

#### Project Catalog 和路径解析

新增服务端 `ProjectCatalog`，浏览器不再参与路径解析：

~~~text
trade-engine       -> 部署配置中的 TradeEngine 根目录
strategy:momentum  -> 服务端配置的外部私有策略仓库
~~~

第一版目录来源：

- 固定一个 `trade-engine` 根 Project；
- 不扫描 TradeEngine 仓库，策略 Project 只来自服务端
  `TRADE_AGENT_PROJECTS_JSON` exact 配置；
- 拒绝 symlink、重叠路径、重复 ID、越界路径和非普通目录；
- Project ID 由服务端配置并做 exact 校验；
- cwd 只在服务端解析，不写入公开 Project/Sidebar/Chat snapshot；
- Event Store 持久化 `projectId`、`kind` 和逻辑 workspace key，不把绝对路径作为公共
  identity；
- 如果未来出现正式 Strategy Catalog，以 Catalog 替代目录扫描，公开 `projectId` 保持
  稳定。

Chat 创建仍使用 `projectId`。AgentCoordinator 在每个 Turn 开始前重新通过
`ProjectCatalog` 解析 cwd，并重新确认它仍是批准的独立目录。根 Project
用于跨策略和共享 Engine 开发；策略 Project 用于该策略自己的 Session、History、Diff
和 Skills。

当前没有生产数据，Event Store 直接升级为干净的 schema v4，不保留 v3 compatibility
reader。切换时重置 PoC Session 数据。

#### 阶段验收

- `agent_web/` 可独立构建和启动；
- 页面、协议和服务端都只能识别两个 Backend ID；
- 非法 Backend/Model 在服务端拒绝；
- 根 Project 和既有策略 Project 可创建独立 Session；
- 页面不显示、协议不接受绝对路径；
- 不存在任意目录选择、Project clone 或任意路径 Project create；
- Chat 创建后不能更改 Project；
- 静态扫描不再出现 Cursor、Pi、OpenRouter 和 GitHub Provider 功能。

### 阶段 2：统一账户和双向导航（完成）

TradeEngine 已有主机级 Cookie：

- `trade_session`；
- `trade_csrf`；
- `Path=/`；
- TradeEngine auth DB 是 Session authority。

#### Kanna 修改

- 删除 PasswordScreen；
- 删除 Kanna `/auth/login`、`/auth/logout` 和内存 Session 集合；
- 新增 `TradeSessionVerifier`；
- 从请求 Cookie 取得 `trade_session`，调用 TradeEngine `/auth/session`；
- 只保留 `userId`、`email`、`role`、`expiresAt`；
- 不向 Agent 子进程传递浏览器 Cookie 或 CSRF；
- WebSocket 建连时验证 Session 和 Origin；
- 长连接定期复核 Session；
- Provider 登录、API key 修改和登出每次重新验证管理员角色；
- Session 失效时关闭 WebSocket，前端进入重新登录跳转状态。

#### TradeEngine 修改

- 新增配置项 `agentPublicUrl`；
- 将侧栏 Agent 改成真实链接，而不是内嵌 SPA view；
- `GET /agent` 验证 Session 后 `303` 跳转到 `agentPublicUrl`；
- 未登录 Kanna 跳转到 TradeEngine `/login?next=/agent`；
- TradeEngine 登录完成后经 `/agent` 返回 Kanna；
- Kanna 的返回按钮从服务端配置读取 TradeEngine public URL；
- 保存进入 Agent 前的 TradeEngine 相对路径，用于回跳；
- Agent 页面显示当前 TradeEngine 账户；
- Agent 发起 Sign out 时调用 TradeEngine `/auth/logout` 并清理主机级 Cookie。

#### 安全约束

- URL 不从不可信 Host Header 推导，使用部署配置；
- 只允许配置中的 TradeEngine/Agent Origin；
- Kanna 不直接读取 TradeEngine auth DB；
- Kanna 不持久化原始浏览器 Session token；
- Provider credential mutation 只允许 TradeEngine `admin`；
- 当前单机部署必须使用同一个 hostname，不能一端用 IP、一端用 localhost。

#### 阶段验收

- 登录 TradeEngine 后进入 Agent 不再要求密码；
- Agent 返回 TradeEngine 不重新登录；
- TradeEngine 登出后 Agent Session 自动失效；
- Session 到期后跳回 TradeEngine 登录；
- Agent/TradeEngine URL 均无硬编码端口；
- 普通用户不能修改全局 Provider 凭据。

### 阶段 3：完善 Agent Session 合同（完成）

复用 Kanna Event Store，并补齐：

- Chat 创建后永久绑定 `backendId`；
- Native session ID 只能由对应 Backend resume；
- 同 Backend 允许切换模型；
- 每个 Turn 保存实际模型；
- `chat.send` 增加 `clientRequestId`；
- Event Store 持久化请求 ID 和 canonical input digest；
- 相同 ID、相同输入返回原 Turn；
- 相同 ID、不同输入拒绝；
- queued message 保存 backend、model、context 和 request ID；
- 每个 Chat 和 Transcript 绑定 TradeEngine `ownerId`；
- 所有订阅、读取、rename/archive/delete 按 owner 过滤；
- 浏览器断开不取消 Turn；
- 服务重启不自动重放 Turn，只记录 `interrupted`；
- 下一轮在同一后端继续 resume 已持久化 native session；
- Provider 凭据失效时记录 `reauth_required`，不误报为无响应；
- runtime 失败保存稳定的错误码和 retryable 标记。

UI 明确展示：

- Running；
- Waiting for user；
- Interrupted；
- Failed；
- Completed；
- 当前 Tool/Command；
- Turn 开始时间；
- 最后事件时间；
- Backend 和实际模型；
- 可以 Retry、Resume 或必须 Reauthenticate 的原因。

#### 阶段验收

- 后端不能在已有 Chat 内切换；
- 同后端模型切换在下一 Turn 生效；
- 刷新、断网不丢 Transcript；
- 服务崩溃后不残留永久 Running；
- 服务恢复后可以在原 native Session 继续；
- 重复 `clientRequestId` 不生成重复消息或 Turn；
- 账户之间不能读取彼此 Chat。

### 阶段 4：常驻 UI Sync 与 Turn Context（源码完成）

Kanna 的 `/ws/ui` 是唯一协作 Hub。Engine SPA 与每个 JupyterLab tab 使用同一
`trade_session` 建连，注册稳定 tabId、client kind 与最小 capability。Hub 只保存逻辑
页面、资源和 workspace-relative 文档身份，不接收绝对路径、Cookie、CSRF 或 API key。

已实现的消息域：

- presence：connected、visible、focused、last interaction 与断线保留窗口；
- semantic context：route、view/subview、显式资源引用、图节点选择、当前文档 revision；
- document：open/update/close、snapshot、CAS replace patch、dirty/savedRevision；
- resource event：changed/published/archived/deleted/validation-changed；
- operation event：waiting/progress/completed/failed/interrupted；
- reconnect：server sequence、全量 snapshot、心跳与 bounded in-memory retention。

Engine SPA 已登记 Pipeline、Environment、Analysis、Backtest composition 和 Visualization
草稿。Agent patch 只更新可见草稿并置 dirty，不调用 Publish、Save 或 Backtest。Jupyter
通过正式 prebuilt JupyterLab extension 登记文本文件；`save` 必须由 tool 参数显式给出。
Notebook 仅显示 presence，V1 明确拒绝自动读取或修改 `.ipynb`。

每个 Turn 发送时由 Kanna 服务端原子捕获 `UiTurnContextV1` 并持久化；浏览器不能在
`chat.send` 中伪造 Context。排队、retry、后台 resume 均复用该 Turn 原快照。需要更新
状态时 Agent 显式调用 `trade_ui_state_get`。

旧 `/api/agent-handoffs`、`/exchange`、Agent `/api/trade-context/exchange` 与 Engine
`/api/events` 已删除并直接 404，不保留兼容 reader 或废弃实现。

#### 阶段验收

- Engine、Jupyter、Agent 多窗口能看到当前 active/ambiguous Context；
- 后台 tab 的异步更新不会抢走当前 focus；
- 页面断开、重连和 Session 过期有明确状态；
- 同文档多 dirty editor 拒绝自动选择；
- patch 要求 revision + SHA-256 digest；同 operationId 不同输入稳定冲突；
- Agent 修改立即出现在当前浏览器草稿/文本编辑器；
- 无绝对路径、凭据或 Notebook 自动修改。

### 阶段 5：实现 TradeEngine MCP Tools（完成）

第一版实现七个 Engine 资源工具与三个 live UI 工具：

| Tool | 能力 |
|---|---|
| `trade_context_get` | 解析当前精确引用、合同和能力摘要 |
| `trade_catalog_find` | 查找兼容的 Engine 资源候选 |
| `trade_dataset_inspect` | 有界 preview、profile、因果时点和 conformance |
| `trade_validate` | Pipeline/Graph/组合/Module 草稿的权威验证 |
| `trade_backtest_get` | 获取 frozen composition、Job、错误和 Result availability |
| `trade_result_query` | 有界 describe、字段和周期查询 |
| `trade_proposal_create` | 创建展示用 AnalysisBrief/Proposal，不执行写入 |
| `trade_ui_state_get` | 读取当前 tab、语义 Context、文档和长任务状态 |
| `trade_ui_document_get` | 读取当前打开的受控文本/结构化草稿 |
| `trade_ui_document_patch` | 以 revision、digest、operationId 做 CAS replace patch |

明确不开放：

- Save；
- Publish；
- Run；
- Submit；
- 对 live Engine resource 的 Apply；
- 任意 Engine API passthrough；
- 任意路径读取或未打开文件读取。

#### Tool grant

- Kanna 为每个 Turn 申请短期 Tool grant；
- grant 绑定 `userId`、`chatId`、`turnId`、`contextDigest`、scopes 和 expiry；
- Engine 只保存 token hash；
- 原始 grant 不进入 Transcript、日志、错误或数据库；
- Agent 子进程不获得浏览器 Session/CSRF；
- MCP 只能调用 exact allowlist；
- Engine API 离线时立即失败，不进入本地重放队列；
- Dataset/Result 设置 record、field、cycle 和 byte 上限；
- 所有资源事实通过 Engine API 和 archive verification 获取；
- Claude 与 Codex 使用同一个 TradeEngine MCP 合同。

#### Kanna 接入

- Claude Agent SDK 启动时注册 TradeEngine MCP；
- Codex app-server 启动时注册同一 MCP；
- Kanna Transcript 保留 MCP tool call/result；
- UI 显示当前 Tool、输入摘要、完成/失败状态；
- Tool payload 按 Kanna 现有 bounded projection 处理；
- Tool 错误保留稳定错误码和用户可读信息。

#### 阶段验收

- 两个后端都能调用十个工具；
- Context 引用由 Engine 现场解析；
- 无权限、超限和 Engine 离线都有明确状态；
- MCP 无法执行 Backtest 或直接修改已发布 Engine 资源；
- 浏览器凭据不会出现在 Agent 进程环境、Transcript 或日志中。

### 阶段 6：AnalysisBrief、Proposal 和 Skills（完成）

#### 结构化产物

将旧 Gateway 中的严格合同迁移为 Kanna/TradeEngine 共享合同，不保留旧 Python Gateway
runtime。

支持：

- `AnalysisBriefV1`；
- `ProposalV1`；
- confirmed fact 必须带精确引用；
- calculation 必须带 method、result 和引用；
- Proposal 只包含标题、摘要、建议动作和引用；
- 单项和整体大小限制；
- exact fields；
- 非法、超限或缺引用结果不发布。

`trade_proposal_create` 成功后：

- Kanna 追加专用 Transcript entry；
- React 渲染 AnalysisBrief/Proposal 卡片；
- 引用可跳回 TradeEngine 对应资源页；
- Proposal 不出现 Apply、Execute 或 Run 按钮；
- 非法结果显示明确警告，不伪造成功。

#### Skills

只保留四个任务型 Skill：

- `strategy-development`；
- `dataset-preparation`；
- `backtest-investigation`；
- `research-verification`。

部署时从一个 canonical source 安装到 Claude 与 Codex 的原生 Skill discovery 目录，
不维护两套内容副本。Skill 在 Broker 离线时必须明确停止，不回退到直接读取 Engine
control/archive 路径。

#### 阶段验收

- 两后端均能发现四个 Skill；
- AnalysisBrief/Proposal 能结构化显示；
- 引用能回跳 TradeEngine；
- Proposal 没有执行入口；
- 非法结构化产物不进入 Transcript 权威记录。

### 阶段 7：一次性切换并删除旧实现（完成）

所有 Kanna 能力通过后执行单次 cutover。最终代码不允许两套 Agent 共存。

删除：

- `web/agent.js`；
- `web/agent.css`；
- `web/index.html` 中旧 Agent DOM 和 Settings dialog；
- `AgentGatewayClient`；
- `agent_gateway/**`；
- `tests/agent_gateway/**`；
- `/api/agent/threads`；
- `/api/agent/runs`；
- `/api/agent/events`；
- `/api/agent/preferences`；
- `/api/agent/backends`；
- Gateway SQLite schema 和迁移；
- `trade-agent-gateway.service`；
- Gateway smoke 和 requirements；
- 旧 Gateway 的联合 Agent/Mining 部署逻辑。

部署拆分：

- Mining 独立部署；
- Kanna Agent Web 独立部署；
- TradeEngine 只保存 Agent public URL 和 tool/UI bridge；
- 旧 Agent API 直接 404，不做 deprecated redirect 或 compatibility adapter。

文档同步更新：

- `docs/agent_application_design.md`；
- `docs/engine_web.md`；
- `docs/mcp_agui_design.md`；
- `README.md`；
- 部署 README。

## 5. 开发和热更新方式

在当前开发阶段使用用户模式运行：

~~~text
TradeEngine: 10.130.130.66:30809
Agent Web:   10.130.130.66:30810
~~~

建议增加：

- `scripts/dev_agent_web.sh`：启动 Kanna dev server；
- `scripts/build_agent_web.sh`：typecheck、test、build；
- `scripts/reload_agent_web.sh`：原子替换静态产物并重启用户服务；
- 用户级 systemd service：进程崩溃自动拉起；
- 页面显示 build commit，方便确认浏览器看到的是哪次修改；
- 每个阶段完成后立即更新 30810 预览，不等到全部功能完成。

部署阶段再使用 root 安装正式 service、权限、credential 和固定运行目录。开发阶段不维护
一套假生产部署。

## 6. 完整验收标准

### 6.1 登录与导航

- 登录 TradeEngine 后进入 Agent 不再要求密码；
- 从 Agent 返回 TradeEngine 不再重新登录；
- TradeEngine 登出后 Agent WebSocket 自动失效；
- Session 到期后跳回 TradeEngine 登录；
- Agent URL 和 TradeEngine URL 均不硬编码；
- 两端可以返回原 TradeEngine 页面；
- 当前 TradeEngine 用户和管理员权限在 Agent 页面正确显示。

### 6.2 Backend 和模型

- 新用户默认 `claude-deepseek`；
- 只存在 `claude-deepseek` 和 `codex-openai`；
- DeepSeek 使用 API key；
- Codex 使用 app-server 原生 device-code 登录；
- Backend 在 Chat 创建后永久固定；
- 同 Backend 可以为下一 Turn 切模型；
- 每个 Turn 保存实际模型；
- Claude 与 Codex History 不混用。

### 6.3 Project

- 页面至少提供一个 TradeEngine 根 Project 和所有合规的既有策略 Project；
- 不同策略 Project 拥有独立的 Session、History、Diff 和 Skills scope；
- 浏览器只接收和提交 `projectId`，不接收或提交 `localPath`；
- 页面、URL、WebSocket snapshot 和错误中不显示绝对路径；
- 不允许浏览目录、输入路径、打开任意文件夹或 Clone Project；
- 非法、过期、symlink 或越界 Project 在服务端拒绝；
- Chat 创建后 Project 不可改变；
- 跨策略工作使用 TradeEngine 根 Project，不通过修改已有 Chat 的 cwd 实现；
- Pipeline、Dataset、Backtest 和 Result 继续作为 Context reference，不被错误建模为
  Project。

### 6.4 Agent Session 与状态

- 页面刷新不丢 Transcript；
- 浏览器断网不取消 Turn；
- 服务重启后未完成 Turn 显示 Interrupted；
- 不出现永久 Running 或状态未知；
- 下一轮可以继续原 native Session；
- 重复发送不会产生第二个 Turn；
- 当前 Tool、等待原因和最后活动时间清晰可见；
- Provider 认证失效和 Runtime 崩溃能区分；
- 用户可以明确 Retry、Resume 或 Reauthenticate。

### 6.5 TradeEngine UI Context

- 精确 Pipeline/Dataset/Module/Environment/Analysis/Backtest/Result 可由当前页面投影；
- 不允许 `latest`；
- Context 在排队、刷新和 Resume 后不漂移；
- 每个 Turn 都保存 Context；
- UI Context 和用户 Prompt 分开，由服务端捕获；
- 当前 Engine 草稿和 Jupyter 文本文件可 CAS 读取/修改；
- Notebook 自动修改稳定拒绝；
- 无绝对 control/archive/workspace 路径泄露。

### 6.6 Agent Tools 和结构化产物

- 两个 Backend 都能调用十个 TradeEngine Tool；
- Engine resource Tool 只能 read、validate、propose；UI Tool 只可修改当前打开的草稿/buffer；
- 无 Save、Publish、Run、Submit 或 Apply；
- Engine 离线、无权限、超限均明确失败；
- AnalysisBrief/Proposal 严格验证并以卡片显示；
- Proposal 没有执行按钮；
- 引用可回跳精确 TradeEngine 页面；
- 浏览器 Session、CSRF、DeepSeek key 和 Codex token 不进入 Transcript 或日志。

### 6.7 清理验收

最终静态扫描要求：

- Kanna 产品源码没有 Cursor、Pi、OpenRouter 或 GitHub Provider；
- 保留 Project catalog、Project 分组和按 `projectId` 创建 Session；
- 没有目录选择、路径输入、Project clone 或接受 `localPath` 的 Project create/open
  协议；
- TradeEngine 没有旧 Agent Gateway API；
- 没有旧 Gateway service、SQLite 或迁移；
- 浏览器构建产物不包含旧 Agent 页面；
- 浏览器构建产物不包含批准 workspace 的绝对路径；
- fresh install 不创建旧 Gateway 文件或目录。

## 7. 已执行顺序

1. 正式纳入 Kanna 源码；
2. 删除无关 Provider、任意路径入口、Cloud 和 GitHub 功能；
3. 实现 TradeEngine 根 Project 与策略 Project catalog；
4. 统一 TradeEngine 登录和双向导航；
5. 完善 Kanna Session owner、project binding、idempotency 和状态合同；
6. 实现 Context handoff；
7. 实现七个 TradeEngine MCP Tools；
8. 实现 AnalysisBrief、Proposal 和四个 Skills；
9. 完成真实 Claude/DeepSeek 与 Codex 回归；
10. 一次性切换入口；
11. 删除旧 Agent Gateway、旧 UI、旧 API 和旧部署逻辑；
12. 更新设计与部署文档；
13. 执行完整验收和用户模式热更新。

随后已用 `/ws/ui`、server-captured Turn Context 和三个 UI tools 替换第 6 步的 handoff，
并删除 handoff/SSE 旧接口；Jupyter 使用官方 prebuilt extension 机制接入。

每个阶段均在 30810 用户态预览完成独立验收，没有等待最终部署才检查页面效果。

## 8. 2026-08-16 验收证据

- Agent Web：`806 pass / 0 fail`，TypeScript `--noEmit` 通过，Vite production build
  通过；
- Engine bridge/auth/architecture/user-preview：`49 passed`；
- 四个 canonical Skills：Claude/Codex 共 8 个原生 discovery link 校验通过；
- 当前用户态 build：`dev-20260816T221220Z`，30809/30810 两个 systemd user service
  均为 active；
- 已认证浏览器合同现场验证：旧 `/api/agent/threads` 返回 404，Agent session 投影为
  admin，Agent HTML 非空；测试账户与 Session 已清理；
- Claude Code + DeepSeek 真实首轮、native resume 和 `trade_context_get` MCP 调用通过，
  grant 文件在读取后删除；
- Codex app-server 使用 `~/.trade-agent/credentials/codex` 私有
  `CODEX_HOME`，现场 account 为未登录且模型目录可读；管理员在页面完成 device-code
  登录后即可执行真实 Codex Turn；
- 浏览器构建静态扫描没有批准 workspace 的绝对路径、旧 Gateway 名称、旧 Agent API、
  OpenRouter 或 `claude-anthropic`。
