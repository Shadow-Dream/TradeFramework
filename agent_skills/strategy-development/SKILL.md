---
name: strategy-development
description: Design, implement, test, or revise Trade Engine Module implementations and Pipeline, Environment, or Analysis Graph drafts. Use when Codex must develop strategy logic, bind ports and DataKeys, diagnose draft validation errors, prepare a readable resource diff, or propose publishing an exact strategy resource without changing Engine internals.
---

# Strategy development

## Workflow

1. Read [Engine contracts v1](references/engine-contracts-v1.md). Identify the exact resource, repository kind, current immutable version, public ports, DataKeys, JSON Schemas, and lifecycle contract.
2. State one intended observable change and the resources that must remain fixed. Treat an unclear business rule as a hypothesis, not an implicit Engine requirement.
3. Edit only an Agent workspace draft. Import the public SDK, keep reusable calculations separate from the adapter, bind every dependency through declared ports, and emit only declared outputs.
4. Run syntax and focused unit tests, then use the authoritative public validation tool. Preserve structured diagnostics; do not bypass a failed contract or infer validity from local tests.
5. Compare the validated draft with the exact base version. Include behavior, contracts, configuration, files, and content digest in the readable diff.
6. Return an Analysis Brief and, when a change is ready, one display-only Proposal summarizing the native draft or backtest request, exact references, validation result, readable diff, and digest. The current Proposal contract cannot carry a draft or execute an action; keep the actual files in the assigned workspace and do not publish or run them merely because they validate.

If the user explicitly selects `trade.basic-workflow`, also read [Basic Workflow Protocol v1](references/basic-workflow-protocol-v1.md). Never impose that profile on a generic resource.

## Guardrails

- Treat Pipeline, Environment, and Analysis as independent resources combined only by an exact Backtest composition.
- Keep `builtin` as ownership metadata; use the same public Module, archive, validation, and runtime contracts as any other implementation.
- Do not import Engine internals, inspect managed archives or control state, add a hidden execution path, or hard-code a strategy ID, Module ID, DataKey, date, asset, or protocol into generic infrastructure.
- Stop with the missing public contract when the available tools cannot read or validate required material.

## Analysis Brief

Use the user's language and exactly these four sections:

1. **已确认事实** — attach an exact Engine reference or external source and as-of time to each fact.
2. **确定性计算** — state the method and input digests.
3. **解释、反证与不确定性** — separate inference from evidence and name a falsifier.
4. **单一下一实验或 Proposal** — propose one minimal discriminating change; do not silently execute it.
