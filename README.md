# TradeEngine

TradeEngine 是一个单运行时、Data Dict 驱动的策略研究与回测平台。控制面负责版本归档和图编译，所有 Module 最终由同一个 Python Runtime 执行；不存在 Lean、RemoteService 或第二执行后端。

## 目录结构

```text
engine/             Engine 生产代码，按 core / contracts / archive / control /
                    repository / authority / compiler / runtime / composition /
                    worker / jobs / service 分层
agent_web/          Kanna Agent Web fork、Session/Event Store 与 Claude/Codex 适配器
trade_agent_bridge/ Context handoff、Turn grant 与 Engine Tool 安全投影
trade_agent_tools/  Claude/Codex 共用的只读/验证/建议 stdio MCP
strategy_devkit/    策略与 Module SDK；CLI 使用 `python -m strategy_devkit`
tests/              按 Engine 领域组织的单元与集成测试
web_src/            Web 源码；web/ 为浏览器运行资源
vendor/             独立版本的外部项目；Engine 禁止 import
artifacts/          本地归档产物（不属于源码）
benchmarks/         本地性能证据与基准产物（不属于源码）
```

根目录仍存在的 Python 生产文件属于正在迁移的旧边界；不得继续向其中增加新职责。
目标依赖与拆分验收规则见 [Architecture contract](docs/architecture.md)。

以下体积较大的目录是本机状态，不是源码层级：`.runtime/` 保存控制库、归档与回测
结果，`artifacts/remote-imports/` 保存显式导入的外部源码快照，`node_modules/` 是
前端依赖。它们均被 Git 忽略，也不得被 Engine 生产代码当作隐式 fallback；清理时
应使用对应的资源/依赖流程，不能把运行证据与源码目录混在一起移动。

策略源码、策略专属协议、数据与结果位于独立私有仓库，不属于 TradeEngine Git
历史。Agent Web 只从服务端 `TRADE_AGENT_PROJECTS_JSON` 明确批准的外部 Project
目录加载策略；TradeEngine 不扫描仓库内目录，也不接收浏览器提供的文件系统路径。

## 核心资源

- Pipeline：用自身 `config.observationInput` 从 Environment Observation 投影初始 Data Dict，再依次执行 `Universe / Signal Graph / Target / Constraint`；Constraint 完成后的 Data Dict 就是 Pipeline 输出。
- Environment：独立的 Environment Graph，把本周期 Sample 与上一周期 Pipeline 状态转换为 Observation；只有 Pipeline 自身的 `config.observationInput` 投影结果才成为初始 Data Dict，裸 Sample 不会绕过这两个边界。
- Analysis：独立的 Analysis Graph；普通 Input 读取本周期 Sample 与上一周期 `last.*`，需要观察本周期完成状态的 Input 显式选择 `currentPipeline`，输出只写入 Result。
- Dataset：只读、内容校验的数据容器版本。
- Sampler：把指定 Dataset Version 转换为每周期 Sample。
- Backtest：显式选择并固定 Pipeline、Environment、Analysis、Dataset、Dataset Version、Sampler Version 后执行；页面不会自动补全标准资源。

Pipeline Module、Environment Module、Analysis Module 分别存放在三个仓库中，但都继承同一公共 `Module` 基类并使用同一归档与运行协议。Graph 是同级 Module 实例的可视化编辑和拓扑编译视图，不是 Container Module；Graph 的 Data Input/Data Output 只是虚拟边界节点，不是 Module。

## 周期闭环

1. Sampler 生成本周期裸 Sample 字段。
2. Engine 只把 Graph 显式绑定的上一周期 Pipeline 字段映射为 `last.<DataKey>`；完整 `last` 根对象不可绑定。
3. Environment 从本周期 Sample 与上一周期 `last.*` 执行；只有其显式 Graph Output 组成 Observation，未导出的 Sample 字段在此边界终止。
4. Pipeline 先按已编译的 `config.observationInput` 从 Observation 投影初始 Data Dict，再按固定拓扑执行，得到本周期完成的 Pipeline Data Dict。
5. Analysis 每周期只执行一次：普通 Input 保留 `Sample + last.*` 语义，声明 `source: "currentPipeline"` 的 Input 读取第 4 步的同周期完成数据；两类输入可以在同一 Graph 中并存。
6. Pipeline 完成数据按显式依赖投影为下一周期 `last.*`；Analysis 的显式 Graph Output 只写入 Result，不进入 Pipeline 或下一周期状态。

## Module 与版本

所有 Module 使用同一端口、递归 JSON Schema、配置和生命周期：

```text
initialize -> invoke* -> finalize -> close
                  snapshot / restore
```

标准 Python Module 使用 `PythonModule` 归档格式：归档根目录的 `module.py` 导出继承公共基类的 `MODULE_CLASS`，每个 Backtest Runtime 在自己的单一 Python 进程内直接调用所有 Python Module。Engine 自带逻辑只以 `builtin=true` 标识所有权，并经过相同的归档、SDK、端口和生命周期。原生库、其他语言及外部程序使用 `ProcessRunner`，通过独立进程协议隔离；运行方式由归档格式确定，不是策略性能开关。

所有数字版本由 Engine 以无前导零的规范十进制字符串（`1`、`2`、……）单调分配，
并与规范绝对归档路径保持一一对应；索引不接受相对路径、`.`、`..` 或符号链接别名。
相同内容不会重复升版，只有完整归档、只读且通过 manifest verification 的版本可以
进入 Runtime 或 Backtest。Sampler Version 同时归档自己的 runtime manifest 与
worker/SDK/映射实现；Dataset Script 必须先发布为 Recipe Version，不能从原始文本或
Workspace 路径直接执行。Backtest snapshot 还会冻结 Engine build 和 Python runtime 身份。

## 启动与验证

```bash
python3 engine_service.py \
  --host 127.0.0.1 \
  --port 30808 \
  --config .runtime/strategy-control.json \
  --public-url https://trade.duckduckrun.com

python3 -m unittest discover -p 'test_*.py'
node --check web/app.js
node --check web/module_graph_litegraph.js

# Agent Web 用户态构建与热更新
scripts/build_agent_web.sh
scripts/reload_agent_web.sh
```

详细接口见 [Engine Web API](docs/engine_web.md)，Module 接入见
[Module contracts](docs/module_contracts.md)，Agent 设计见
[Agent application design](docs/agent_application_design.md)，实施与验收见
[Kanna integration plan](docs/kanna_agent_integration_plan.md)，设计不变量见
[Engine guideline](docs/guideline.md)。
