# Basic Workflow Protocol: Dataset view

- Knowledge ID: `trade.basic-workflow`
- Knowledge version: `1.0.0`
- Protocol version: `1.0.0`
- Profile: `single-instrument-bar-position`
- Status: implemented in the application protocol package; confirm that the exact BuiltIn registry entries are installed before using them
- Authority: the approved application-layer protocol specification; Engine Dataset contracts remain publication authority

Apply this profile only when explicitly selected. Protocol conformance is an application check and must not introduce special Engine behavior.

## Record contract

Use the existing records envelope with continuous `sequence`, absolute `eventTime`, absolute `availableAt`, and a `values` object. Declare every emitted value column in the exact Dataset schema.

Required `values` fields are:

| Field | Type | Rule |
|---|---|---|
| `instrumentId` | string | Equals the selected profile descriptor |
| `open`, `high`, `low`, `close` | finite number | Positive and OHLC-consistent |
| `volume` | finite number | Non-negative |
| `referencePrice` | finite number | Positive current valuation price |
| `executionPrice` | finite number | Positive basis for the prior target |
| `executionTime` | absolute timestamp | Time of that executable observation |
| `complete` | boolean | `true` for v1 standard backtests |

Optional standard fields are `vwap`, `bid`, `ask`, and `tradeCount`. Private extensions use `ext.<namespace>.<field>` and remain explicitly declared; there is no wildcard schema.

## Full conformance

- Require sequence from zero without gaps, strictly increasing event time, and non-decreasing available time.
- Require `availableAt >= eventTime`.
- For cycle `t > 0`, require `executionTime(t) >= availableAt(t-1)` so the prior target exists before execution.
- Require `executionTime(t) <= availableAt(t)` so the current execution observation is knowable.
- Require finite numbers, `low <= min(open, close) <= max(open, close) <= high`, stable instrument/unit descriptors, and exact schema coverage.
- Confirm the selected Sampler maps the exact fields to declared `market.*` outputs. Metadata alone is insufficient.
