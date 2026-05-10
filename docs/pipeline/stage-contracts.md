# Trade Pipeline — Stage Contracts

> **Status:** Canonical (pipeline scope). Adopted in **S-AI-WS2**
> (2026-05-10) per [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md).
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
> (AI-scope canonical) and
> [`docs/ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md)
> (system-wide canonical). When this doc and either canonical doc
> overlap, the canonical doc wins. Stage names defined here are the
> locked WS2 names; types live in
> [`src/pipeline/types.py`](../../src/pipeline/types.py).

## Purpose

Document per-stage I/O, owners, deterministic-vs-model classification,
and logging requirements for the canonical trade pipeline.

## Safety invariant

**Stages 6, 7, and 8 must remain rejection-capable for any upstream
output regardless of source.** A `StageDecision(verdict=VETO,
rejection_source=DETERMINISTIC)` raised at any of these stages is
immutable: no upstream allow, however confident, may override it.

A `StageDecision(verdict=VETO, rejection_source=MODEL)` is advisory.
Whether it actually blocks the candidate depends on the deployment
tier of the issuing model (see WS7 in
[`docs/AI-TRADERS-ROADMAP.md`](AI-TRADERS-ROADMAP.md)). In `shadow`
mode, model VETOs are logged but not enforced; in `advisory` /
`live-approved` modes, they are enforced according to the registry
policy.

## Stage map (10 stages)

Numbering matches
[`StageName`](../../src/pipeline/types.py) (`INGEST=1` … `REVIEW=10`).
The 8-step pipeline in `ARCHITECTURE-CANONICAL.md` maps onto this
list as: step 1 = stages 1–2, step 2 = stages 3–4, step 3 =
(audit log only), step 4 = stage 6, step 5 = stage 7, step 6 = stage
8, step 7 = stage 9, step 8 = (operator visibility, cross-cuts).

| # | Stage | Class | Input | Output | Owner files (today) | Owner files (target) |
|---|---|---|---|---|---|---|
| 1 | `INGEST` | Deterministic | external (exchange API, feeds) | raw candle / tick / account snapshot | `src/exchange/`, `src/runtime/market_data.py` | unchanged |
| 2 | `NORMALIZE` | Deterministic | raw market data | normalized internal candle / tick state | `src/runtime/market_data.py` | unchanged |
| 3 | `CONTEXT` | Deterministic + model-assisted (later) | normalized data + account state | feature / context bundle | `src/units/accounts/`, future `ml/features/` | future `ml/features/` produces engineered features |
| 4 | `SETUP` | Deterministic today; optional model-assist later | feature / context bundle | zero or more `TradeCandidate` (without model_scores) | `src/units/strategies/`, `src/ict_detection/` | unchanged + setup quality scorer hook |
| 5 | `SCORE` | Model-assisted | `TradeCandidate` | `TradeCandidate` enriched with `model_scores` and a `StageDecision(SCORE_ONLY)` | future model layer + `src/core/coordinator.py` | unchanged |
| 6 | `RISK` | **Deterministic only** | `TradeCandidate` | `StageDecision(ALLOW \| VETO)`; on ALLOW, candidate proceeds | `src/units/accounts/risk.py`, `prop_risk.py`, `src/runtime/risk_counters.py` | unchanged |
| 7 | `PACKAGE` | Deterministic | `TradeCandidate` (post-risk) | `ExecutionIntent` or `StageDecision(VETO, DETERMINISTIC)` | `src/runtime/orders.py`, `src/runtime/validation.py` | unchanged |
| 8 | `ROUTE` | Deterministic | `ExecutionIntent` | broker-side order id (live) or simulated id (dry) | `src/units/accounts/execute.py`, `src/exchange/` | unchanged |
| 9 | `CAPTURE` | Deterministic ingest; model-assisted enrichment downstream | post-trade event | trade journal row, audit log line | `trade_journal.db`, `runtime_logs/signal_audit.jsonl` | unchanged |
| 10 | `REVIEW` | Model-assisted | trade journal + outcome | `review_journal` row | future `ml/reports/`, `runtime_logs/` | future post-trade review model |

## Type ownership

| Type | Module | Stages that produce it | Stages that consume it |
|---|---|---|---|
| `TradeCandidate` | `src/pipeline/types.py` | 4 (`SETUP`), 5 (`SCORE`) | 6 (`RISK`), 7 (`PACKAGE`) |
| `ExecutionIntent` | `src/pipeline/types.py` | 7 (`PACKAGE`) | 8 (`ROUTE`) |
| `StageDecision` | `src/pipeline/types.py` | every stage | the orchestrator + the audit log |
| `Direction` | `src/pipeline/types.py` | shared by `TradeCandidate` and `ExecutionIntent` | n/a |
| `RejectionSource` | `src/pipeline/types.py` | stages that VETO | the orchestrator |

## Logging requirements

Every stage that produces a `StageDecision` must emit a structured
log line carrying:

- `stage` (the `StageName` value),
- `candidate_id` when applicable,
- `verdict`,
- `rejection_source` when `verdict == VETO`,
- `reason`,
- `score` and `model_id` when present,
- a monotonic timestamp (`created_at` ISO 8601 UTC).

During WS2, only `STAGE.SCORE` and `STAGE.REVIEW` log fields are
new. Stages 1–4 and 6–9 already log via the existing audit pipeline
(`runtime_logs/signal_audit.jsonl`); WS8 will reconcile and unify
these into a single line shape.

## Migration plan

WS2 lands the types and the contract spec. The live runtime call
sites in `src/runtime/pipeline.py`, `src/units/`, and
`src/core/coordinator.py` continue to use the existing `OrderPackage`
shape. Migrating those call sites onto `TradeCandidate` /
`ExecutionIntent` is **out of scope for WS2** and gated on operator
approval; it will be filed as a follow-up Tier 2 sprint. Until then:

- New code that wants to emit a `TradeCandidate` (e.g. a WS5
  baseline) constructs one directly from `src/pipeline/types.py`.
- Existing strategies still emit `OrderPackage` and route through
  the existing coordinator path.
- A future adapter sprint converts at the boundary.

## Update rule

This doc must be reviewed in the same PR as any change to the stage
list, the type definitions in `src/pipeline/types.py`, the
rejection-source policy, or the logging fields. Stage map changes
also require an update to
[`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).
