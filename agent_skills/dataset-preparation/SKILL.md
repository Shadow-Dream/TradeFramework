---
name: dataset-preparation
description: Acquire, clean, join, audit, and prepare reproducible Trade Engine Dataset drafts and Dataset build inputs. Use when Codex must inspect data quality or lineage, prevent point-in-time leakage, define an exact schema, adapt provider data, validate a selected application protocol, or propose a Dataset publication or build without modifying Engine internals.
---

# Dataset preparation

## Workflow

1. Define the downstream contract and exact source versions. For historical decisions or temporal joins, also define the observation, entity, as-of boundary, and decision time, then read [Data causality v1](references/data-causality-v1.md).
2. Inspect source schema, lineage, capabilities, hashes, and bounded samples. Preserve provider-native raw material and document any unavailable field or revision history.
3. Transform only in the assigned workspace. Make parsing, units, timezone, joins, missing-value policy, adjustments, and output ordering deterministic.
4. Validate the complete output: declared schema, file digests, row counts, ordering, duplicates, finite values, temporal availability, revision handling, and downstream-required fields.
5. If `trade.basic-workflow` is explicitly selected, read [Basic Workflow Dataset v1](references/basic-workflow-dataset-v1.md) and run its full conformance checks. Otherwise do not impose bar, price, or trading fields.
6. Return an Analysis Brief and one display-only Proposal describing the exact Recipe/Workspace inputs, lineage, output schema, validation result, and digest. The current Proposal contract carries no Dataset payload. Let the Engine validate and commit the immutable Dataset Version and evidence through the normal UI after user confirmation.

## Guardrails

- For time-dependent data, treat event time, publication/availability time, revision vintage, and ingestion time as different concepts.
- Join a fact into a historical decision only when it was available by that decision time. Do not repair missing history with a later revision.
- Do not infer compatibility from column names, Dataset IDs, BuiltIn labels, or protocol metadata alone.
- Never write managed Dataset roots, read Engine control state, assign content identity, or submit a build through a private route.

## Analysis Brief

Use the user's language and exactly these four sections:

1. **已确认事实** — attach an exact Dataset/source reference and as-of time to each fact.
2. **确定性计算** — state the method and input digests for counts, profiles, joins, and checks.
3. **解释、反证与不确定性** — distinguish an observed defect from a possible business explanation.
4. **单一下一实验或 Proposal** — propose one minimal preparation or publication step; do not silently execute it.
