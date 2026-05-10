# WS1 — Architecture baseline

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started

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

- A single architecture doc exists and reflects both current repo reality
  and the desired future state.
- The doc names which parts are live, staged, planned, forbidden.
- The doc explicitly states deterministic risk controls are outside the AI
  layer.
- The doc is linked from the main repo docs navigation.

## Out of scope

- Building any new pipeline code.
- Building any new model code.
- Touching live runtime files.

## Risks

- Audit drift if WS2 starts before WS1 lands. Mitigation: WS2 cannot open a
  PR until WS1 lands `docs/architecture/ai-model-platform.md` on `main`.
