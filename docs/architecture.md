# TradeEngine 架构权威规范

状态：生效中
架构规范版本：1
适用范围：Engine 控制面、归档、编译、Runtime、Backtest worker、Job、公共 SDK，
以及这些边界与策略仓库之间的关系。

本文是后续 Engine 重构的权威架构约束。历史审计文档用于说明当时发现和修复过的
问题，但“某一快照没有已知 P1/P2”不代表本规范的职责、依赖和可维护性要求已经
满足。实现、测试或旧接口与本文冲突时，应修改实现并删除旧入口；不得用兼容层、
fallback 或第二条执行路径保留冲突行为。

管理员账户的既有硬编码是用户明确要求保留的唯一例外。该例外只属于认证边界，
不得扩散为其他 Engine 资源、策略、时间、DataKey 或执行路径的特例。

## 1. 不可变总原则

1. Engine 只实现可泛化的资源、契约、编译、执行和持久化能力，不识别策略 ID、
   策略 DataKey、开闭盘、warmup、事件或某个策略的调用频率。
2. JSON object 的成员顺序没有语义。所有影响执行的顺序必须在编译结果中成为显式
   array、edge 或 ordered plan，Runtime 不得从 object 插入顺序重新推导。
3. Frozen composition artifact 是 worker 唯一的执行计划和合约权威。归档内容只用于
   身份、摘要和实现完整性证明；worker 不得从当前索引读取资源，也不得从原始
   Definition 重新生成另一份执行计划。
4. Runtime 只能由本调用栈内验证完成的 nominal authority 建立。可执行 plan、
   definitions、invoker、adapter 和生命周期状态不得由外部替换。
5. Engine 假设 Module 有状态。每个周期必须按正式语义调用 Module；不得因输入相同
   在 Engine 中跳过调用。策略内部是否缓存只能由策略 Module 自己决定。
6. 每个信任边界只进行一次严格验证，同一调用栈后续只复用 nominal proof；禁止
   “先验证、再重编、再用 equality 猜测等价”的第二事实来源。
7. Pipeline、Environment、Analysis、Sampler、Dataset 与 Result 是独立边界。
   Composition 是唯一可以组装多个边界的层。
8. 不保留旧协议、raw Runtime 构造、兼容别名、可选生命周期、catch-and-fallback
   或异常后改走另一实现。清理阶段可以在保留原始异常的前提下尽力释放资源，但
   清理失败不得改变执行语义、伪造成功或选择备用执行路径。

## 2. 固定依赖 DAG

目标源码位于 `engine/`，按下列 package 迁移。箭头表示“右侧可以依赖左侧”：

```text
core -> contracts -> archive/control/repository -> authority -> compiler
                                                   -> runtime
compiler + runtime + authority -> composition -> worker -> jobs -> service
```

`compiler` 与 `runtime` 是同一权威计划的两个消费者关系，不是 Runtime 现场调用
compiler 的关系。Freeze/composition 可以调用 compiler；frozen worker 不可以。

目标 package 及允许的内部依赖如下。未列出的 target package 不得自行出现，必须先
修改本规范和架构测试并经过设计评审。

| Package | 唯一职责 | 允许依赖的 `engine.*` package |
|---|---|---|
| `core` | 无 Engine 依赖的时钟等最小基础原语 | `core` |
| `contracts` | 纯数据结构、Schema、DataKey 和协议校验 | `core`, `contracts` |
| `archive` | 通用不可变目录、摘要、原子发布原语 | `core`, `contracts`, `archive` |
| `control` | 物理控制数据库 schema 与唯一 connection authority | `core`, `contracts`, `archive`, `control` |
| `repository` | 索引和持久化事务；不执行资源 | `core`, `contracts`, `archive`, `control`, `repository` |
| `authority` | immutable Definition/compiled proof | `core`, `contracts`, `archive`, `authority` |
| `compiler` | 从已验证输入生成唯一显式计划 | `core`, `contracts`, `authority`, `compiler` |
| `runtime` | 只消费 authority；Module/Graph/transport 生命周期 | `core`, `contracts`, `authority`, `runtime` |
| `composition` | Freeze、跨域合约绑定、artifact 验证与 nominal bind | `core`, `contracts`, `authority`, `compiler`, `runtime`, `composition` |
| `worker` | 一次性 frozen request 执行与进程宿主 | `core`, `contracts`, `archive`, `authority`, `runtime`, `composition`, `worker` |
| `jobs` | Job repository、状态机和 worker supervisor 协调 | `core`, `contracts`, `archive`, `control`, `repository`, `authority`, `composition`, `worker`, `jobs` |
| `service` | 最外层 API/use-case 组装 | 以上全部及 `service` |

