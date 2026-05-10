# S-AI-WS2 — AI traders WS2: Canonical trade pipeline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (master plan); subordinate to [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) and [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
**Status:** ✅ COMPLETE

## Goal

Formalise the trade pipeline as ten named stages with typed I/O so
later AI workstreams can plug in without breaking the deterministic
floor. Lock stage names. Land canonical `TradeCandidate` /
`ExecutionIntent` types with invariant checks. Document per-stage
contracts and the deterministic-vs-model rejection-source rule.

## Deliverables

- New: [`src/pipeline/__init__.py`](../../src/pipeline/__init__.py) — module entrypoint.
- New: [`src/pipeline/types.py`](../../src/pipeline/types.py) — frozen-dataclass `Direction`, `StageName`, `DecisionVerdict`, `RejectionSource`, `StageDecision`, `TradeCandidate`, `ExecutionIntent` with `__post_init__` invariant checks.
- New: [`tests/pipeline/test_types.py`](../../tests/pipeline/test_types.py) — schema invariants (frozen, range, required-field, model-score range, ten-stage count).
- New: [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md) — per-stage I/O, owners, logging, migration plan.
- Updated: [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md) — stage map switched to locked `StageName` values; companion-docs section + Mermaid + Known Gaps + Architecture Change Log all updated.
- Updated: [`docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md`](../sprint-plans/ai-traders/ws2-canonical-pipeline.md) — sprint id `S-AI-WS2`, status → DONE, acceptance check-offs.
- Updated: [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) — WS2 row → DONE; change-log row appended.
- Updated: [`ROADMAP.md`](../../ROADMAP.md) — WS2 status row → DONE; S-AI-WS2 ledger row.
- This file: sprint log per `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

## Acceptance (from WS2 sprint plan)

- [x] Every pipeline stage has a documented owner and I/O contract.
- [x] Repo contains shared types / schemas for trade candidates
  and execution intents.
- [x] Explicit rule: models cannot place or modify orders outside
  the validated execution-packaging path. Documented in
  `ExecutionIntent` docstring and in `stage-contracts.md`.
  `RejectionSource.DETERMINISTIC` distinguished from
  `RejectionSource.MODEL` and required for any `VETO` decision.

## Decisions

- **Frozen dataclasses, not pydantic.** Stays inside the stdlib;
  avoids pulling pydantic into non-API code paths. The API layer
  already depends on pydantic via FastAPI; that stays where it is.
- **Additive, not replacing.** WS2 lands the types and stops there.
  No live runtime call site is rewired. Migration of
  `OrderPackage` onto `TradeCandidate` is filed as a Tier 2
  follow-up requiring operator approval.
- **Rejection source distinction.** `DETERMINISTIC` rejections are
  immutable; `MODEL` rejections are advisory. The runtime invariant
  enforced by `__post_init__` is that any `VETO` records its
  source. Whether a `MODEL` veto actually blocks a candidate is a
  WS7 deployment-tier concern.
- **`__post_init__` validates** confidence and score ranges,
  positive entry / stop_loss / quantity, model-score ranges, and
  rejection-source presence on `VETO`. Out-of-range or missing
  values raise `ValueError` at construction time — loud failure.
- **Stage 9-step pipeline reconciliation deferred.** The 8-step
  pipeline in `ARCHITECTURE-CANONICAL.md` and the 10-stage list
  here are bridged by a mapping in `stage-contracts.md` rather than
  rewriting the canonical doc. WS10 may revisit when the
  architecture-change checklist lands.

## Out of scope (deferred)

- WS3 dataset taxonomy + first dataset builder.
- WS4 training-center expansion under `ml/`.
- Migration of live runtime path onto WS2 types (Tier 2; needs
  operator ack).
- Lint guard preventing model code from constructing
  `ExecutionIntent` (filed for WS10).
- Unifying the structured-log line shape across stages (deferred
  to WS8).

## Hand-off

After this sprint ships, the natural follow-ups in priority order:

1. **WS3 — data foundation.** Dataset taxonomy + schema doc + first
   reproducible builder. Sprint plan:
   [`docs/sprint-plans/ai-traders/ws3-data-foundation.md`](../sprint-plans/ai-traders/ws3-data-foundation.md).
2. **WS4 — training center.** Repo-native training factory
   structure under `ml/`. Sprint plan:
   [`docs/sprint-plans/ai-traders/ws4-training-center.md`](../sprint-plans/ai-traders/ws4-training-center.md).
3. **Tier 2 follow-up: live-path migration onto WS2 types.** Wire
   `TradeCandidate` / `ExecutionIntent` through the existing
   coordinator path. Operator-ack required.

## Live runtime impact

None. Additive code-only sprint:

- New module `src/pipeline/` is not imported by any live runtime
  code (`src/runtime/`, `src/units/`, `src/core/coordinator.py`,
  `src/main.py`).
- New tests under `tests/pipeline/` are unit tests with no
  side effects.
- No `deploy/` unit, no `config/`, no CI workflow modified.
- ruff guardrail respected: no operator-hold paths
  (`src/runtime/`, `src/units/accounts/`, `src/main.py`,
  `config/accounts.yaml`, `deploy/*`) modified.
