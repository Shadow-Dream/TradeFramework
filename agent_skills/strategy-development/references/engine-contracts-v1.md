# Trade Engine contracts

- Knowledge ID: `trade.engine-contracts`
- Knowledge version: `1.0.0`
- Status: active operational summary
- Applies to: Engine architecture contract version 1
- Authority: the Engine public contracts and the user's active architecture/design specifications; this summary does not override them

## Resource boundaries

- Pipeline, Environment, Analysis, Dataset, Sampler, Backtest, and Result are independent boundaries.
- Pipeline owns fixed stages and its Signal Graph. Environment and Analysis each own an independent Graph.
- Graph nodes are ordinary peer Module instances. Graph input/output nodes are virtual wiring boundaries, not Modules or containers.
- A Backtest explicitly selects exact Pipeline, Environment, Analysis, Dataset Version, and Sampler Version identities. Do not select “latest” at execution time.

## Module contract

- All Module implementations use one public base contract, declared input/output ports, recursive JSON Schemas, configuration schema, and lifecycle.
- The lifecycle is `initialize -> invoke* -> finalize -> close`, with snapshot/restore governed by the same public SDK.
- A Module receives only values bound to declared ports and may return only declared outputs. It never receives a hidden complete Data Dict or role-specific context.
- BuiltIn is ownership metadata, not a Module kind, activation mode, validation shortcut, or runtime path.

## Graph and cycle semantics

- Compile exact Module versions, ports, schemas, edges, I/O boundaries, and stable topology before execution.
- Reject unknown or orphan nodes, missing required inputs, incompatible schemas, cycles, and invalid Pipeline stage placement.
- Sampler emits the current-cycle Sample. Environment reads declared Sample and `last.<DataKey>` inputs; explicit Environment outputs form an Observation. Pipeline applies `config.observationInput` to project its initial Data Dict.
- Pipeline produces the completed current-cycle Data Dict. Analysis runs once afterward. An Analysis input that needs completed current-cycle data must explicitly select `currentPipeline`; Analysis outputs enter Result only.
- `last` is a path prefix. Never bind or expose the complete `last` object.

## Identity and authority

- The client submits a draft without assigning version, status, archive path, frozen snapshot, or BuiltIn ownership.
- The Engine validates and creates immutable versions, content evidence, and the frozen Backtest composition.
- Runtime consumes the frozen composition and must not recover missing material from a current index or recompile an alternative plan.
- Local tests establish draft confidence. Only public Engine validation establishes compatibility with an exact Engine contract.

## Agent boundary

- Read exact resources through public tools. Work on drafts in the assigned workspace.
- Return proposed changes for user confirmation. Do not read Engine source, managed roots, databases, credentials, or private localhost routes to compensate for a missing tool.