低层不得 import 控制 API、Job 或 Service。`worker` 不得 import `compiler`；它必须
直接绑定 artifact 中的 exact plan。`jobs/manager.py` 不直接写 SQL，不 import God
module，只依赖明确的 JobRepository、FrozenRequestService 和 WorkerSupervisor 能力；
Job SQL 只能存在于 `jobs/repository.py`。

目标 package 内还必须满足：

- 跨模块 private import 或 `module._private` 调用数量为零；
- package 根 `engine/__init__.py` 不作为旧根模块的 re-export 兼容门面；
- 静态 import graph 无强连通分量；函数内 import 不得用于隐藏逆向依赖；
- Engine 生产源码不得 import `strategies.*` 或 `vendor.*`；
- Strategy 只能通过公共 SDK、发布 API 和归档协议接入，不能成为 Engine 依赖。

## 3. 职责边界

### 3.1 Contracts、Archive、Repository、Authority

- Core 不知道数据库、资源类型或 Runtime；它只容纳确有多个低层共同依赖的最小原语。
- Contracts 只描述并严格校验数据，不访问数据库、文件仓库、子进程或 Runtime。
- Archive 只负责通用不可变发布和验证，不知道 Pipeline、Sampler 或策略身份。
- 所有数字资源版本只由 Engine 分配，必须是从 `1` 开始、无符号且无前导零的规范
  十进制字符串；归档 record 中的 `archive.root` 必须是 managed root 内逐字唯一的
  规范绝对路径，不接受相对路径、`.`、`..` 或符号链接别名。版本身份与路径必须双射。
- Control 只拥有物理数据库 schema、准备过程和 connection authority，不实现任何
  Dataset/Pipeline/Result repository use case。
- Repository 只负责索引和事务，不编译、不构建 Runtime、不执行策略代码。
- Authority 冻结验证后的身份和材料。公开属性返回不可变值或副本；Runtime 所需材料
  通过窄 nominal binder 传递，不允许调用方写私有缓存伪造 proof。

### 3.2 Module 与 Graph

- Module authority、实现隔离、adapter 与 invoker 分属 authority/runtime 责任；
  Python Module 在一个 Backtest Python worker 内直接调用，ProcessRunner 才使用
  独立进程通信。
- Python Module 可把一个策略私有缓存 generation 提交给 ModuleInvoker；首次仍执行完整
  输出验证和隔离，Engine 仅保存验证后的编码快照，后续同 generation 每次重新调用
  Module 并为消费方生成新输出所有者。该 nominal proof 绑定 exact adapter 和 invocation
  authority，并在 generation 变化、restore、finalize 或 close 时失效；不得扩展成跳过
  Module 调用、跨 invoker 借用或 ProcessRunner 的第二路径。
- Graph compiler 泛化处理节点、实例、definitions、端口、Schema、edge、拓扑与显式
  I/O 顺序。`nodes == instances` 且 referenced definitions 精确，不忽略 orphan。
- Graph Runtime 不重新编译，不查询 Repository，不跳过 Module 语义调用。

### 3.3 Dataset 与 Sampler

- Dataset authority 证明不可变容器及 capability；Dataset Runtime 只提供正式
  capability，不向外暴露可写 proof 缓存。
- Sampler 是独立资源，唯一职责是把已验证 Dataset 转换为有严格合约的 Sample/Frame。
  它不是 Module 快速路径，也不拥有 Pipeline、Environment 或市场事件语义。
