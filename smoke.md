# 科技长征策略接入 Smoke 观察

日期：2026-05-25

本次 smoke 的目的不是优化“科技长征”策略本身，而是把它当作一个带有真实缺陷和不完整表达的测试样本：让 subagent 在不修改 Engine 代码的前提下，尝试通过当前模块框架接入它，并观察框架能否暴露问题、约束边界、承接不完备逻辑。

## 约束

- 不修改 `Lean/`、Engine、Common、Algorithm 等 Engine 代码。
- 只允许新增策略侧模块、payload、文档和运行态控制面状态。
- 通过当前控制 API 的 `add-module`、`create-instance`、`attach`、`record-artifact` 完成接入。
- 主线程不接管实现，只观察 subagent 的操作和阻碍。

## Subagent 实际结果

subagent 在策略侧创建了：

- `/data/data_jyz/quat/tech_long_march_module/TechLongMarch.Modules.csproj`
- `/data/data_jyz/quat/tech_long_march_module/src/TechLongMarchSignalModule.cs`
- `/data/data_jyz/quat/tech_long_march_module/src/TechLongMarchTargetModule.cs`
- `/data/data_jyz/quat/tech_long_march_module/payloads/*.json`
- `/data/data_jyz/quat/tech_long_march_module/README.md`
- `/data/data_jyz/quat/tech_long_march_module/logs/framework_observation_report.md`

它实现并编译通过了两个模块：

- `Signal/tech-long-march-signal/20260525-001`
- `Target/tech-long-march-target/20260525-001`

并通过控制 API 完成：

- `add-module`
- `create-instance`
- `attach tech-long-march@20260525-001`
- `record-artifact` 记录策略说明、参考结果和模块源码 snapshot

完整执行记录见：

```text
/data/data_jyz/quat/tech_long_march_module/logs/framework_observation_report.md
```

## 验证状态

subagent 报告中确认：

- 没有修改 Engine 代码。
- 模块已入库。
- 实例已创建。
- attachment 已写入当前 live manifest。
- artifact 已记录到运行态控制面。

主线程没有复写或接管实现，只读取报告和文件清单。

## 顺畅的地方

当前框架的核心三段式可以工作：

- `add-module` 负责模块入库。
- `create-instance` 负责绑定模块版本和 config。
- `attach` 负责真正接入运行中的 pipeline。

内置 `json-input` 对静态 symbol 输入有帮助，不需要为简单输入再写一个 Input DLL。

同一个 DLL 可以暴露多个 entry point，本次同一个策略 DLL 同时提供了 Signal 和 Target。

新增的持久化机制也发挥了作用：模块、实例、attachment、iteration、artifact 都能落盘。

把策略拆成 `Input`、`Signal`、`Target`、`Constraint`、`Execution` 等显式组件是正向约束。很多原始策略脚本会把选股、信号、仓位、风控、执行和人工判断混在一起，短期能跑，但长期不可维护。框架要求策略作者显式拆分职责，有助于暴露逻辑缺口，也让替换、测试、复现和前端蓝图编辑更可控。

## 观察到的主要问题

### 1. 策略开发者仍然需要理解太多框架细节

subagent 为了写策略，必须理解：

- `AlphaModel`
- `PortfolioConstructionModel`
- `Insight.Weight`
- `entryPoint`
- `assemblyPath`
- `hotSwapMode`
- `ModuleConfiguration.Parameters["config"]`

这些不是“科技长征”策略本身的内容，而是接入框架和 Lean 的适配知识。当前框架还没有把策略逻辑和 Engine 适配层彻底隔离。

### 2. 缺少策略 devkit 和脚手架

subagent 需要手动创建 csproj、源码目录、payload、config schema、attach JSON 和 artifact 命令。

理想流程应该是：

```bash
strategy-devkit new tech-long-march --template weekly-state
strategy-devkit test
strategy-devkit publish --api http://127.0.0.1:8777
strategy-devkit attach --api http://127.0.0.1:8777
```

