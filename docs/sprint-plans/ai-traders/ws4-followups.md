# WS4 follow-ups

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Sprint id:** **S-AI-WS4-FU** (closed 2026-05-10)
**Status:** ✅ DONE

## Objective

Close the WS4 + WS5-A follow-ups that improve the foundation
before the next round of specialist baselines:

1. **Generic `Predictor` interface.** Decouple evaluators from
   trainer-specific state shape.
2. **Walk-forward / time-aware splitters.** Critical prereq for
   any time-series-aware baseline (WS5-B regime classifier needs
   this).
3. **`compare` CLI subcommand.** Side-by-side metric diff across
   two registry entries.
4. **Global-only sanity baseline manifest.** Pair with the
   per-strategy winrate baseline to test whether the
   `strategy_name` feature is carrying signal.

## Deliverables (all met)

Code:
- New: [`ml/predictors/`](../../../ml/predictors/) — `Predictor` ABC
  + `ConstantPredictor` + `PerGroupPredictor`.
- Updated: [`ml/trainers/{base, constant_baseline, per_strategy_winrate}.py`](../../../ml/trainers/) —
  declare `PREDICTOR_CLASS` class var per trainer.
- Updated: [`ml/evaluators/{base, regression, classification}.py`](../../../ml/evaluators/) —
  `_resolve_predictor(state)` helper; refactor `score()` to use
  the predictor instead of reading state-specific keys.
  Probability clamping in classification so a regression-style
  predictor doesn't break Brier.
- New: [`ml/experiments/splitters.py`](../../../ml/experiments/splitters.py) —
  `holdout` / `time_aware_holdout` / `walk_forward` dispatched
  from `evaluator_config.split_strategy`. Default is `holdout`
  matching the existing WS4 behavior.
- Updated: [`ml/experiments/runner.py`](../../../ml/experiments/runner.py) —
  use `splitters.split()`.
- Updated: [`ml/cli.py`](../../../ml/cli.py) — add
  `compare <id-a> <id-b>` subcommand.
- New: [`ml/configs/baseline-trade-outcome-global.yaml`](../../../ml/configs/baseline-trade-outcome-global.yaml) —
  global-only sanity baseline using `ConstantPredictionTrainer`
  + `ClassificationEvaluator` against `trade_outcomes`.

Tests:
- New: [`tests/ml/test_predictors.py`](../../../tests/ml/test_predictors.py).
- New: [`tests/ml/test_splitters.py`](../../../tests/ml/test_splitters.py).
- Updated:
  [`tests/ml/test_per_strategy_winrate.py`](../../../tests/ml/test_per_strategy_winrate.py) —
  mock states now include `trainer` qualname so predictor-resolved
  scoring works; explicit test for missing `trainer` raising.
- New: [`tests/ml/test_compare_cli.py`](../../../tests/ml/test_compare_cli.py).

Docs:
- Updated: [`docs/ml/training-center.md`](../../ml/training-center.md) —
  Predictor section + split-strategy section + `compare` subcommand.
- Updated: [`docs/architecture/ai-model-platform.md`](../../architecture/ai-model-platform.md) —
  Known Gaps refresh + Change Log row.
- Updated:
  [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](ws5-baseline-models.md) —
  market_raw multi-source design note added to the WS5-B sub-sprint.
- New: [`docs/sprint-logs/S-AI-WS4-FU.md`](../../sprint-logs/S-AI-WS4-FU.md).
- Updated: [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md),
  [`ROADMAP.md`](../../../ROADMAP.md) — change log + ledger row.

## Out of scope (deferred per the master plan)

- Aggregated walk-forward (averaging metrics across folds).
  `splitters.split_walk_forward(...)` returns the fold list; the
  runner uses single-split form via `splitters.split(...)` which
  picks the last fold. Aggregated mode requires a richer runner +
  metrics format and is filed.
- Per-strategy detail metrics artifact alongside scalar registry
  metrics. Filed for a follow-up to WS5-A.
- Registry concurrent-writer locking.
- `python -m ml.datasets publish` HF subcommand.

## Acceptance

- [x] Existing WS4 + WS5-A tests still pass (default
  `split_strategy=holdout` keeps the suffix splitter behavior;
  `PerStrategyWinRateTrainer.fit()` already emits the `trainer`
  qualname so predictor resolution works for the round-trip test).
- [x] New tests cover predictors, splitters, compare CLI.
- [x] Two manifests (per-strategy + global-only) ship under
  `ml/configs/` and can be compared via `python -m ml compare`.
- [x] No operator-hold path modified.