- Sampler Draft/Version 字段与参数 Schema 推导只由 `engine/contracts/sampler.py`
  拥有；SQLite row 解码、列表、精确读取和不可变发布事务只由
  `engine/repository/samplers.py` 拥有。Repository 自持完整 control lock，且不得
  import authority、compiler 或根模块。
- Sampler 编辑与发布用例只由 `engine/service/sampler_workspaces.py` 拥有；受管
  Workspace 根和路径约束统一在 `engine/repository/workspace_paths.py`。Workspace
  root、target、identity marker、`sampler.json` 与 `sampler.py` 都不得经过符号链接。
- 所有执行态 Sampler 必须继承统一 `SamplerRuntime` 协议；协议固定提供 Python
  `__len__`。默认算法必须通过 `fork_for_counting()` 创建同类型、状态隔离的计数
  Runtime，在副本上完整迭代并可靠关闭；禁止在正式 Runtime 上预迭代或用通用
  `deepcopy` 复制进程、锁和文件 authority。能用 Dataset capability 或索引直接求值的
  实现应覆盖该算法。Backtest 必须验证正式执行的实际 emission 数量与该长度完全一致。
- Provider 只管理 Sampler iterator、帧序和生命周期；Sampler 的 `close` 是必需协议，
  不接受协议外的 duck-typed 对象，也不使用 `getattr` 静默跳过。

### 3.4 Pipeline、Environment 与 Analysis

- Pipeline 独占 stage/phase 顺序、Signal Graph 和 Pipeline Data Dict 演进。
- Pipeline 精确执行版本、`pipeline.json` 与 `control-snapshot.json` 读取只由
  `engine/repository/pipelines.py` 拥有；从 verified Module Definition authorities
  重建 manifest 只由 `engine/compiler/pipeline_manifest.py` 拥有。历史 manifest hash
  与 Backtest evidence hash 分别使用 `engine/contracts/pipeline.py` 和
  `engine/contracts/backtest.py` 的正式摘要合约。
- Environment/Analysis Graph 的版本索引、不可变发布与精确版本读取只由
  `engine/repository/graph_resources.py` 拥有；通用 control JSON、事务锁与 history
  分别统一在 `engine/repository/control_state.py`，资源路径段约束位于
  `engine/contracts/archive.py`。根目录不保留 repository facade。
- Environment 与 Analysis 只通过通用 Cycle/Graph authority 与 Runtime 实现；Engine
  不固定 Fee、Fill、Settlement、账户、Benchmark 等业务槽位。
- Environment 的显式 Graph Output 组成 Observation；Pipeline 只通过版本化
  `config.observationInput` 投影建立初始 Data Dict。Analysis 输出只进入 Result。
- 任何跨三者的契约解析只能发生在 Composition，不能互相 import 特化实现。

### 3.5 Composition、Worker、Result 与 Job

- Composition 是唯一跨资源组装点。Freeze 生成 artifact；worker 验证 frozen 资源
  身份后直接 bind artifact exact plans/contracts。
- Worker 不读 current index，不调用发布/control API，不在 artifact 缺失时现场 freeze。
- DataKey projection 的公开默认仍隔离所有值；只有 frozen Backtest kernel 可在生成
  previous-cycle snapshot 时共享只读叶节点。该唯一调用点由静态门禁锁定，后续 Graph、
  Pipeline 与 Analysis 写入必须使用 copy-on-write，Module 输入必须先隔离，Result writer
  必须在 `append` 返回前同步编码，任何一项改变都必须使所有权回归失败。
- Result writer 只消费完成的周期，负责流式写入和不可变封存；catalog 提交由父进程
  recovery service 完成。Result projection 位于
  一次性 Result Runtime，不在常驻控制进程执行策略 Module。
- Result 临时 Module 的依赖编译、流式投影、跨层组合、进程监督与 worker 入口分别由
  `engine/compiler/result_projection.py`、`engine/runtime/result_projection.py`、
  `engine/composition/result_projection.py`、`engine/runtime/result_runtime.py` 与
  `engine/worker/result_runtime.py` 拥有；worker 只依赖正式 package owner，旧根
  `market_data.py` 已被替代并物理删除。
