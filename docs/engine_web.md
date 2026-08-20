# TradeEngine Web 与 API

`engine_service.py` 同时提供认证控制 API、单页前端、Backtest Job 管理和 Jupyter Workspace 代理。一个服务进程独占一个 `controlRoot`；不存在 Lane、Attachment、Remote runner 或第二执行后端。

## 启动

```bash
python3 engine_service.py \
  --config .runtime/strategy-control.json \
  --host 127.0.0.1 \
  --port 30808 \
  --public-url https://trade.duckduckrun.com
```

默认配置要求 HTTPS 公网地址，并只允许服务绑定 loopback 后置于反向代理。所有 `/api/*` 都要求登录 Session；所有状态修改还要求匹配的 CSRF Cookie/Header。静态资源响应使用 `Cache-Control: no-store`，不依赖手工 query-string 版本。

启动顺序固定为：声明控制目录 Owner、准备 control schema、准备 SQLite schema、通过公共归档路径安装 BuiltIn、初始化认证和 Backtest Job Manager。发现不兼容 schema 时，旧 `control/release/live` 资源会作为一个只读目录整体归档；不会解释或兼容执行其中的旧记录。

## 页面

- `/overview`：服务时间和当前资源数量，不读取 Market Hours 或其他策略数据文件。
- `/pipeline`：Pipeline 资源浏览器；双击资源或点击 `Open` 后才进入编辑页面。
- `/pipeline/builder?pipelineId=...`：单个 Pipeline 的 stages Builder；返回列表不会把 Builder 常驻在浏览页中。
- `/environment`：独立 Environment Graph 编辑器。
- `/analysis`：独立 Analysis Graph 编辑器。
- `/modules`：Pipeline、Environment、Analysis 三个独立 Module repository。
- `/data`：Dataset、Sampler、Dataset Script 与 Workspace。
- `/mining/k-line`：独立的 Provider 原生分钟数据采集、续断、Gap 回补与健康状态；不会自动发布 Dataset。
- `/backtests`：组合并冻结各顶级资源 Version，提交 Backtest Job。
- `/result`：Result 与临时可视化 Module；临时实例不写回 Result 或下一周期状态。
- `/agent`：验证当前 TradeEngine Session，携带受限的返回路径跳转到独立 Agent Web；
  不附加当前页面或选择状态。Agent Web 只支持 Claude Code + DeepSeek（默认）与
  Codex + GPT；它的可用性不改变 Engine 健康状态。

Signal、Environment、Analysis 使用同一 LiteGraph 编辑器和同一后端 Graph compiler。Data Input/Data Output 只在编辑器内表达边界，不会保存为 Module。

## 主要只读 API

```text
GET /api/health
GET /api/summary
GET /api/repositories
GET /api/modules
GET /api/environment-modules
GET /api/analysis-modules
GET /api/pipelines
GET /api/pipelines/{pipelineId}
GET /api/pipelines/{pipelineId}/versions
GET /api/pipelines/{pipelineId}/versions/{version}
GET /api/environments
GET /api/analyses
GET /api/data/datasets
GET /api/data/datasets/{datasetId}/versions
GET /api/data/samplers
GET /api/data/recipes
GET /api/data/workspaces
GET /api/backtest-jobs
GET /api/backtests
GET /api/backtests/{backtestId}/meta
GET /api/backtests/{backtestId}/result
GET /api/visualizers
GET /api/history
GET /api/mining/health
GET /api/mining/providers
GET /api/mining/jobs
GET /api/mining/jobs/{jobId}
GET /api/mining/jobs/{jobId}/records
GET /api/mining/jobs/{jobId}/gaps
GET /api/mining/jobs/{jobId}/manifest
GET /api/mining/events
```

列表和读取 Version 时，后端会验证索引键、完整单调历史、归档记录快照、逐文件摘要、manifest digest 和只读权限。验证失败时请求直接失败，不回退到当前 Draft 或旧协议。

## 主要修改 API

```text
POST /api/modules
POST /api/environment-modules
POST /api/analysis-modules
POST /api/pipelines
POST /api/pipelines/{pipelineId}/versions
POST /api/pipelines/{pipelineId}/clone
POST /api/pipelines/{pipelineId}/disable
POST /api/environments
POST /api/analyses
POST /api/data/upload
POST /api/data/samplers
POST /api/data/recipes
POST /api/data/process
POST /api/backtests
POST /api/backtests/{backtestId}/result
POST /api/visualizations
POST /api/mining/jobs
POST /api/mining/jobs/{jobId}/pause
POST /api/mining/jobs/{jobId}/resume
POST /api/mining/jobs/{jobId}/run-now
POST /api/mining/jobs/{jobId}/gaps/{gapId}/refill
```

旧 `/api/agent/threads|runs|events|preferences|backends` 和 Python Agent Gateway 已删除，
没有兼容转发。Agent Session、Transcript、Provider 登录、模型目录和实时状态由
`agent_web/` 的 Kanna Event Store 与 WebSocket 协议负责；Engine 不保存第二份 History。

