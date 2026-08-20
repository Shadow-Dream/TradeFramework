# Module 接入契约

## 发布定义

客户端发布 Python Module 时不提交 `version`、`status` 或 `archive`；这些字段只由公共归档器生成。最小 Draft 形如：

```json
{
  "kind": "Signal",
  "moduleId": "my-signal",
  "name": "My Signal",
  "activationMode": "PythonModule",
  "parameters": {},
  "configSchema": {
    "type": "object",
    "properties": {"period": {"type": "integer", "minimum": 1}},
    "additionalProperties": false
  },
  "ports": {
    "inputs": {"close": {"schema": {"type": "number"}, "required": true}},
    "outputs": {
      "signal": {
        "schema": {
          "type": "object",
          "properties": {"value": {"type": "number"}},
          "required": ["value"],
          "additionalProperties": false
        },
        "required": true
      }
    }
  },
  "description": "Example Signal Module",
  "files": [
    {"path": "module.py", "contentBase64": "Li4u", "executable": false}
  ]
}
```

发布内容变化时 Engine 分配下一个数字 Version；内容相同则返回现有最新 Version。`{{moduleRoot}}` 只在归档后解析到该 Version 的只读执行副本。

## Python SDK

```python
from strategy_devkit.module_sdk import SignalModule


class MySignal(SignalModule):
    def update(self, close):
        return {"signal": {"value": close}}


MODULE_CLASS = MySignal
```

归档根目录必须包含固定入口 `module.py` 并导出 `MODULE_CLASS`。Pipeline Module 可继承 `UniverseModule / SignalModule / TargetModule / ConstraintModule`；独立仓库分别提供 `AnalyzerModule` 和 `EnvironmentModule`。这些类只声明 repository kind，身份、版本、配置、端口、状态和生命周期都由同一个 `Module` 基类实现。SDK 由 Engine Runtime 提供并冻结，Module 不得捆绑自己的 `strategy_devkit` 或原生动态库。

`update()` 可以使用两种统一调用形式：显式 Python 参数必须与输入端口一致，可选端口提供默认值；需要使用非 Python identifier 端口名时，实现为 `update(self, /, **inputs)` 并从统一端口映射读取。两种形式不得混用。返回对象只能包含已声明输出，且不能省略必需输出。端口承载的是 Data Dict DataKey，因此禁止 array runtime type，也禁止没有封闭 properties 或明确 additionalProperties value schema 的 opaque object。

Module `configSchema` 的根值统一为有限 exact-JSON object，并只接受 Engine/UI 能统一生成与验证的可满足 JSON Schema 子集。发布时会拒绝标量根、不可满足约束、非 exact-JSON schema/default，以及不在该子集内的组合/引用/条件关键字；PythonModule 与 ProcessRunner 因此共享同一配置值域。

Python Module 可以为自己已经计算并缓存的输出申请 Engine 本地复用证明。Module 只有在
`reusable_output_registration_available()` 为真时才可调用
`register_reusable_outputs(outputs, slot=...)` 并从 `update()` 返回该 handle；直接调用
`update()`、普通 SDK command 和 `ProcessRunner` 路径仍须返回各自拥有的普通 `dict`。
首个 handle generation 仍经过完整 SDK 端口检查以及 ModuleInvoker 的 Schema 校验和
所有权隔离；后续周期仍逐次调用 `update()`，只有同一已确认 generation 的重复 handle
可以复用该证明。Engine 只保留验证后输出的私有编码快照，并在每次命中时解码出新的
调用方所有者。新 generation、`restore`、`finalize` 和 `close` 都使旧证明失效。

## Runtime 与外部进程协议

每个后台 Backtest 在一个独立 Python Runtime 进程中执行。该 Backtest 的所有 `PythonModule` 由 Graph Runtime 直接调用，不进行逐 Module JSON 编解码或进程通信。Module 之间仍只能通过声明端口和 Graph wire 交换数据，不能互相调用。

原生库、其他语言和外部程序使用 `ProcessRunner` 归档格式，并使用 `pipeline-data-v5` JSON Lines 协议：

```json
{
  "protocolVersion": "pipeline-data-v5",
  "requestId": "instance:1",
  "command": "invoke",
  "payload": {"inputs": {"close": 123.45}}
}
```

```json
{
  "protocolVersion": "pipeline-data-v5",
  "requestId": "instance:1",
  "success": true,
  "payload": {"outputs": {"signal": {"value": 123.45}}},
  "error": ""
}
```

两种 Runtime 都支持 `initialize / invoke / finalize / snapshot / restore / close`。ProcessRunner 对 stdout 完整行、响应大小和 deadline 做限制，并持续排空 stderr；成功 `close` 后 worker 必须退出。

Engine 不发送完整 Data Dict、InstanceID 状态键或隐藏上下文。Module 只收到当前实例端口 Wiring 解析出的数据。需要网络、文件或子服务时，由该已归档 Module 自己管理并在 `on_close()` 释放。

## Graph I/O 边界

Graph Data Input/Data Output 是编辑器虚拟节点，不是 Module：

- Input Boundary 把所属资源外部 DataKey 映射成 Graph wire；
- Output Boundary 把 Graph wire 显式导出成所属资源允许的 DataKey；
- 它们不进入 Module repository、实例表、拓扑节点或生命周期。

Signal、Environment、Analysis 的普通节点都必须引用真实已归档 Module Version。Environment 和 Analysis 只有显式 Output 可以跨越 Graph 边界；`finalize()` 不能产生隐式业务输出。
