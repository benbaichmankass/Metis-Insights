# S-AI-WS7-PART-1 — Deployment-stage metadata + ShadowPredictor scaffold

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws7-deployment-tiers.md`](../sprint-plans/ai-traders/ws7-deployment-tiers.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
**Status:** ✅ COMPLETE — first of (at least) two WS7 parts.

## Goal

Open WS7 (deployment tiers / shadow mode) with the smallest
scaffold that wires the 7-stage WS7 ladder into the WS4 model
registry and ships a `ShadowPredictor` wrapper ready to be used
by future live integrations. Critically: **no live trading
pipeline code is touched in this sprint**. The pipeline-side
shadow-call wiring is filed for PART-2 so the operator can
review the integration point before any model touches live
decision-making.

## Decisions

- **Pre-audit found two parallel enums.** WS4's registry
  (`ml/registry/model_registry.py`) defined a 5-state `status`
  field (`candidate` / `champion` / `paper` / `advisory` /
  `live-approved`); the manifest layer (`ml/manifest.py`)
  defined a 7-state `target_deployment_stage` field
  (`research_only` / `candidate` / `backtest_approved` /
  `shadow` / `advisory` / `limited_live` / `live_approved`)
  matching the WS7 spec exactly. The manifest's enum was
  validated at construction time but the registry never
  surfaced it — `RegistryEntry.manifest` was a flat dict.
- **Strategy: add the WS7 axis without disturbing WS4.** Keep
  the legacy `status` machine working (existing entries +
  promotion edges + `champion` pointer). Add a NEW orthogonal
  `target_deployment_stage` field + `stage_history` tuple on
  `RegistryEntry`, with its own `_STAGE_TRANSITIONS` and a new
  `promote_stage()` method. The two state machines are
  independent — a model can sit at `status=candidate` (WS4 sense)
  AND `target_deployment_stage=shadow` (WS7 sense) at the same
  time. PART-2 (filed) can decide whether to deprecate
  `status` once the stage axis is fully wired.
- **Stage default = `research_only` for backward-compat.** Old
  registry entries on disk have no `target_deployment_stage`
  key; `from_dict` defaults to `research_only` on load. New
  registrations pick up `manifest["target_deployment_stage"]`
  if present, otherwise default to `research_only`.
- **Transition rules** (forward + one-step rollback for every
  forward edge; `live_approved` has rollback only):
  - `research_only → candidate`
  - `candidate → backtest_approved | research_only`
  - `backtest_approved → shadow | candidate`
  - `shadow → advisory | backtest_approved`
  - `advisory → limited_live | shadow`
  - `limited_live → live_approved | advisory`
  - `live_approved → advisory` (rollback only)
  No skip-the-ladder edges. Every transition records a
  `StageEvent` with required `by` (operator handle) and
  `reason` (gate name + evidence pointer).
- **No-op transitions raise.** `promote_stage("m-1",
  current_stage, ...)` raises rather than silently succeeding.
  Audit-log integrity: every stage event represents a real
  state change.
- **ShadowPredictor is a pure composition wrapper.**
  `ml/predictors/shadow.py::ShadowPredictor(wrapped: Predictor,
  *, model_id, stage, log_path=None)`. Forwards
  `.predict(row)` to the wrapped predictor, emits a JSONL audit
  line per call (`predicted_at_utc`, `model_id`, `stage`,
  `score`, `row_keys`), returns the wrapped score unchanged.
  Caller decides what to do with the score; the wrapper is a
  pure side-channel observer.
- **`row_keys` (not values) in the audit log.** Defense-in-depth
  against accidentally capturing operator-side state (API keys,
  account balances, etc.) in the shadow trail. If full row
  context is needed for replay, operators have
  `signal_audit.jsonl` already; shadow events join to it by
  timestamp. Tested explicitly with a sentinel
  `api_key=SECRET_DO_NOT_LOG` value.
- **Stage value validated at wrapper construction.** A typo
  like `stage="shadow_mode"` fails fast at __init__ — better
  than silently misclassifying a year of shadow-mode runs.

## Deliverables

- `ml/registry/model_registry.py` — extended:
  - Imports `VALID_DEPLOYMENT_STAGES` from `ml.manifest`.
  - New `_STAGE_TRANSITIONS` table (7 stages).
  - New `StageEvent` dataclass (parallel to `StatusEvent`).
  - `RegistryEntry` gains `target_deployment_stage: str =
    "research_only"` + `stage_history: tuple[StageEvent, ...] =
    ()`, with `__post_init__` validation against
    `VALID_DEPLOYMENT_STAGES`.
  - `register()` extracts stage from manifest with backward-
    compat default and rejects unknown values.
  - New `promote_stage(model_id, new_stage, *, by, reason)`
    method enforcing transitions + no-op refusal + non-blank
    `by` / `reason`.
- `ml/registry/__init__.py` — exports `StageEvent`.
- `ml/predictors/shadow.py` — new `ShadowPredictor` wrapper.
- `ml/predictors/__init__.py` — exports `ShadowPredictor`.
- `tests/ml/test_model_registry.py` — 13 new tests under
  `TestDeploymentStage`:
  - Default stage is `research_only`.
  - Register picks up manifest stage.
  - Register rejects unknown stage.
  - Round-trip preserves stage + stage_history.
  - Promote stage forward (every edge).
  - Promote stage rollback.
  - Promote stage disallowed skip.
  - Promote stage unknown raises.
  - Promote stage no-op refused.
  - Blank `by` / `reason` rejected.
  - Full ladder walk (research_only → live_approved).
  - Legacy status `promote()` preserves stage.
  - `VALID_DEPLOYMENT_STAGES` integrity check.
- `tests/ml/test_shadow_predictor.py` — 12 new tests:
  - Wrapped score returned unchanged.
  - One JSONL line per call.
  - `row_keys` records keys not values (secret sentinel check).
  - Parent dir auto-created.
  - `log_path=None` runs silently (no IO).
  - Invalid stage rejected.
  - Blank model_id rejected.
  - Non-Predictor wrapped object rejected.
  - Int score coerced to float.
  - Every valid stage accepted.
  - `model_id` + `stage` exposed as properties.
  - Existing log file appended-to (not overwritten).

## Acceptance

- [x] `pytest tests/ml/test_model_registry.py tests/ml/test_shadow_predictor.py` —
      25 / 25 pass (12 prior `TestModelRegistry` + 13 new
      `TestDeploymentStage` + 12 new `TestShadowPredictor`).
- [x] `pytest tests/ml/` — full ML suite 231 / 231 pass (206
      prior + 13 + 12; no regression).
- [x] `ruff check ml/registry/ ml/predictors/shadow.py
      tests/ml/test_shadow_predictor.py tests/ml/test_model_registry.py` —
      clean.
- [x] Backward-compat: old registry entries on disk (without
      `target_deployment_stage`) load with the default and
      preserve their existing `status`. Verified by round-trip
      test.
- [x] No live runtime touched (`src/runtime/pipeline.py`,
      `src/units/accounts/*` unchanged).

## Out of scope (filed for follow-ups)

- **S-AI-WS7-PART-2 — pipeline-side shadow-call wiring.** Add
  a pre-decision hook in the trade pipeline (or per-strategy) so
  a shadow-mode model receives the same row a strategy is
  scoring and emits its score side-by-side with the
  deterministic decision. NOT autonomous — this PR will need
  operator approval of the integration point per the
  non-negotiable "No model in live strategy logic without
  staged promotion + operator approval".
- **WS4 status enum deprecation.** Once PART-2 lands and stages
  are fully wired, the legacy 5-state `status` machine can be
  retired in favour of the unified stage machine. Migration
  needs a one-shot script to map `status` → `stage` for
  existing entries.
- **`limited_live` semantics.** Stage 6 (`limited_live`)
  in the WS7 spec is "limited live influence" — what that means
  in code (e.g. percentage-of-position-size weighting, veto-only
  influence, etc.) is undefined. Filed for a sub-sprint once
  PART-2 has the basic shadow→advisory path working.
- **Operator-approval gates in code.** WS7 task 4 ("Require
  explicit operator approval before any model influences
  strategy behavior in live mode") is currently documentation
  only in `ml/promotion/checklist.py`. A future sprint should
  refuse `promote_stage` to `limited_live` / `live_approved`
  unless the `reason` references an operator-approval artifact
  (Telegram ack id, comms request id, etc.).
- **Promotion checklist alignment.** `ml/promotion/checklist.py`
  was built for the WS4 status enum; it references gates like
  `candidate→paper` that don't exist in the WS7 ladder. Needs
  a parallel set of stage-gates: `research_only→candidate`,
  `candidate→backtest_approved`, etc.
- **CLI exposure.** The new `promote_stage()` method is not
  yet wired into any CLI. The WS4 status `promote()` has
  `ml.promotion.cli`; a sibling `promote_stage` CLI command
  should land before operators are expected to use it
  routinely.

## Hand-off

- WS7 is now structurally opened. Model registry knows about
  stages; `ShadowPredictor` exists and is tested. Pipeline
  integration (the high-blast-radius piece) is intentionally
  separate so the operator can review it as its own PR.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md);
  WS7 status flips from "📋 NOT STARTED" to "🔄 IN PROGRESS
  (PART-1 done; PART-2 queued)".

## Live runtime impact

None. New code paths are only exercised by `pytest`. No
imports added to `src/runtime/*` or `src/units/accounts/*`.
The registry's on-disk JSON layout gains two new keys
(`target_deployment_stage`, `stage_history`); old files load
cleanly with the defaults.
