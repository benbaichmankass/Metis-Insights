# WS2 — Canonical trade pipeline

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Sprint id:** **S-AI-WS2** (started + closed 2026-05-10)
**Status:** ✅ DONE

## Objective

Formally define the trade lifecycle so model work can plug in cleanly.

## Pipeline stages (locked)

Stage names are locked in
[`src/pipeline/types.py`](../../../src/pipeline/types.py) (`StageName`).
Per-stage I/O + owner files live in
[`docs/pipeline/stage-contracts.md`](../../pipeline/stage-contracts.md).

1. `INGEST` — Market and account ingest
2. `NORMALIZE` — Normalization
3. `CONTEXT` — Context assembly
4. `SETUP` — Setup detection
5. `SCORE` — Opportunity scoring
6. `RISK` — Risk gating (deterministic-only)
7. `PACKAGE` — Execution packaging
8. `ROUTE` — Broker routing
9. `CAPTURE` — Post-trade capture
10. `REVIEW` — Review and feedback

## Tasks (delivered)

1. [x] Add typed schemas (frozen dataclasses) for stage inputs and
   outputs in `src/pipeline/types.py`.
2. [x] Define canonical `TradeCandidate` and `ExecutionIntent`
   objects with `__post_init__` invariant checks.
3. [x] Mark which stages are deterministic and which may consume
   model scores — done in `docs/pipeline/stage-contracts.md` and
   `docs/architecture/ai-model-platform.md` § Stage map.
4. [x] Document and **enforce in code** that risk and broker
   validation may reject outputs from any model: `RejectionSource`
   distinguishes `DETERMINISTIC` (immutable) from `MODEL` (advisory),
   and `StageDecision` raises `ValueError` if a `VETO` decision is
   constructed without a `rejection_source`.
5. [x] Per-stage logging requirements (fields + log levels)
   documented in `docs/pipeline/stage-contracts.md` § Logging.
6. [x] Test scaffolding for stage contracts under
   `tests/pipeline/test_types.py`.
7. [x] Update `docs/architecture/ai-model-platform.md` with the
   final stage list and per-stage owner files.

## Acceptance

- [x] Every pipeline stage has a documented owner and I/O contract.
- [x] Repo contains shared types or schemas for trade candidates and
  execution intents.
- [x] Explicit rule: models cannot place or modify orders outside
  the validated execution-packaging path — documented in
  `src/pipeline/types.py` `ExecutionIntent` docstring and in
  `docs/pipeline/stage-contracts.md` § Safety invariant. Hard
  enforcement (lint guard) is filed as a follow-up.

## Out of scope (deferred)

- Migration of the live runtime path (`src/runtime/pipeline.py`,
  `src/units/strategies/*`, `src/core/coordinator.py`) onto the new
  types. The existing `OrderPackage` shape continues to flow
  through the live coordinator. Migration is a Tier 2 sprint and
  requires operator approval.
- Lint guard preventing new code from constructing `ExecutionIntent`
  outside `src/runtime/orders.py` — filed for WS10.
- Reconciliation of the structured-log line shape across stages
  1–9 — deferred to WS8.

## Risks (resolved)

- **Schema churn.** Mitigated by keeping the first version minimal
  and additive. New fields can be added with defaults without
  breaking existing callers.

## Deliverables (this sprint)

- `src/pipeline/__init__.py` — module entrypoint + public surface.
- `src/pipeline/types.py` — schemas with invariant checks.
- `tests/pipeline/__init__.py` — (empty) package marker.
- `tests/pipeline/test_types.py` — schema invariants.
- `docs/pipeline/stage-contracts.md` — per-stage spec.
- `docs/architecture/ai-model-platform.md` — stage map updated to
  reference the locked names; Known Gaps refreshed; Change Log row
  appended.
- This file — sprint id, status, acceptance check-offs.
- `docs/AI-TRADERS-ROADMAP.md` — WS2 status → DONE; change-log row.
- `ROADMAP.md` — WS2 status → DONE; S-AI-WS2 ledger row.
- `docs/sprint-logs/S-AI-WS2.md` — sprint log per canonical
  template.
