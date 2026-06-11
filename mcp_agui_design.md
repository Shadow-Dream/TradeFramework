# Engine MCP and AG-UI Design

Date: 2026-05-26

## Scope

This design adds native MCP and AG-UI adapters around the existing control plane. It does not make strategy authors call Engine internals, and it does not turn MCP or AG-UI into the source of truth. The source of truth remains the persisted module repository, instance repository, attachment snapshots, iterations, artifacts, and live pipeline manifest.

## Protocol Facts Used

- MCP is a JSON-RPC 2.0 protocol with hosts, clients, and servers, and supports capability negotiation over stateful connections. It exposes server features including tools, resources, and prompts. Source: https://modelcontextprotocol.io/specification/2025-06-18
- MCP tools are listed through `tools/list` and invoked through `tools/call`; tool definitions include `name`, `description`, `inputSchema`, and optional `outputSchema`. Source: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- AG-UI is a streaming event protocol between agents and frontends. It uses lifecycle events, tool-call events, state snapshot/delta events, activity events, and custom/raw events. Source: https://docs.ag-ui.com/concepts/events
- AG-UI tools use JSON Schema parameters and are frontend-defined, which is useful for human-in-the-loop approval and UI-controlled actions. Source: https://docs.ag-ui.com/concepts/tools

## Architecture

```text
Frontend / Agent Host
  |-- AG-UI stream: /agui/runs
  |-- MCP client: stdio or HTTP transport
        |
        v
Engine Interaction Gateway
  |-- McpAdapter
  |-- AgUiAdapter
  |-- ControlPlaneClient
  |-- EventProjector
  |-- Auth/Policy
        |
        v
Existing Control Plane
  |-- /v1/module-packages
  |-- /v1/modules
  |-- /v1/pipeline/instances
  |-- /v1/pipeline/attach
  |-- /v1/artifacts
  |-- /v1/history
        |
        v
Persisted State
  |-- control/*.json, events.jsonl, iterations.json
  |-- releases/_packages
  |-- releases/_attachments
  |-- releases/_artifacts
  |-- live/pipeline.json
```

The gateway is an adapter process. It may be hosted beside `strategy_submit_api.py` first, then moved into Engine only after the interfaces stabilize. The Engine should keep consuming live pipeline manifests and release package paths; MCP/AG-UI should not directly mutate Engine memory.

## MCP Server Design

### Tools

Expose mutating operations as MCP tools with strict schemas and policy checks:

- `module_package_add`: upload package and register module entry points.
- `module_instance_save`: create or update a configured instance.
- `pipeline_attach`: attach instances to the active pipeline.
- `pipeline_detach`: detach stages, instances, or market slots.
- `artifact_record`: persist snapshot/checkpoint/result/report/log.
- `devkit_validate`: validate a strategy-side project without publishing.
- `devkit_publish`: build/test/schema/package/attach/artifact as one operation.
- `replay_run`: run local replay against a strategy module project.

Every mutating tool returns structured content:

```json
{
  "accepted": true,
  "operationId": "publish:tech-long-march:20260526-001",
  "iterationId": "...",
  "artifactKeys": [],
  "warnings": []
}
```

For failures, use MCP tool execution errors for business failures and JSON-RPC protocol errors only for invalid tool names, invalid request shapes, or server failures.

### Resources

Expose read-only resources so an agent can inspect state without calling mutating tools:

- `trade://current/manifest`
- `trade://modules`
- `trade://packages`
- `trade://instances`
- `trade://attachment/current`
- `trade://iterations`
- `trade://artifacts`
- `trade://artifacts/{kind}/{artifactId}/manifest`
- `trade://alpha-graph/current`

Resource content must be summary-first. Large file contents require explicit artifact download tools or paginated resource reads.

### Prompts

Add MCP prompts for repeatable workflows:

- `new_strategy_module`: scaffold a strategy module with inputs, signal, target, tests, and payloads.
- `review_module_project`: inspect schema/replay/publish readiness.
- `publish_strategy_project`: produce a step plan and require approval before publish.
- `debug_replay_failure`: summarize test failure and likely fixture/module issue.

### Authorization

MCP tools are model-invoked, so mutating tools must be policy-gated:

- Read-only resources: default allow for local trusted clients.
- Build/test/replay: allow in project sandbox.
- Package upload/instance save: require user or service approval in cloud mode.
- Attach/detach/live market changes: require explicit approval and audit event.
- Trading/execution-affecting market changes: require stronger role and optional flat/no-open-orders precheck.

## AG-UI Design

AG-UI is the frontend event layer. It should not replace the control API; it streams progress and state to UI.

### Run Types

- `strategy.scaffold`
- `strategy.validate`
- `strategy.test`
- `strategy.publish`
- `pipeline.attach`
- `artifact.inspect`
- `alphaGraph.edit`

### Event Mapping

For a publish run:

```text
RunStarted
StateSnapshot      current project, package, instances, attachment
StepStarted        schema
ActivityDelta      generated schemas
StepFinished
StepStarted        build
ActivityDelta      dotnet output summary
StepFinished
StepStarted        test
ActivityDelta      test pass/fail counts
StepFinished
StepStarted        package
StateDelta         package repository changed
StepFinished
StepStarted        createInstances
StateDelta         instance repository changed
StepFinished
StepStarted        attach
StateDelta         live manifest / current attachment changed
StepFinished
StepStarted        artifact
StateDelta         artifact repository changed
RunFinished
```

For errors:

```text
RunError {
  "code": "VERSION_EXISTS",
  "message": "Package version already exists...",
  "recovery": {
    "action": "bumpVersion",
    "files": ["payloads/attach.json", "payloads/package.json", "payloads/*.instance.json"]
  }
}
```

### State Model

AG-UI `StateSnapshot` should use this top-level shape:

```json
{
  "control": {
    "currentAttachment": {},
    "currentManifestHash": "",
    "iterations": [],
    "artifacts": {}
  },
  "repositories": {
    "packages": {},
    "modules": {},
    "instances": {}
  },
  "project": {
    "root": "",
    "schemas": {},
    "tests": {},
    "validation": {}
  }
}
```

Use JSON Patch `StateDelta` for incremental updates after each step.

### Frontend Tools

Use AG-UI frontend-defined tools for human-in-the-loop actions:

- `confirmPublish`
- `confirmAttachLive`
- `chooseVersionBump`
- `editInstanceConfig`
- `selectArtifactFiles`
- `resolveValidationIssue`

This keeps sensitive actions in the UI and avoids allowing an agent to silently mutate live Engine state.

## Control API Changes Needed

Short term:

- Add `/v1/validate/project` or keep validation in `strategy_devkit.publish` and expose it through MCP.
- Add `/v1/artifacts/{kind}/{artifactId}/files/{path}` download endpoint.
- Add `/v1/events/stream` as Server-Sent Events or newline-delimited JSON for AG-UI projection.
- Add error codes to control API responses, e.g. `VERSION_EXISTS`, `UNKNOWN_INSTANCE`, `SCHEMA_INVALID`, `PACKAGE_INVALID`.

Medium term:

- Add transaction boundaries for publish:
  - package accepted
  - instances saved
  - attach committed
  - artifact recorded
- Add rollback or resumable publish when package succeeds but attach/artifact fails.
- Add version bump helper:
  - input: current package/attachment
  - output: updated payload patch

Long term:

- Move from file-backed control state to a transactional store, while preserving the same API semantics.
- Add signed package manifests and module provenance.
- Add remote package fetch with checksums and policy evaluation.

## Engine Integration Boundary

The Engine should observe only:

- live manifest changes,
- package artifact paths,
- active attachment snapshots,
- module hot-swap requirements.

MCP and AG-UI should observe and mutate the control plane, not individual Engine runtime objects. This preserves strong decoupling and supports cloud development where module code is authored away from the Engine machine.

## Implementation Phases

1. `mcp_gateway.py`: read-only resources and safe tools over current control API.
2. `agui_gateway.py`: event stream for `devkit.publish` and control API history.
3. Error code standardization in `strategy_submit_api.py`.
4. Frontend-compatible schema and AG-UI state snapshots.
5. Human approval tools for attach/live changes.
6. Native Engine observer for live manifest changes if current polling/hot-swap is insufficient.
