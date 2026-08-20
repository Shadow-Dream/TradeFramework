---
name: backtest-investigation
description: Diagnose failed, suspicious, or underperforming Trade Engine Backtests and compare exact immutable Results. Use when Codex must explain validation or runtime diagnostics, investigate an anomalous cycle or metric, attribute differences between runs, check reproducibility, or propose one controlled follow-up experiment without changing Engine execution semantics.
---

# Backtest investigation

## Workflow

1. Read [Result interpretation v1](references/result-interpretation-v1.md). Resolve the exact Backtest IDs, terminal states, Engine/Python runtime identities, frozen Pipeline, Environment, Analysis, Dataset Version, Sampler Version, snapshot hash, and Result content digest.
2. Choose one mode:
   - **Diagnose:** localize a validation, execution, or data-flow failure from structured diagnostics and the exact contracts.
   - **Compare:** first prove which immutable inputs differ; attribute only a single isolated change.
   - **Investigate:** query bounded Result paths and cycle ranges around a stated anomaly.
3. Separate Engine-reported facts and deterministic calculations from hypotheses. Never infer values hidden by Result projection.
4. Challenge a material conclusion once using alternatives supported by the selected contracts. Common trading checks include leakage, fee/slippage, exposure, denominator, warmup, sample selection, timestamp, benchmark, and multiple simultaneous changes.
5. Return an Analysis Brief with one smallest discriminating next experiment. If requested, prepare one exact Backtest Proposal; do not submit or retry it silently.

## Query discipline

- Start from Result schema/metadata, then request only required DataKeys and bounded ranges. Keep query parameters and returned digests with calculations.
- Compare like-for-like periods, currencies, units, annualization conventions, and missing-cycle policies.
- Mark a comparison as confounded when more than one relevant frozen input differs. Report contribution or correlation only when the calculation supports it; do not rename it causation.
- Preserve the first authoritative failure. Do not catch it and try a different resource, current version, or execution path.

## Analysis Brief

Use the user's language and exactly these four sections:

1. **已确认事实** — attach exact Backtest/Result references and as-of time.
2. **确定性计算** — state method, query range, and input digests.
3. **解释、反证与不确定性** — label inference, competing explanations, and falsifiers.
4. **单一下一实验或 Proposal** — hold all unrelated exact resources fixed; do not silently execute it.
