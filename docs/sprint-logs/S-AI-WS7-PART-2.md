# S-AI-WS7-PART-2 — Shadow-mode per-strategy adapter

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws7-deployment-tiers.md`](../sprint-plans/ai-traders/ws7-deployment-tiers.md), [`docs/sprint-logs/S-AI-WS7-PART-1.md`](S-AI-WS7-PART-1.md)
**Status:** ✅ COMPLETE — second of three planned WS7 parts.

## Goal

Land the shadow-mode helper that lets a strategy run a
shadow-stage model side-by-side with its deterministic decision
**without affecting trade outcome**. Operator-chosen integration
pattern (2026-05-10): **per-strategy adapter**, not pipeline-level
pre-route — blast radius scoped to one strategy at a time;
failure of a model call cannot crash the pipeline tick.

This sprint ships **the helper only**. No production strategy is
wired in this PR. Wiring the first strategy is filed as PART-3 so
the operator reviews each strategy adoption as its own change.

## Decisions

- **`with_shadow_pred(decision, *, predictor, feature_row, logger=None)`**
  is the single integration glue. Stateless, returns the
  deterministic `decision` byte-for-byte regardless of predictor
  outcome.
- **Model failure cannot crash the tick.** `predictor.predict(...)`
  is wrapped in a `try/except Exception` that logs a warning and
  returns the deterministic decision. Per the WS7 non-negotiable
  "Deterministic fallback when a model is unavailable", a broken
  pickle, schema drift, or division-by-zero in the model can never
  block a real trade decision.
- **`predictor is None` is a pass-through.** Strategies can call
  `with_shadow_pred` unconditionally even when no model is wired
  for the current account / strategy / run — the helper costs
  nothing in that case. This matters because we want strategy
  authors to ALWAYS thread their decision through the helper, so
  enabling shadow mode for a new strategy is a config change, not
  a code change.
- **Bare `Predictor` is rejected, not silently consumed.** A
  strategy passing `predictor=PerGroupPredictor(...)` instead of
  `ShadowPredictor(PerGroupPredictor(...), ...)` raises
  `TypeError`. The `ShadowPredictor` wrapper is the audit-log
  surface; bypassing it would let a model call happen without
  audit. We surface that misconfiguration loud.
- **The helper does NOT catch strategy exceptions.** Only the
  predictor's exception is contained. A misbehaving strategy
  still propagates as before. The helper's `try/except` is
  scoped to exactly the `predictor.predict(feature_row)` call —
  not the whole function body.
- **Defense-in-depth test: decision keys unchanged.** A dedicated
  test verifies that `with_shadow_pred(package, ...)` returns a
  package whose keys are exactly the input keys — no
  `shadow_score`, no `model_id`, no anything model-derived
  leaking into the order package. Even if a future refactor
  confuses "shadow" with "advisory", this test catches it.
- **No audit line on predictor failure.** Audit log entries are
  emitted by `ShadowPredictor.predict()` after the wrapped
  predictor returns; if the wrapped predictor raises, no audit
  line lands. Operators reading the audit log can trust each line
  represents a real, completed model call. Verified by a test
  that confirms the audit file is empty after a `_BrokenPredictor`
  run.

## Deliverables

- `src/runtime/shadow_adapter.py` — `with_shadow_pred` helper.
  Module docstring carries the integration contract + a worked
  example showing how a strategy adapter would adopt it.
- `tests/runtime/test_shadow_adapter.py` — 10 tests:
  - Decision returned unchanged on predictor success.
  - Decision returned unchanged on predictor failure (broken
    model) + WARNING logged.
  - Predictor called with the feature row the strategy supplied.
  - Audit log emitted on success path.
  - No audit log on predictor failure (failure containment).
  - `predictor=None` pass-through.
  - Strategy exception NOT caught by the helper.
  - Bare `Predictor` (non-`ShadowPredictor`) rejected with
    `TypeError`.
  - Custom logger honoured.
  - **Defense-in-depth: decision keys unchanged** — the order
    package returned by the helper has the exact same key set
    as the input.

## Acceptance

- [x] `pytest tests/runtime/test_shadow_adapter.py` — 10/10 pass.
- [x] `pytest tests/ml/ tests/runtime/test_shadow_adapter.py` —
      241 / 241 pass (231 prior + 10 new; no regression).
- [x] `ruff check src/runtime/shadow_adapter.py
      tests/runtime/test_shadow_adapter.py` — clean.
- [x] No production strategy modified in this PR
      (`src/units/strategies/*` untouched).
- [x] No live runtime touched at runtime — the helper is
      defined but not imported by any module under `src/`.

## Out of scope (filed for follow-ups)

- **S-AI-WS7-PART-3 — wire first strategy.** Pick one of
  `src/units/strategies/{vwap, turtle_soup}.py`, thread its
  signal output through `with_shadow_pred` with a real
  `ShadowPredictor` instance, and feed it a feature row drawn
  from the strategy's `cfg + meta`. Operator picks which
  strategy to start with; pre-PR scoping question will surface
  WHICH model gets shadow-wired and WHICH features it sees.
- **Config-driven shadow predictor selection.** Currently a
  strategy that wanted to use shadow mode would have to import
  + construct its own `ShadowPredictor`. A config-driven path
  (e.g. `config/strategies.yaml::strategies.vwap.shadow_model_id`
  resolved via the model registry at strategy-init time) is
  the natural next iteration once one strategy proves the
  pattern.
- **Multiple shadow predictors per strategy.** A strategy can
  theoretically run multiple models concurrently in shadow
  mode (e.g. one each from WS5-C, WS5-D, WS5-E). The helper
  takes one predictor; a `with_shadow_preds(decisions, ...)`
  variant or a composite `MultiShadowPredictor` could come
  later. Defer until one strategy actually wants this.
- **Shadow audit log rotation.** `ShadowPredictor` writes to a
  single JSONL file forever. Operators on the VM will want
  daily rotation + an inventory hook (similar to
  `signal_audit.jsonl`'s consumer). Filed for an ops
  follow-up.

## Hand-off

- WS7 now has the per-strategy hook contract pinned in code
  and tested. PART-3 is the operator-reviewed integration of
  one specific strategy.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md);
  WS7 status stays "🔄 IN PROGRESS (PART-1 + PART-2 done;
  PART-3 wires first strategy)".

## Live runtime impact

None. `src/runtime/shadow_adapter.py` is defined but not
imported by any other module under `src/`. The helper is a pure
function that takes a predictor + a feature row and returns the
decision unchanged; with `predictor=None` (the default for
unwired strategies), it is a single-branch passthrough. Zero
risk to live trading until PART-3 lands.
