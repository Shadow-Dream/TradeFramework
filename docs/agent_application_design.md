# TradeEngine Agent 应用层设计

> 状态：Kanna Session 与跨窗口实时联动源码已实施；现场浏览器联调待验收
>
> 日期：2026-08-16

TradeEngine Agent 是本机 Claude Code 与 Codex 的 Web 工作台，不是通用聊天页面。
`agent_web/` 采用 Kanna 的成熟 Session、Transcript、Tool 状态和 native resume 实现；
TradeEngine 继续负责账户、精确资源身份、验证和运行事实。系统不维护第二套 Agent
History，也不让 Agent 绕过 Engine authority。

## 1. 权威边界

~~~text
TradeEngine Web / API
  ├─ trade_session、资源、版本、验证、Backtest/Result
  ├─ 跨窗口 Context / presence / document / operation
  └─ Turn-bound Tool grant
             │
             ▼
TradeEngine Agent Web (Kanna fork)
  ├─ Project / Session / Transcript / 实时状态
  ├─ Claude Code + DeepSeek
  ├─ Codex app-server + GPT
  └─ TradeEngine stdio MCP
~~~

- TradeEngine 是账户、资源、不可变 Version、验证和执行状态的唯一权威；
- Agent Event Store 是 Session、Transcript、每 Turn 状态和 native session token 的
  唯一权威；
- MCP 只读取或验证 Engine 事实并创建展示用产物；
- Agent 可在服务端批准的源码 Project 中开发，但不能直接写 Engine control、archive、
  release 或 live 状态；
- Claude 与 Codex 的原生 History 永不混用。

## 2. Project 与 Context 是不同对象

Project 表示一个长期源码工作区。浏览器只接收：

~~~ts
interface AgentProject {
  projectId: string
  label: string
  kind: "trade-engine" | "strategy"
}
~~~

服务端固定提供 `trade-engine` 根 Project，并从 `TRADE_AGENT_PROJECTS_JSON` 加载显式
批准的外部私有策略仓库。它不扫描 TradeEngine 内部目录，并拒绝 symlink、重叠目录、
重复 ID 和任意浏览器路径输入。绝对 cwd 只在 Turn 启动时由
`ProjectCatalog` 解析，不进入页面、URL、WebSocket snapshot 或错误。Chat 创建后永久
绑定 Project；开发另一策略时创建属于另一 Project 的 Session，而不是切换已有 Chat
的目录。

Context 表示某一 Turn 引用的精确 Engine 资源，例如 Pipeline、Dataset Version、
Module Version、Backtest 或 Result。它不等于 Project，也不暗示当前最新版本。每个
Turn 都保存独立的 `TradeContextV1`；空 Context 仍是完整对象：

~~~json
{
  "schemaVersion": "1",
  "sourceView": "agent",
  "capturedAt": "2026-08-16T00:00:00Z",
  "references": []
}
~~~

旧 “Add current selection to Agent” 与 ticket 接口均已删除。Kanna `/ws/ui` 现在汇总
Engine SPA 和各 Jupyter tab 的 presence、语义资源选择、open document、dirty/revision、
resource change 与 operation progress。发送 Turn 时由 Kanna 服务端捕获不可变
`UiTurnContextV1`；浏览器 payload 不再携带可伪造 Context。

Agent 可通过三个 exact UI tools 读取实时状态、读取当前打开文档、以 revision/digest
CAS 修改当前 Engine 草稿或 Jupyter 文本 buffer。Engine 草稿 patch 不会隐式 Publish、
Save、Build 或 Run；Jupyter 是否保存由 tool 的 `save` 字段显式决定。`.ipynb` V1 只做
presence/read-only metadata，自动内容读写稳定拒绝。

## 3. 账户与导航

TradeEngine auth DB 与 `trade_session` 是唯一账户 authority。Agent 不再有密码页、
内存用户或第二套 Cookie：

- `/agent` 验证登录后跳到配置的 Agent public URL；
- Agent 通过 TradeEngine `/auth/session` 验证 Cookie，只投影 userId、email、role、
  expiresAt；
- 两端必须使用同一 hostname，Cookie 不复制到 URL；
- WebSocket 建连和定期复核都验证 Session 与 Origin；
- Agent 的 Return 链接回到进入前的 TradeEngine 相对页面；
- Sign out 调用 TradeEngine `/auth/logout`；
- Provider credential mutation 每次重新验证 admin，用户角色不由浏览器声明。

## 4. 后端、模型与原生 Session

公开后端只有：

| Backend ID | Runtime / Provider | 登录 |
|---|---|---|
| `claude-deepseek` | Claude Code / DeepSeek | API key 或受控导入 `.setdeepseek` |
| `codex-openai` | Codex app-server / OpenAI | 原生 device code |

默认是 `claude-deepseek`。Claude Code + Claude、OpenRouter 和任意 Provider URL 均不
存在于产品合同。DeepSeek 模型来自受控 credential profile；Codex 模型通过
app-server `model/list` 获得。Agent 为 Codex 创建独立模式 `0700` 的私有
`CODEX_HOME`，不会读取当前 Unix 用户的 Codex 登录。

