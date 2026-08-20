# Result interpretation

- Knowledge ID: `trade.result-interpretation`
- Knowledge version: `1.0.0`
- Status: active analytical guidance
- Applies to: immutable Trade Engine Backtest Results
- Authority: the exact sealed Result, frozen composition, and declared metric contracts override this guide

## Establish comparability

Before interpreting a metric, pin Backtest ID, Result digest, frozen resource identities, Engine build, Python runtime, Dataset content, Sampler, time range, units, currency, benchmark, costs, and metric definition. “Same strategy” is not evidence that two runs are comparable.

For an A/B attribution, create an exact frozen-input diff. If multiple relevant resources, parameters, code bundles, data versions, or runtime identities change, label the result confounded and propose a one-change experiment.

## Read bounded evidence

Inspect Result schema before values. Query only required DataKeys and bounded cycle ranges; retain query parameters, total/returned counts, and content digest. Do not assume an absent DataKey is zero, unchanged, or inaccessible for a business reason.

Trace an observed outcome backward through declared data flow:

```text
Result field -> producing Analysis/Module output -> declared input DataKeys
             -> upstream Module/Graph boundary -> Sample or prior-cycle field
```

Use exact cycle and decision times where declared. Do not assume that an intent-like final-cycle field was executed; prove its effect from the selected Environment, Pipeline, and Dataset cycle contracts.

## Calculation checks

- Recompute relevant totals and selected cycle windows from the exact projected fields. For trading contracts, this may include return legs, drawdown, turnover, exposure, and fees.
- State denominator, compounding, annualization interval, missing-cycle handling, and rounding.
- Split gross performance, transaction costs, exposure/timing, and residual differences when the available contract supports it.
- Treat a metric emitted by Analysis as a declared computation, not proof that its economic interpretation is correct.

## Competing explanations

Challenge surprising performance with point-in-time leakage, revised data, execution timing, unexecuted terminal intent, survivorship, warmup, fee/slippage, unit mismatch, benchmark mismatch, small samples, outliers, and multiple simultaneous changes. A useful challenge identifies evidence that would discriminate alternatives; a generic bull/bear debate does not.

## Recommendation boundary

Facts and deterministic calculations may be reported directly. A strategy explanation remains a hypothesis until an isolated exact experiment supports it. Recommend one experiment with a predicted observable and falsification condition; do not convert a backtest narrative into an automatic live-trading action.
