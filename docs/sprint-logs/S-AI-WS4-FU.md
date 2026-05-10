# S-AI-WS4-FU — AI traders WS4 follow-ups

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/ml/training-center.md`](../ml/training-center.md)
**Status:** ✅ COMPLETE

## Goal

Close the WS4 + WS5-A follow-ups that strengthen the foundation
before the next baseline. Generic `Predictor` interface,
time-series splitters, `compare` CLI, global-only sanity manifest,
and the operator's `market_raw` multi-source design note.

## Deliverables

Code (stdlib + pyyaml):
- New: [`ml/predictors/`](../../ml/predictors/) — `Predictor` ABC
  + `ConstantPredictor` + `PerGroupPredictor`.
- Updated: [`ml/trainers/`](../../ml/trainers/) —
  `PREDICTOR_CLASS` class var on each concrete trainer.
- Updated: [`ml/evaluators/`](../../ml/evaluators/) —
  `_resolve_predictor` base helper; both evaluators refactored.
- New: [`ml/experiments/splitters.py`](../../ml/experiments/splitters.py).
- Updated: [`ml/experiments/runner.py`](../../ml/experiments/runner.py)
  uses `splitters.split()`.
- Updated: [`ml/cli.py`](../../ml/cli.py) adds `compare`
  subcommand.
- New: [`ml/configs/baseline-trade-outcome-global.yaml`](../../ml/configs/baseline-trade-outcome-global.yaml).

Tests:
- New: `tests/ml/test_predictors.py`,
  `tests/ml/test_splitters.py`,
  `tests/ml/test_compare_cli.py`.
- Updated: `tests/ml/test_per_strategy_winrate.py` — mock states
  include `trainer` qualname.

Docs:
- Updated: [`docs/ml/training-center.md`](../ml/training-center.md) —
  Predictor section + split-strategy section + `compare`
  subcommand.
- Updated: [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md) —
  Known Gaps refresh; Change Log row.
- Updated:
  [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md) —
  WS5-B `market_raw` multi-source design note (operator directive).
- New:
  [`docs/sprint-plans/ai-traders/ws4-followups.md`](../sprint-plans/ai-traders/ws4-followups.md).
- This file.
- Updated: [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md),
  [`ROADMAP.md`](../../ROADMAP.md).

## Decisions

- **`PREDICTOR_CLASS` is a class var on `Trainer`, not a separate
  registry.** Each concrete trainer self-declares its predictor.
  Evaluators import the trainer class via `state['trainer']`
  qualname and read its `PREDICTOR_CLASS`. No magic registry.
- **Default `split_strategy = holdout`** matches WS4 behavior so
  existing manifests round-trip identically.
- **`split_walk_forward(...)` returns folds; `split(...)` returns
  one (train, test) pair.** Aggregated walk-forward is filed for a
  follow-up; it requires runner + metrics-format changes.
- **Probability clamping in `ClassificationEvaluator`.** A
  regression-style predictor (constant outside [0,1]) used for
  classification gets clamped before scoring so Brier stays
  well-defined. Without clamping a misconfigured manifest would
  silently emit nonsense Brier scores.
- **`market_raw` design captured as a sprint-plan note**, not a
  separate ADR. The note records the operator's multi-source
  directive and a concrete adapter sketch so WS5-B can start with
  the design already pinned.

## Out of scope (deferred)

- WS5-B onwards.
- Aggregated walk-forward (metrics averaged across folds).
- Per-strategy detail metrics artifact (alongside scalar registry
  metrics).
- Registry concurrent-writer locking.
- `python -m ml.datasets publish` HF subcommand.

## Hand-off

1. **Operator review of the `market_raw` multi-source design**
   (see WS5 sprint plan). The CSV / yfinance / off-VM-exchange
   adapter set is the proposal; operator decides which adapter
   ships first in WS5-B.
2. **WS5-B — regime classifier.** Sprint plan:
   [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md).

## Live runtime impact

None. Stdlib-only additive code; only NEW paths under `ml/`,
`tests/ml/`, `docs/ml/`, `docs/sprint-plans/`, `docs/sprint-logs/`,
plus updates to existing canonical / roadmap docs. Operator-hold
paths not modified.
