---
name: research-verification
description: Verify external financial, market, company, news, or dataset evidence that materially affects a Trade Engine research hypothesis. Use when Codex must research a claim, distinguish facts from inference, establish point-in-time availability, reconcile conflicting sources, challenge a strategy thesis, or turn external evidence into one testable backtest experiment rather than an unsupported BUY/SELL recommendation.
---

# Research verification

## Workflow

1. State the claim, instrument/universe, decision horizon, jurisdiction, units, and historical as-of cutoff. Define what would falsify the claim.
2. Read [Evidence verification v1](references/evidence-verification-v1.md). Search current primary sources first; use secondary sources to find or contextualize them, not to replace available originals.
3. Record each material source's URL or immutable identifier, publisher, publication time, effective/event time, historical availability, applicable period, and retrieval as-of time.
4. Extract facts and deterministic calculations separately. Report conflicts, missing vintages, stale data, licensing limits, and assumptions instead of filling gaps from memory.
5. Run one adversarial pass only when the conclusion is material, surprising, source-conflicted, or intended to justify an expensive experiment. Seek an alternative explanation or disconfirming source, not a role-play debate.
6. Return an Analysis Brief with one falsifiable next experiment or Proposal. External research may motivate a strategy draft; it cannot validate an Engine contract or authorize execution.

## Guardrails

- Prefer filings, regulators, exchanges, official statistics, issuer investor relations, and provider methodology documents. Cite close to every material claim.
- Separate publication time from event/effective time and from when the source was actually knowable to the simulated strategy.
- Do not copy secrets or licensed bulk data into output, fabricate precise values, count repeated reporting as independent corroboration, or turn retrieved prose into automatic trading action.
- Treat confidence as evidence quality and unresolved uncertainty, never as the number of Agents or debate rounds.

## Analysis Brief

Use the user's language and exactly these four sections:

1. **已确认事实** — attach a primary source and as-of time to each fact.
2. **确定性计算** — state method, inputs, units, and source versions/digests.
3. **解释、反证与不确定性** — separate thesis, counterevidence, conflicts, and falsifiers.
4. **单一下一实验或 Proposal** — translate the claim into one observable test; do not silently execute it.