- Result 数据/可视化/组合合约分别由 `engine/contracts/result.py`、
  `engine/contracts/visualization.py`、`engine/contracts/backtest_composition.py` 拥有。
  Visualization compatibility 编译、双表 current/saved 原子事务和 Result/Module 组合
  分别只由 `engine/compiler/visualization.py`、
  `engine/repository/visualizations.py`、`engine/service/visualizations.py` 拥有；Result
  sealed manifest/content digest 不随可视化保存改变。Result 索引与归档事务只在
  `engine/repository/backtest_results.py`，流式封存只在
  `engine/worker/result_writer.py`，流式读取只在 `engine/runtime/result_stream.py`。
- immutable Result 的 `_backtests/<id>` 路径协议只由
  `engine/archive/backtest_result.py` 拥有；未索引 sealed Result 的 manifest 读取、双表
  catalog 事务及 commit-ACK reconcile 只由 `engine/repository/backtest_results.py`
  拥有；expected frozen request、
  snapshot、Result metadata 与 catalog evidence 的跨层恢复验证只由
  `engine/service/backtest_results.py` 组装。
- Job manager 只管理 Job 状态转换和 worker 监督；SQL 在 JobRepository，冻结请求在
  FrozenRequestService，PID 身份和进程树统一在 process supervision。
- frozen execution kernel 与一次性 module entry 分别位于
  `engine/worker/backtest_execution.py` 和 `engine/worker/backtest_runtime.py`；前者只
  返回 `backtestId/cycleCount/contentDigest/resultSize` 内部证据，后者由 supervisor
  通过 `python -m engine.worker.backtest_runtime` 启动。direct API 的临时 execution
  root、Backtest ID 与父进程 catalog recovery 由
  `engine/service/backtest_execution.py` 拥有。
- Backtest/Result/Dataset worker 使用不继承控制进程 `HOME`、user site 或 ambient
  `PYTHONPATH` 的最小宿主环境，并显式设置 `PYTHONNOUSERSITE=1`。Backtest Python
  environment identity 按解释器 site-root 的实际优先级哈希每个受权安装根的完整
  非 cache 文件树；目录、普通文件、符号链接及其有界目标都进入摘要。外部 path-only
  `.pth`、egg-link、editable distribution、broken/special entry 与无界符号链接一律
  fail closed。非 editable 的 executable `.pth` 是已哈希的受信安装 hook；正式部署
  不得让它动态暴露受权 roots 之外的代码，也不得让 Backtest 依赖仅在控制进程 ambient
  import path 中存在的包。

## 4. 禁止兼容接口和 fallback

下列规则数量必须为零：

- 直接调用 authority-bound Runtime/Sampler/DatasetHandle 的 raw constructor；
- 缺失 artifact 后现场 freeze，或 artifact 校验失败后从 Definition 重编执行；
- 运行时读取当前 Pipeline/Environment/Analysis/Dataset/Sampler 索引；
- optional `close/finalize/snapshot/restore/invoke/execute` 生命周期；
- `pipelineEventRouting`、`executionOnlyValues`、`invocationPolicy`、
  `invoke_on_input_change`、`transientDataKeys` 等已删除协议；
- 以 `legacy`、`compat` 或默认参数为名保留第二条执行语义；
- 捕获验证/编译/执行异常后返回默认值或尝试另一个实现。

删除旧入口时必须同步迁移所有调用者和测试，然后删除原符号；不得用旧文件 re-export
新实现。只有“释放多个资源且最终重新抛出第一个错误”的清理聚合可以继续执行后续
cleanup，这不构成执行 fallback。

## 5. Engine 与策略的分阶段变更

Engine 与策略不能在同一阶段、同一提交或同一发布中更新：

1. **Engine 阶段**：仅修改通用 Engine/SDK；不得引用 TLM ID、TLM DataKey、日期、
   市场状态或策略值。先通过 Engine 回归、artifact canonical transport、frozen worker
   no-current-control-read、父子 DataKey 写序、lifecycle、真实 row-map worker 和 Result
   recovery；记录 build identity、结果 identity/hash 与性能。
