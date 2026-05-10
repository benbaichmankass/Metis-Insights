# S-AI-WS1 — AI traders WS1: Architecture baseline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (master plan), subordinate to [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md)
**Status:** ✅ COMPLETE

## Goal

Land the canonical AI-scope architecture doc per WS1 of the AI traders
master plan. Establish a single source of truth for current state
(post-audit) and target state of the AI model layer in the trading
platform, with explicit safety statement that deterministic risk
controls sit outside the AI layer.

## Deliverables

- New: [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md) — canonical AI-scope architecture doc.
- Updated: [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) — cross-link added in preamble + Repo Responsibility Map + Architecture Update Rule + Verification Checklist.
- Updated: [`docs/sprint-plans/ai-traders/ws1-architecture-baseline.md`](../sprint-plans/ai-traders/ws1-architecture-baseline.md) — sprint id `S-AI-WS1`, status → IN PROGRESS, acceptance criteria checked.
- Updated: [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) — WS1 row → DONE, change-log row appended.
- Updated: [`ROADMAP.md`](../../ROADMAP.md) — WS1 status row → DONE, M9 status → IN PROGRESS, S-AI-WS1 ledger row appended.
- This file: sprint log per `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

## Acceptance (from WS1 sprint plan)

- [x] Single architecture doc reflecting current + target state.
- [x] Names live / staged / planned / forbidden.
- [x] Explicit deterministic-controls-outside-AI safety statement.
- [x] Linked from `ARCHITECTURE-CANONICAL.md`.

## Decisions

- **Sprint id.** Used `S-AI-WS1` instead of `S-067` because S-067 is
  in flight as the silent-empty error-path audit
  (`docs/sprints/sprint-067-prompt.md`). Themed id matches the
  `S-CANON-*` / `S-AI-ROADMAP` precedent.
- **Stage map.** The new doc uses the master plan's 10-stage list.
  `ARCHITECTURE-CANONICAL.md` documents an 8-step pipeline; the two
  are reconciled via cross-reference and explicit "WS2 reconciles"
  callout. WS2 owns landing typed schemas + the locked stage list.
- **Vestigial `ml/` tree.** `ml/` currently contains only `ml/config/`
  + `ml/src/{collect_data.py,test_breakout_strategy.py}`. Recorded as
  Known Gap; WS4 owns rebuilding to the master-plan target structure.
- **Existing `docs/architecture.md`.** S-008 9-unit translator
  section is current; "Target Structure" block predates the canonical
  doc set. Flagged as Known Gap; not blocking. Cleanup deferred to
  WS10.

## Out of scope (deferred)

- WS2 stage contracts + typed schemas.
- WS3 dataset taxonomy + first dataset builder.
- WS4 training-center expansion under `ml/`.
- WS10 architecture-change checklist + PR-template enforcement.
- Any code under `src/runtime/`, `src/units/`, or any model code.

## Hand-off

After this sprint ships, the natural follow-ups in priority order:

1. **WS2 — canonical trade pipeline.** Typed schemas for stage I/O,
   canonical `TradeCandidate` + `ExecutionIntent`, per-stage logging
   spec, test scaffolding. Doc + light scaffold work; no live runtime
   risk. Sprint plan: [`docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md`](../sprint-plans/ai-traders/ws2-canonical-pipeline.md).
2. **WS3 — data foundation.** Can run in parallel with WS2 once WS1
   lands. Dataset taxonomy + schema doc + first reproducible builder.
   Sprint plan: [`docs/sprint-plans/ai-traders/ws3-data-foundation.md`](../sprint-plans/ai-traders/ws3-data-foundation.md).

## Live runtime impact

None. Doc-only sprint. No source under `src/` modified, no `deploy/`
unit modified, no `config/` value changed, no test or CI workflow
edited.
