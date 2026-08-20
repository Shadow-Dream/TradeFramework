# Golden Cross Signal

This is the smallest stateful Python Signal that follows the TradeEngine
Module SDK contract. The Engine resolves one explicit DataKey into the
`close` input port. The module never receives the Dataset, Environment, full
cycle dictionary, or a transport context.

Configuration:

- `fastPeriod`: positive moving-average window (default `20`)
- `slowPeriod`: moving-average window greater than `fastPeriod` (default `50`)

The module emits both auditable intermediate values and the target position.
During warm-up, `target_position` is null. Afterwards it is `1.0` when the
fast average is above the slow average and `0.0` otherwise.