旧 `POST /api/agent-handoffs`、`/exchange`、Agent `/api/trade-context/exchange` 与
Engine `/api/events` 已删除。跨窗口状态由 Kanna `/ws/ui` 统一承载，不能恢复旧的手动
按钮或 ticket compatibility。Agent Web 通过 Engine
`/auth/session` 验证同一个 `trade_session`，因此两端不再分别登录。浏览器只提交逻辑
`projectId`，Agent 服务端把它解析为批准的 TradeEngine 或策略 workspace，绝不公开
绝对路径。

Agent Web 的 Session 创建后永久绑定 `claude-deepseek` 或 `codex-openai`；后端切换
必须新建 Session，模型可以在同后端的下一 Turn 切换。每次发送携带
`clientRequestId` 和独立的 `TradeContextV1`，Event Store 负责幂等、owner 隔离、
断线恢复和 native session resume。DeepSeek 凭据与 Codex 私有 `CODEX_HOME` 均由
Agent 服务端管理，不使用当前 Unix 用户的 Codex 登录。

另有三个内部 bridge 路由：`/api/agent-tools/grants`、`/api/agent-tools/call` 和
`/api/agent-tools/grants/revoke`。它们只接受 Agent 服务持有的 bridge bearer，不是
浏览器 API。Turn grant 绑定 owner/chat/turn/Context，原始 token 通过一次性 `0600`
文件交给 MCP 并立即删除，Turn 终态再主动撤销。七个资源工具只允许 read、inspect、
validate 和创建展示用 Proposal；三个 UI 工具只读写当前页面登记的草稿/文本 buffer，
不接受路径，也不会隐式 Save/Publish/Run/Submit。

Mining 使用与 `controlRoot/releaseRoot/liveRoot/sourceRoot` 不重叠的独立
`miningRoot`，新部署必须使用 fresh root；已有数据库必须精确匹配当前 schema
version、结构指纹和实际结构。服务不会补字段或迁移无法识别的数据库，而是在启动
worker 前 fail-closed。主动 Supervisor 默认关闭；启用后，其子进程和监控线程
必须被证明已停止，Engine service 才会释放 Owner authority。

Backtest 正文没有全量 JSON 读取接口。`/meta` 只返回数据库中的严格索引元数据；
`/result` 按显式 `path` 流式校验并写出投影，避免把完整 cycles 物化到 Engine
控制进程。旧的 `GET /api/backtests/{backtestId}` 和 `/visualization` 全量路由不存在。

Module、Pipeline、Environment、Analysis、Sampler 和 Dataset Script 的客户端 Draft 都不得指定 Engine-owned Version 字段。Draft 必须先通过对应资源发布接口归档；Backtest 只接受用户显式选择的已归档 Version，不会隐式保存、归档或替换资源版本。

已删除的 `/api/attach`、`/api/detach`、`/api/pipeline-versions` 和 `/revisions` 路由不存在，也没有兼容转发。

## Pipeline 后端约束

Pipeline Definition 的字段固定为：

```text
pipelineId, name, config.observationInput, instances, stages, signalGraph
```

`stages` 只允许 `universe / target / constraint`。后端强制每个实例只属于一个 stage、kind 与 stage 一致、Signal 只存在于 `signalGraph.nodes`、所有实例必须被 stage 或 Signal Graph 使用。Constraint 完成后的 Data Dict 就是 Pipeline 输出；成交、Fill、Fee、Slippage、Settlement 与账户更新只属于 Environment。Analysis、Environment 以及已删除的 Input/Execution stage 字段都会被拒绝。

Graph compiler 对 Signal、Environment、Analysis 统一验证端口、Schema、I/O 边界、孤立实例和环，再产生稳定拓扑。前端的连线限制只是即时反馈，后端始终是最终约束来源。

## Backtest 冻结与周期语义

Backtest 提交固定 Dataset Version、Sampler Version、Pipeline Version、Environment Version、Analysis Version，以及三张 Graph 所引用的所有 Module Version。Job 只接受 Engine 生成且 hash 有效的 frozen snapshot；缺少资产或 verification 失败时不会读取当前资源补齐。

执行过程中，每个完成周期直接写入当前 Backtest 目录中的临时 Result，全部生命周期成功结束后再以原子替换发布为唯一的 `result.json`。SQLite 只保存 Backtest 请求、状态、指标和 Result 索引，不保存第二份 Result 正文；提交接口返回元数据，需要正文时再从只读 Result 文件显式加载。这样周期数增加时不会在内存、文件和数据库之间同时保留多份完整结果。

每周期：

```text
Sampler -> Sample
Sample + Environment Graph 显式绑定的 last.<DataKey> -> Environment -> Observation
Observation -> config.observationInput projection -> Pipeline initial Data Dict
Pipeline initial Data Dict -> Pipeline -> current completed Pipeline Data Dict
Sample + Analysis Graph 显式绑定的 last.<DataKey>
  + Analysis Input 显式选择的 currentPipeline.<DataKey> -> Analysis -> Result only
current completed Pipeline Data Dict -> next cycle可被显式绑定的 last.<DataKey>
```

Environment 只有显式 Graph Output 能组成 Observation；Pipeline 只能通过已编译的 `config.observationInput` 投影读取它，裸 Sample 不进入 Pipeline。Analysis 编辑器只对 Input Boundary 提供 `Cycle Sample + prior Pipeline` 与 `Current completed Pipeline` 两个明确来源，输出不进入 Pipeline，也不进入下一周期 `last.*`。
