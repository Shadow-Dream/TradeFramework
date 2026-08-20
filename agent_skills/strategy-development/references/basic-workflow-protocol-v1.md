# Basic Workflow Protocol: strategy view

- Knowledge ID: `trade.basic-workflow`
- Knowledge version: `1.0.0`
- Protocol version: `1.0.0`
- Profile: `single-instrument-bar-position`
- Status: implemented in the application protocol package; confirm that the exact BuiltIn registry entries are installed before using them
- Authority: the approved application-layer protocol specification; Engine contracts remain execution authority

Read this reference only when the user or selected resources explicitly choose this protocol and profile.

## Boundary

The protocol is an application-layer convention. It must not add Engine stages, Module kinds, hidden inputs, runtime branches, or protocol-aware validation. A protocol declaration is discovery evidence only; prove compatibility again from the exact Dataset schema, Sampler output schema, Module ports, DataKeys, Graph wiring, and frozen composition.

## Cycle

```text
Dataset record(t) -> Sampler(t) -> Environment executes approved intent(t-1)
                  -> Observation -> Pipeline observationInput projection
                  -> Pipeline produces approved intent(t) -> Analysis observes cycle(t)
```

- A current target cannot execute at the same cycle's execution price.
- `intent.approved.position = null` means no new intent; `0` means target flat.
- Environment reads only declared prior fields such as `last.intent.approved` and `last.portfolio.account`.
- The final approved intent remains unexecuted unless a later Dataset record exists.

## Data roles

- Sampler owns `market.instrument`, `market.bar`, and `market.price`.
- Environment explicitly exports required market fields and owns `portfolio.account` and `execution.order`; those outputs form Observation.
- Pipeline selects market/account/order paths through `config.observationInput`, then produces `intent.approved`; Constraint completion is the Pipeline boundary.
- Analysis reads declared completed Pipeline fields with `currentPipeline` where required and emits `analysis.*` only to Result.

Do not select Modules by hard-coded IDs. Match protocol metadata, role, exact schemas, ports, and DataKeys; then call the ordinary Engine validator.