开发者应该主要编辑一个干净的策略文件，而不是手写大量接入样板。

当前已补最小脚手架：

```bash
python3 -m strategy_devkit.scaffold new \
  --name my-strategy \
  --out /path/to/my_strategy \
  --strategy-id my-strategy \
  --version 20260525-001
```

生成内容包括：

- `src/*SignalModule.cs`
- `src/*TargetModule.cs`
- `payloads/*.json`
- `README.md`
- `tests/*Modules.Tests.csproj`
- `tests/ModuleSmokeTests.cs`

每个生成模块都自带单元测试入口：

```bash
/root/.dotnet/dotnet test tests/MyStrategy.Modules.Tests.csproj \
  -c Release \
  --nologo \
  --logger "console;verbosity=minimal"
```

这个测试项目不启动 Engine，也不重编 Lean，只引用已构建的模块 SDK 二进制目录，目的是让策略开发者快速理解模块接口、运行生命周期入口，并在本地独立验证策略逻辑。

### 3. 组件拆分是正确约束，但缺少拆分辅助

本次策略接入时，subagent 需要把原始策略里的周线指标、状态机和仓位逻辑拆成 `Signal` 与 `Target`。这个要求本身是合理的：它迫使策略作者明确“什么是信号、什么是仓位构造、什么是输入、什么是风控”，避免把所有逻辑塞进一个大脚本。

真正的问题不是拆分，而是框架没有提供足够好的辅助工具。开发者需要自己判断边界、手写适配代码、手写 payload，还要理解 Lean 接口细节。devkit 应该保留这种显式组件约束，同时提供模板、检查器和本地测试，让拆分过程更顺畅。

### 4. Config API 不干净

策略模块需要手动解析 config。应该支持 typed config：

```csharp
public sealed class TechLongMarchConfig
{
    public string PriceLineMethod { get; init; } = "SMA";
    public int InsightDays { get; init; } = 35;
}
```

框架自动完成：

- JSON schema 生成
- config 校验
- config 绑定
- 默认值处理

### 5. 模块编译依赖太重

独立模块工程引用 Lean 项目后，会触发大量 Engine 相关 warning。策略开发者很难区分哪些是自己的错误，哪些是 Lean 工程噪音。

需要一个轻量 SDK：

```text
QuantConnect.ModuleSdk
```

策略工程只引用 SDK，不直接引用整个 Lean 工程。

### 6. 缺少本地 replay/test harness

subagent 能编译并接入，但没有标准命令验证策略逻辑是否和 `/data/data_jyz/quat/strategy_repro.py` 或历史结果一致。

需要类似：

```bash
strategy-devkit replay \
  --module tech-long-march \
  --data /data/data_jyz/quat/result/IWY科技长征策略11年到现在.csv \
  --expect /data/data_jyz/quat/outputs/tech_long_march/summary.md
```

这类工具应该驱动模块生命周期、喂历史数据、输出 target/insight/event，并和期望结果 diff。

### 7. 模块包 API 不自然

同一个 DLL 同时提供 Signal 和 Target，但当前需要分别 `add-module`，并重复上传同一个 DLL。

更自然的 API 是：

```bash
strategy_submit.py add-package --package TechLongMarch.Modules.dll --package-id tech-long-march --version 20260525-001
strategy_submit.py register-entry --package-id tech-long-march --kind Signal --entry-point ...
strategy_submit.py register-entry --package-id tech-long-march --kind Target --entry-point ...
```

或者一次提交 package manifest。

### 8. CLI/API 细节摩擦

subagent 报告了几个具体问题：

- `create-instance --instance <file>` 帮助说支持完整 JSON，但实际仍要求 `--instance-id/--kind/--module-id/--version`。
- `record-artifact` 的 kind 大小写需要统一。
- `list-artifacts` 输出不应该默认内联 base64 文件内容。

