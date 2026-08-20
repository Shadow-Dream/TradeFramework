# Data causality

- Knowledge ID: `trade.data-causality`
- Knowledge version: `1.0.0`
- Status: active analytical guidance
- Applies to: Dataset preparation and point-in-time backtesting
- Authority: exact provider documentation and Dataset evidence override general heuristics

## Time model

- `event time`: when the measured event occurred.
- `available time`: earliest time the strategy could have known that exact value.
- `revision time/vintage`: when a replacement value became available.
- `ingestion time`: when this system fetched it; useful provenance, not proof of historical availability.
- `decision time`: cutoff for one simulated decision.

For every join, require the joined value's available time to be no later than decision time. When a source revises history, select the vintage available at decision time; never backfill the latest value into prior decisions.

## Leakage checks

- Reject labels, aggregates, normalization parameters, constituents, fundamentals, news, or corporate actions whose availability crosses the decision boundary.
- Distinguish announcement, effective, filing, exchange dissemination, and vendor ingestion timestamps.
- Fit transforms and universe selection on the permitted training window only.
- Preserve delisted entities and historical index membership when the research question requires them.
- Ensure an execution observation occurs after its creating intent and is actually knowable at the simulated execution decision.

## Determinism and lineage

Record provider, retrieval boundary, source artifact hash, parser/version, timezone, units, adjustment policy, join keys, sort keys, duplicate policy, missing-value policy, and output hashes. Preserve raw inputs separately from derived fields. A rerun over the same exact inputs and code should produce the same ordered records and digest.

## Quality evidence

Report counts and examples for missing, duplicate, out-of-order, non-finite, impossible-range, schema-conflicting, late, and revised records. Do not silently coerce a quality defect into a plausible value. Label unresolved provider conflicts instead of choosing by convenience.
