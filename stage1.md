# Stage 1 功能可用性结论

本文基于当前模块框架评估未来前端可提供的能力。假设前端存在，它应把控制 API 当成唯一入口，不直接编辑 Engine config、环境变量或 live manifest。

## 1. 拖拽上传模块、验证并入库

部分可用。

当前 `add-module` 已经能把模块实现加入对应模块仓库，支持 DLL、本地脚本和远程 artifact：

- 本地文件通过 `--file local_path:module_path[:x]` 上传；
- 远程文件通过 `--remote-file url,module_path[,sha256=<hex>][,x]` 下载；
- API 会校验 `kind/moduleId/version/activationMode/entryPoint/hotSwapMode`；
- API 会校验 artifact 路径安全、远程 URL scheme、sha256；
- 入库后模块会出现在 `list-modules` 中。

但“验证有效性”目前还不完整。现在主要是结构和参数校验，不是完整 runtime 校验。前端若要做到“上传后有效模块入库、无效模块拒绝”，还需要后端补一个 dry-run validation 流程：

- DLL 能否加载；
- `entryPoint` 是否存在；
- 类型是否实现 `IModule` 或对应可插拔接口；
- 构造函数是否能用实例 config 创建；
- RemoteService 是否实现协议、通过 `CheckHealth`，以及提交的 `backend` 指纹是否匹配不可变部署；
- ScriptRunner/OutOfProcessWorker 的 `command` 是否可执行；
- 模块声明的 `hotSwapMode` 是否合理。

## 2. 选择装载模块并填写属性

基本可用。

当前框架已经把“模块实现”和“带参数的模块实例”分开：

- `list-modules` 展示模块仓库；
- `create-instance` 基于某个 `kind/moduleId/version` 创建实例；
- 实例可携带 `config`；
- `attach` 才真正把实例接到运行中的 pipeline。

例如 `json-input@builtin` 可以把股票列表作为 config：

```json
{
  "instanceId": "input.us-largecap",
  "kind": "Input",
  "moduleId": "json-input",
  "version": "builtin",
  "config": {
    "symbols": ["SPY", "QQQ"],
    "resolution": "Daily",
    "securityType": "Equity",
    "market": "usa"
  }
}
```

Lean 预置模块也已支持按构造函数参数名绑定 config。例如：

```json
{
  "instanceId": "signal.ema.fast",
  "kind": "Signal",
  "moduleId": "ema-cross-alpha",
  "version": "builtin",
  "config": {
    "fastPeriod": 8,
    "slowPeriod": 21,
    "resolution": "Daily"
  }
}
```

前端可以基于模块的 `configSchema` 或模块文档生成参数表单。当前不是所有预置模块都有完整 `configSchema`，所以 UI 层若要自动生成高质量表单，还需要补 schema 元数据。

## 3. 替换某个模块

部分可用，但正确语义应该是“新增版本并切换接入”，不是覆盖。

当前 custom 模块不能重新 `add-module` 同一个 `kind/moduleId/version`。这符合可复现性要求：正式替换必须发布新版本，再把 pipeline 接到新版本实例。

推荐前端实现：

1. 用户上传新模块实现；
2. 后端验证通过；
3. 写入同一个 `moduleId` 的新 `version`；
4. 创建新实例，或更新实例指向新版本；
5. 调用 `attach` 切换当前 pipeline；
6. 老版本保留，用于历史复现和回滚。

也就是说，前端按钮可以叫“替换”，但后端行为应是版本化发布。

## 4. 数据依赖处理

结构上部分可用，运行时增量依赖还未完成。

当前 pipeline 已经按 stage 拆分：

- `inputs`
- `universe`
- `signal`
- `target`
- `constraint`
- `execution`
- `analyzer`
- `marketRule`
- `market` 子槽位

因此，接入关系上可以表达“只替换 Alpha，不动 Input/Universe”，也可以表达“只替换 Analyzer，不动前面的 target 和 execution”。例如替换 Analyzer 时，attach 请求只需要改变 `analyzer` 槽位。

但这还不等于完整的数据依赖系统。当前缺少：

- stage 之间的显式依赖 DAG；
- 每个模块输出的缓存键；
- 上游输出复用策略；
- 下游重算范围判断；
- Analyzer 对历史订单、持仓、目标、成交流的读取契约；
- 替换 Alpha 后是否复用旧 Universe 输出的 runtime 保障。

所以目前能保证的是“接入关系不必一起改”，还不能保证“计算结果按依赖图精确增量复用”。

## 5. 结果持久化、snapshot/checkpoint 和版本复现

有接口基础，但端到端能力还没完成。

当前基础包括：

- module definition 有 `version`；
- artifact 会落到模块版本目录；
- attachment 会生成 release manifest；
- `IModule` 有 `CreateSnapshot` 和 `RestoreSnapshot`；
- `hotSwapMode` 可以表达模块替换所需运行条件。

但完整历史复现还需要补控制面和运行时记录：

- 模块版本不可变，禁止覆盖同版本 artifact；
- instance config 需要 revision；
- 每次 `attach` 生成不可变 pipeline release；
- 每次热替换前后保存模块 snapshot；
- Engine checkpoint 需要关联 pipeline release、模块版本、实例 config、输入数据版本；
- Analyzer/Result 输出要带 `runId` 和依赖图；
- 前端历史查询要能按 `runId/strategyVersion/pipelineVersion/moduleVersion/configRevision` 还原。

## 总结

当前框架已经具备前端产品的基本骨架：

- 模块仓库可列出、可新增；
- 模块实例可配置；
- 实例可接入运行中的 Engine；
- Lean 预置模块已作为默认模块库暴露；
- 内置模块和 custom 模块都走统一控制 API。

但 Stage 1 还不能宣称完整支持模块市场、依赖增量执行和历史复现。下一阶段应优先补三件事：

1. `validate-module` dry-run API，上传前后都能验证模块真实可用；
2. 不可变版本和 instance config revision，替换必须走版本化发布；
3. runtime snapshot/checkpoint registry，把每次 attach、模块版本、输入数据和结果绑定成可查询历史。