这些不是架构问题，但会直接破坏开发体验。当前已修复：

- `add-module --definition`、`create-instance --instance`、`attach --attachment` 支持完整 JSON 文件，不再强制要求重复字段。
- `record-artifact --kind` 支持大小写兼容，并统一保存为 `Data/Snapshot/Checkpoint/Result/Report/Log`。
- `list-artifacts` 和 `history` 默认只返回 artifact 文件摘要，不再内联 `contentBase64`。

### 9. Artifact 查询需要产品化

当前可以记录 artifact，但查询结果应默认只包含：

- kind
- artifactId
- metadata
- hash
- 文件路径
- 文件大小
- createdAt

文件内容应该通过单独下载接口读取，不能在列表里直接返回 base64。

### 10. 人工条件和外部判断缺少接口

“科技长征”策略包含“市场条件较好”“利好刺激”“牛尾”等非纯价格条件。

当前只能：

- 忽略
- 硬编码
- 另写一个模块

这些条件本身不是本次要修好的策略问题；它们的价值在于测试框架是否能清楚表达“这里存在外部判断输入”。更合理的是提供人工/外部 override 输入模块，例如：

```json
{
  "date": "2026-05-25",
  "marketCondition": "good",
  "newsBoost": true,
  "bullTail": false
}
```

然后策略模块可以把它作为结构化输入消费。这样框架既不替策略作者补全主观逻辑，也不会把缺失条件悄悄埋进硬编码。

## 优先级建议

P0：

- 做 `strategy-devkit new/test/publish/attach`。当前已补 `python3 -m strategy_devkit.scaffold new` 和 `python3 -m strategy_devkit.publish --root <project>`；publish 会 build、运行 tests、包级上传、串行建实例、attach，并记录 source artifact。
- 做轻量 `QuantConnect.ModuleSdk`。
- 修复 `create-instance --instance` 的 full JSON 提交流程。已完成。
- 修复控制面并发写状态丢失。已完成：所有 POST/DELETE 写操作通过 controlRoot 下的 `.control.lock` 串行化，避免 accepted 但状态被覆盖。

P1：

- 做 typed config 绑定和 schema 自动生成。已完成：scaffold 生成轻量 `ModuleConfig.Read<T>()`，`python3 -m strategy_devkit.schema --root <project> --write` 会从 C# `*Config` 类生成 `payloads/*.schema.json` 并回写 `payloads/package.json`；`publish` 会在上传前自动刷新 schema。
- 做本地 replay/test harness。已完成：scaffold 生成 `tests/ReplayHarness.cs` 和 `tests/fixtures/replay.json`，单测会构造 `QCAlgorithm`、`SecurityChanges`、`Slice/TradeBar`，驱动 Signal，再把 insights 交给 Target。
- 做 package-level API，支持一次上传，多 entryPoint 注册。已完成最小 `/v1/module-packages` 和 `strategy_submit.py add-package/list-packages`；scaffold 会生成 `payloads/package.json`，publish 默认使用包级注册。
- 修复 artifact kind 大小写和 `list-artifacts` 输出。已完成。

P2：

- 提供组件拆分向导和模板，例如 weekly signal + weighted target 组合模板。
- 提供人工/外部 override 输入机制，用来显式记录测试策略中的不完备外部条件。
- 提供模块日志和事件观察接口，避免策略作者关心 Engine logging 细节。

## 总结

这次 smoke 证明当前框架已经能把一个真实且不完备的策略样本接入进来，但还没有达到“框架清楚承接策略缺陷、开发者只写策略逻辑”的目标。

下一阶段不应该围绕“科技长征”本身做策略优化，而应该把这类测试样本暴露出来的问题沉淀到框架能力里：devkit、SDK、typed config、replay harness、artifact 查询和外部条件输入。框架要做的是让缺陷可见、边界清楚、接入可复现，而不是替单个策略补完逻辑。