2. Engine 验收后冻结该 build。若 Engine 阶段结果或性能不通过，只能继续修 Engine，
   不能同时用策略修改掩盖。
3. **策略阶段**：只在已冻结 Engine 契约上修改策略 Module、Graph 或私有缓存；不得
   修改 Engine。策略变化必须重新发布版本并验证远端指标、逐周期语义、checkpoint、
   Result 完整性与性能。
4. 策略需求不得通过 Engine 新增开关、必填优化字段、策略调度、事件或功能限制实现。

## 6. 性能验收边界

机器可读常量：

```text
ENGINE_ARCHITECTURE_SCHEMA_VERSION = 1
```

TradeEngine 不内置任何策略的 canonical request、周期数、预期 Result 或耗时阈值。
这些验收参数和证据由对应私有策略仓库固定。计时从启动
`python -m engine.worker.backtest_runtime` 前开始，到 worker
成功退出且父进程将 sealed Result 恢复提交为止；计入 frozen archive 验证、authority bind、Runtime/Graph
构建、Sampler、全部周期、Result encode/write/finalize/seal。资源安装与发布、排队、
服务启动和 Visualization 不计入该 worker wall time。

Result manifest schema 4 的 `result.json` 仍是一个完整合法 JSON document，同时固定将
每个 cycle 编码为一条物理行。换行仅是不可信的并行切片候选，不能作为 worker validation
receipt：Backtest 的进程内 PythonModule 与 Result writer 共享解释器，父进程不得采信该
进程自行声明的 schema 校验结果。恢复时由 Result Runtime owner 通过统一
`ProcessSession`/outer-subreaper authority 启动最多八个全新
`engine.worker.result_verifier`；每个 verifier 独立严格解析其连续字节范围并验证完整
cycle contract。父进程必须证明 prefix、全部 range 与 metadata suffix 对 Result 每个
字节无洞、无重叠覆盖，重算完整 SHA-256，并用磁盘 ledger 按绝对周期顺序合并全局
`cycleId` 唯一性。任一 verifier 未退出、证据文件不完整或 cleanup 未获内核 quiescence
证明时，catalog recovery 必须 fail closed 并保留 session/owner lease authority。

在固定性能机器、无竞争负载下使用相同 frozen request 重复运行全新 worker；结果
identity、cycle count、指标和完整 Result 语义必须一致。任何通过删字段、缩短周期、
预热常驻策略进程、关闭验证、增加策略专用 Engine 开关或缩窄功能取得的数值均无效。

公共结构测试只冻结 Engine 的通用计时和证据边界，不冻结私有 workload。每个策略
发布的验收记录必须在其私有仓库附原始 wall time、机器身份、Engine build、snapshot
hash、Result identity 与正确性证据。

## 7. 当前迁移债务与验收方式

