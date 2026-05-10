# WS1 — Architecture baseline

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Sprint id:** **S-AI-WS1** (started 2026-05-10)
**Status:** 🔄 IN PROGRESS

> **Sprint id note:** the next free numeric id at the time of
> kickoff was S-067, but S-067 is already in flight as the
> silent-empty error-path audit (see
> [`docs/sprints/sprint-067-prompt.md`](../../sprints/sprint-067-prompt.md)).
> WS1 uses the themed `S-AI-WS1` id, matching the
> `S-CANON-*` and `S-AI-ROADMAP` precedent.

## Objective

Create a clear source of truth for the current and target AI trading
architecture before building new model infrastructure.

## Inputs

- `docs/ARCHITECTURE-CANONICAL.md`
- `docs/architecture.md`
- `ROADMAP.md` (M9 / M10 rows)
- Existing `ml/`, `src/`, `automation/`, `deploy/` trees

## Tasks

1. **Repo audit.** Identify and list:
   - live trading entry points,
   - strategy modules,
   - runtime pipeline + scheduler,
   - risk-manager path,
   - broker adapters,
   - bot-control path (Telegram, ops scripts),
   - deployment flow,
   - existing research / backtest / `ml/` utilities.
2. **Create canonical AI-platform doc** at
   `docs/architecture/ai-model-platform.md` with sections:
   - `Current State` — what exists today, what is experimental, what is
     live.
   - `Target State` — the future specialist-model architecture.
   - **Component diagram** in Mermaid showing the full trading pipeline.
   - **Stage table** with columns: stage │ deterministic vs model-assisted
     │ owning paths.
   - `Architecture Change Log` — must be updated whenever model
     boundaries, data schemas, or deployment stages change.
3. **Index update.** Link the new doc from
   `docs/ARCHITECTURE-CANONICAL.md` and the main repo docs nav.
4. **Explicit safety statement.** Doc must state that deterministic risk
   controls are outside the AI layer and cannot be bypassed by model
   output.

## Acceptance

- [x] A single architecture doc exists and reflects both current repo
  reality and the desired future state.
- [x] The doc names which parts are live, staged, planned, forbidden.
- [x] The doc explicitly states deterministic risk controls are outside
  the AI layer.
- [x] The doc is linked from `docs/ARCHITECTURE-CANONICAL.md`.

## Out of scope (deferred to later workstreams)

- Building any new pipeline code (WS2).
- Building any new model code (WS5+).
- Touching live runtime files.
- Reconciling the 8-step pipeline in `ARCHITECTURE-CANONICAL.md`
  with the 10-stage map in `ai-model-platform.md` — WS2 owns this.

## Risks

- Audit drift if WS2 starts before WS1 lands. **Mitigation in
  effect:** WS2 cannot open a PR until WS1 lands `ai-model-platform.md`
  on `main`.

## Deliverables (this sprint)

- `docs/architecture/ai-model-platform.md` — new canonical AI-scope doc.
- `docs/ARCHITECTURE-CANONICAL.md` — link added, no other change.
- This file — sprint id and status updated.
- `docs/AI-TRADERS-ROADMAP.md` — change-log row, WS1 status update.
- `ROADMAP.md` — WS1 status row, S-AI-WS1 ledger entry.
