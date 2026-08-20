# TradeEngine 设计规范

## 1. 单一运行时与控制面

平台只有一个权威控制面和一个 Python/Data Dict 执行后端。在线服务持有控制目录 Owner 时，离线工具不得读写同一生产状态；生产变更应通过认证 API，或在 Engine 停止后运行明确的离线维护工具。

平台不支持 Lean、Remote Instance、RemoteService、角色专用 worker 协议或第二套 Pipeline 语义。Module 可以在其已归档代码内部调用 HTTP 或管理子服务，但这些行为仍属于该 Module 的实现和生命周期。

## 2. 资源边界

- Pipeline、Environment、Analysis 是三个独立顶级资源，只在 Backtest 中按版本组合。
- Pipeline Module、Environment Module、Analysis Module 分属三个仓库，共享同一公共 Module 与 Version Archive 基础设施。
- Signal Graph 属于 Pipeline；Environment Graph 和 Analysis Graph 属于各自顶级资源。
- Graph 中的计算节点是普通同级 Module 实例。Graph 和页面卡片都不是 Container Module。
- Data Input/Data Output 是 Graph 内外连线的虚拟边界节点，没有 Module ID、Version 或生命周期。
- Pipeline Definition 不得包含 Environment 或 Analysis 字段。

## 3. 统一 Module 契约

每个 Module Version 必须包含 `kind / moduleId / version`、`activationMode / parameters`、`configSchema` 和 `ports.inputs / ports.outputs`。进程启动入口只由严格校验的 `parameters.command / arguments / workingDirectory` 定义，不存在第二套 `entryPoint`。所有 Module 继承同一个公共 `Module` 基类，并遵守：

```text
initialize -> invoke* -> finalize -> close
                  snapshot / restore
```

Engine 只传递实例端口明确绑定的数据，只接受声明过的输出。禁止完整 Data Dict、Instance 状态键、角色专用 payload 或未声明上下文进入 Module 协议。

BuiltIn 不是另一套 Module 类型或激活方式。它只是 Engine 拥有源代码和身份的普通已归档 PythonModule，必须经过相同基类、端口校验、版本门禁和生命周期。标准 Python Module 在每个 Backtest 的独立 Runtime 进程内直接调用；只有原生、外部语言或外部程序归档使用 ProcessRunner 子进程边界。

## 4. Graph 编译

Signal、Environment、Analysis Graph 在执行前统一完成：

1. 校验实例引用的 Module Version 已归档且完整；
2. 校验端口绑定、DataKey 和递归 JSON Schema；
3. 校验必需输入与显式 Graph I/O 边界；
4. 拒绝孤立实例、未知节点、重复实例归属和环；
5. 固化稳定拓扑，Runtime 只按该顺序初始化和执行普通 Module。

Pipeline 后端还必须强制 `Universe / Target / Constraint` stage 与 Module kind 一致。Signal 实例只能位于 Signal Graph，不存在 `stages.signal`。`Constraint` 可包含多个实例，`Universe`、`Target` 各最多一个实例。Constraint 完成后的 Data Dict 就是 Pipeline 输出；成交、Fill、Fee、Slippage、Settlement 与账户更新属于 Environment。Pipeline 通过自身 `config.observationInput` 选择 Observation 字段，不存在 Input Module、Input stage 或 Execution Module/stage。

按当前明确决定，平台暂不保证多个输出 DataKey 的业务唯一性；图作者负责避免非预期覆盖。这不是版本唯一性问题。

## 5. 周期语义

默认周期输入是本周期 Sample 裸字段和上一周期完整 Pipeline Data Dict 中被 Graph 显式绑定的 `last.<DataKey>`。`last` 只是路径前缀，不是可绑定的 DataKey；任何 Module 或 Graph 边界都不得读取完整 `last` 根对象。

- Environment 在临时 Data Dict 上执行，显式 Graph Output 组成本周期 Observation；
  Pipeline 经 `config.observationInput` 投影后才建立初始 Data Dict，裸 Sample 不得隐式穿透。
- Pipeline 在本周期 Data Dict 上执行，完整结果成为下一周期状态。
- Analysis 在 Pipeline 完成本周期 Data Dict 后恰好执行一次。未声明 `source` 的 Input 保留 `Sample + last.*`；需要本周期完成状态的 Input 必须显式声明 `source: "currentPipeline"`。两种输入可同时连接，不允许 Engine 猜测 Analyzer 的业务时序。显式 Graph Output 只进入 Result，绝不进入 Pipeline 或下一周期 `last.*`。

不得把 `last.*` 全局改义为同周期状态，也不得在 EOF 隐式重跑或 drain Analysis：前者会删除合法的上一周期比较能力，后者会制造没有新 Sample/decisionTime 的第二次 observation。Analysis Input source 是编译期 Schema/requiredness 插槽，不是运行时开关；省略它即为唯一明确的默认周期源，不执行 fallback。

Fee、Fill、Slippage、Settlement、账户、Benchmark 等能力必须实现为有真实逻辑、端口与配置 Schema 的普通 Environment Module，不得成为 Engine 编排器的固定槽位。

## 6. 唯一版本流程

所有可执行版本化资源遵守同一流程：

1. 客户端提交 Draft，不得指定或覆盖 version/status/archive 字段；
2. 公共归档器验证现有完整单调历史并检测有效内容变化；
3. 内容未变化时返回最新 Version，不产生副本；
4. 内容变化时由 Engine 分配 `max(version)+1`；
5. 完整目录写入 staging，生成逐文件摘要、记录快照和 manifest；
6. 目录只读并通过两次 verification 后原子移动到最终位置；
7. 只有目录提交成功，索引才可更新为 Archived；索引提交失败必须删除新目录；
8. Runtime、Backtest 和临时可视化均只能初始化已归档且再次验证通过的 Version。

Module、Pipeline、Environment Graph、Analysis Graph、Sampler 和 Dataset Script 共享 `engine/archive/version.py`。Sampler Version 必须包含自己的 runtime manifest 与执行资产，Runtime 只能按该归档 protocol 激活，不能从当前 Engine 临时复制 worker/SDK 或按 Sampler ID 分派。Dataset Script 只能通过已归档的 `recipeId + recipeVersion` 执行，Workspace 路径和原始脚本文本只能用于发布 Draft，不能直接进入 Build。Dataset 不是 Module，使用内容地址版本，但必须执行等价的完整目录、逐文件摘要、只读、原子提交和运行前 verification。

Backtest 组合必须由用户显式选择 Pipeline、Environment、Analysis、Dataset、Dataset Version 和 Sampler Version；UI 不得根据 BuiltIn 身份、资源 ID、当前版本或列表顺序自动补全。执行快照除所有资源版本外还必须冻结并校验 Engine build 与 Python runtime 身份。

旧协议、旧 Result、旧 Graph 或已删除资源模型只能整体存档，不能保留执行兼容接口。