本轮目标迁移已经完成：Clock、Control Database/Schema/Auth、Module
authority/adapter/invoker、Graph/Cycle Graph contracts/compiler/authority/runtime，
Pipeline contracts/compiler/authority/runtime，以及 Backtest Jobs
repository/manager/worker supervisor 已迁入目标 package；对应
根目录旧文件已在调用者迁完后删除，未保留 façade。
Dataset archive/contract/authority/repository/service 与 Sampler
archive/contract/authority/repository/runtime/Provider/Workspace service 也已迁入目标
package；根 `dataset_archive.py`、`backtest_sampling.py`、`sampler_lifecycle.py` 已删除，
旧根 `market_data.py` 也已在 Visualization 最终 cutover 后物理删除，Engine HTTP 与策略
reproduction 工具均直接调用正式 service，不保留 façade。Pipeline execution read、control
snapshot 与 manifest rebuild 均只通过正式 Engine owner 完成；控制层入口属于
`engine/service/control_api.py`，根目录不保留转发 façade。Backtest fixed-point、
frozen artifact nominal bind 和 Graph Runtime assembly 已迁入
`engine/composition/backtest.py`，repository-backed resolve/validate/freeze 已迁入
`engine/service/backtests.py`，完整 Python/时区/源码 build identity 位于
`engine/core/runtime_identity.py` 与 `build_identity.py`。Result catalog recovery 已迁入
`engine/service/backtest_results.py`，未索引 sealed archive 的精确读取、双表事务与
commit-ACK reconcile 已归属 `engine/repository/backtest_results.py`。frozen kernel、module
runtime entry 与 direct recovery API 已分别迁入 `engine/worker/backtest_execution.py`、
`engine/worker/backtest_runtime.py`、`engine/service/backtest_execution.py`；根
`backtest_runtime_worker.py` 已物理删除，supervisor 只使用 package module 入口。
Result contracts、stream、writer、repository、projection compiler/runtime、
独立 worker 与 service 入口均已迁入目标 package；根 `result_archive.py`、
`result_runtime_worker.py` 与旧符号均已删除且没有 façade。
Environment/Analysis 的字段与纯 Graph normalization、Module Graph 编译验证、BuiltIn
与版本选择、外层查询以及 authority-bound Runtime factory 已分别迁入
`contracts/compiler/repository/service/runtime`；根 `backtest_environment.py` 与
`backtest_analysis.py` 已删除且不保留 façade。
Jupyter Workspace 的 identity contract、私有 runtime 文件、sandbox profile、HTTP/WebSocket
proxy 与 lifecycle orchestration 已分别迁入 `contracts/repository/runtime/service`；根
`jupyter_workspaces.py` 已物理删除。Dataset one-shot build、Jupyter long-lived server、
Backtest runtime 与 Result projection 四类生产 writer 统一使用
`engine/runtime/process_session.py` 的持久 outer subreaper authority，但保留彼此独立的
network/rlimit/output profile。Engine claim owner 后，registry 先自持 owner lease 的
`F_DUPFD_CLOEXEC` 副本，并只将该 OFD 传给 outer supervisor；primary writer 始终
`close_fds=True` 且不继承 lease。Engine 异常退出或显式关闭父 lease 后，旧 outer 在
内核 `waitpid` 明确证明无 child 前继续持锁，因此新 Engine claim、reconcile、hash/publish
与 Workspace delete 都不能越过仍可能写入的旧进程树。正常 shutdown 先停止请求接入并
join 非 daemon HTTP handlers，再清四类 registry session、关闭 registry lease 副本，
最后关闭 owner lease。
Dataset Workspace、Recipe、Build Job 与受管 scratch 路径也已分别迁入
`contracts/repository/runtime/service`；根 `dataset_workspaces.py` 已物理删除。Build
提交事务用不可变 receipt 处理 durable commit 后 ACK 丢失，发布和终态化必须先证明
writer quiescence，Workspace lifecycle 锁始终先于 control transaction 锁。

`tests/engine/architecture/` 对以下内容提供第一组机械门禁：

- 新 `engine/` package 的固定依赖 DAG、跨模块 private、domain 边界及静默 fallback；
- Engine 到策略实现的依赖、已删除协议和 raw Runtime 构造必须保持为零；
- 已知根目录跨层 import、跨模块 private 和静默 callback 债务使用精确清单锁定。

首份债务基线为：

| 类型 | 精确遗留项 |
|---|---|
| 跨模块 private | 无 |
| 跨层 import | 无 |
| 新 package 暂借根模块 | 无；首批迁移已经清零 |
| 静默 callback | 无；Job event callback 异常保留为日志证据 |

债务清单采用 exact equality：新增和删除都会使测试失败。修复债务时，必须在同一变更
删除实现中的依赖及测试中的对应清单项；清单在评审中只允许减少，不允许为了让测试
通过而增加。文件行数仅用于识别风险，不是拆分完成标准。

当前已知根 boundary debt 为空。后续只按职责审计继续收窄正式 package owner；任何新迁移
仍须在调用者完成切换后物理删除旧入口，不得创建 façade。`docs/audits/` 中引用旧
`market_data.py` 行号的内容是迁移前历史快照，现行 authority 以本文件列出的四层 owner
与机械门禁为准。

每一步都必须保持公开行为、artifact、identity/hash、错误优先级和资源生命周期不变。
拆文件本身不是功能或性能优化；只有职责唯一、依赖方向和 authority 边界可由测试机械
证明时，该迁移才算完成。