Session 创建时固定 backendId。已有 Session 不接受另一 backend；同后端模型可在下一
Turn 切换，每个 Turn 保存实际模型。Claude 使用原生 session ID / resume，Codex 使用
app-server thread / turn；只把 matching token 交给 matching adapter。

每次创建 Session 和发送 Turn 都有 `clientRequestId` 与 canonical input digest：相同
ID、相同输入返回已有结果，相同 ID、不同输入拒绝。浏览器刷新或 WebSocket 断开不会
取消 Agent。服务重启把未完成 Turn 标记 `interrupted`，不自动重放可能有副作用的输入；
用户随后在同一 native Session 继续。

UI 显示 Running、Waiting for user、Interrupted、Failed、Reauthentication required、
Completed，以及当前 Tool、开始时间、最后事件时间、backend、模型和可执行的恢复动作。
`AskUserQuestion` 是 Agent 等待状态，不是审批流程；当前版本不增加额外审批层。

## 5. MCP 工具边界

Claude 与 Codex 共用同一个 stdio MCP，提供十个 exact 工具：

| Tool | 能力 |
|---|---|
| `trade_context_get` | 解析当前精确引用和能力摘要 |
| `trade_catalog_find` | 查找兼容资源候选 |
| `trade_dataset_inspect` | 有界 preview、profile 与 conformance |
| `trade_validate` | 权威验证 Pipeline/Graph/组合/Module 草稿 |
| `trade_backtest_get` | 查询 frozen composition、Job、错误和 Result availability |
| `trade_result_query` | 有界 describe、字段和周期查询 |
| `trade_proposal_create` | 创建展示用 AnalysisBrief/Proposal |
| `trade_ui_state_get` | 读取 live UI tabs、Context、documents、operations |
| `trade_ui_document_get` | 读取当前打开的受控文档 |
| `trade_ui_document_patch` | 对当前文档执行强 CAS replace patch |

每 Turn 由 Agent 服务申请 capability grant，绑定 ownerId、chatId、turnId、Context digest、
scope 与 expiry。Engine 只保留 token hash。原始 token 写入一次性 `0600` 文件；MCP
进程以 `O_NOFOLLOW` 校验 owner/mode/nlink 后读取并立刻 unlink，因此 secret 不出现在
Claude/Codex 进程参数、Transcript、事件或错误。Turn 完成、失败、取消、启动失败或
服务关闭时都按 turnId 撤销 grant。

工具结果有 record、cycle、field 和 byte 上限。资源工具没有 Save、Publish、Run、
Submit、Apply 或通用 Engine API passthrough；UI 文档工具只处理已由浏览器登记的逻辑
documentId，不接受路径，并用 operationId 防响应丢失后的重复副作用。

## 6. 结构化产物与 Skills

`trade_proposal_create` 只接受 exact `AnalysisBriefV1` 或 `ProposalV1`。confirmed fact
必须带精确引用，calculation 必须带 method、result 和引用。验证成功后 Agent 追加专用
Transcript entry，React 渲染可回跳 Engine 的卡片。Proposal 没有 Apply、Run 或
Execute 按钮；非法或超限内容不进入权威 Transcript。

canonical skills 只有：

- `strategy-development`；
- `dataset-preparation`；
- `backtest-investigation`；
- `research-verification`。

`scripts/install_agent_skills.py` 从一个 source 安装到 Claude 与 Codex 的原生 discovery
目录并提供 `--check`。Skill 必须通过 Engine/MCP 取得资源事实，Broker 离线时停止，
不能直接读取 control/archive 路径充当 fallback。

## 7. 源码与部署边界

- `agent_web/`：Kanna fork、React UI、WebSocket、Event Store 和两种 runtime adapter；
- `trade_agent_bridge/`：Tool grant、Engine tool projection 和 UI Hub bridge；
- `jupyter_ui_sync/`：JupyterLab source extension；`engine/assets/jupyter_labextensions/`
  保存其 prebuilt release artifact；
- `trade_agent_tools/`：两个 runtime 共用的 stdio MCP client/server；
- `deploy/user/` 与 `scripts/*agent_web.sh`：当前开发预览和热更新；
- Mining 独立运行，不与 Agent Web 共同打包。

旧 `web/agent.js`、`web/agent.css`、Python `agent_gateway/**`、Gateway SQLite、
`/api/agent/threads|runs|events|preferences|backends` 和
`trade-agent-gateway.service` 均已删除，不提供兼容接口。

旧 `/api/agent-handoffs`、`/api/agent-handoffs/exchange`、
`/api/trade-context/exchange` 和 Engine `/api/events` 也已删除，不提供 deprecated route。

完整实施与验收清单见 [Kanna integration plan](kanna_agent_integration_plan.md)，MCP
协议细节见 [MCP / Agent UI design](mcp_agui_design.md)。
