# S-AI-WS5-B-PART-2 PR 2B — Regime classifier baseline

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/data/dataset-{taxonomy,schema}.md`](../data/), [`docs/ml/training-center.md`](../ml/training-center.md), [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
**Status:** ✅ COMPLETE

## Goal

Land the WS5-B baseline classifier: a 3-class regime classifier
(trend / range / volatile) trained on a derived
`market_features` family. PR 2A wired the Bybit fetch path; this
PR adds the feature builder, the multinomial trainer / predictor /
evaluator, and the YAML manifest that ties them together.

## Decisions

- **Sprint id `S-AI-WS5-B-PART-2 PR 2B`.** Operator-chosen split.
  PR 2B is independent of PR 2A — branched off `main`, not stacked
  on PR 2A's branch — so each can review and merge on its own
  cadence.
- **3-class regime, not binary high/low-vol.** Operator picked
  "3-class trend / range / volatile" over "binary high/low-vol".
  The binary baseline would have been smaller scope, but the 3-
  class output is what downstream orchestration eventually needs,
  so paying the multiclass-evaluator tax now keeps that work-stream
  unblocked.
- **New `market_features` family, not an extension of
  `market_raw`.** Operator-chosen. Keeps `market_raw` canonical
  OHLCV-only (per the WS5-B-PART-1 architectural principle that
  `market_raw` carries no labels) and lets the engineered-feature
  family own its own leakage discipline. Matches the dataset
  taxonomy filed in S-AI-WS3.
- **Forward-window labels for leakage discipline.** Bar `t`'s
  features read only `[t - vol_window_n + 1 .. t]` (inclusive of
  bar `t`); bar `t`'s label reads only `[t + 1 .. t + forward_window_m]`
  (strictly after `t`). The two windows do not overlap, so
  feature/label leakage is structurally impossible. Metadata stamps
  `leakage_test_status: passed`.
- **`MulticlassPredictor` as a `Predictor` subclass, not a
  parallel hierarchy.** Adds `predict_label` + `predict_proba`
  on top of the existing `predict(row) -> float` surface. Default
  `predict` returns the top-class probability so existing
  single-float consumers don't break. The multiclass evaluator
  type-narrows and raises `TypeError` against any non-multiclass
  predictor.
- **Per-bucket modal class trainer (simplest baseline).** Operator
  guidance: "simplest baseline ... or per-period mean using
  lagged volatility quantile". The per-bucket modal generalises
  cleanly to 3-class (per-bucket class probabilities + marginal
  fallback for unseen buckets). Pairs with `RegimeClassifierTrainer`
  via `PREDICTOR_CLASS`.
- **Trainer enforces leakage discipline at `fit(...)`.** `feature_column`
  pointing at `regime_label`, `forward_log_return`, or
  `forward_log_return_vol` raises `ValueError`. A typo in the
  manifest fails fast rather than silently overfitting.
- **`split_strategy: time_aware_holdout` in the manifest.**
  Time-series data; eval is the latest 20 % of bars to mirror the
  live deployment shape. Walk-forward aggregation is filed as a
  follow-up.

## Deliverables

Code (stdlib only):
- [`ml/datasets/families/market_features.py`](../../ml/datasets/families/market_features.py)
  — `MarketFeaturesBuilder`. Reads a built `market_raw` dataset
  via `market_raw_path`. Configurable `vol_window_n`,
  `forward_window_m`, `vol_threshold`, `trend_threshold`,
  `n_vol_buckets`. Quantile bucketing of past vol; 3-class
  forward-window regime label.
- [`ml/datasets/registry.py`](../../ml/datasets/registry.py)
  — registers `MarketFeaturesBuilder`.
- [`ml/predictors/multiclass.py`](../../ml/predictors/multiclass.py)
  — `MulticlassPredictor` ABC.
- [`ml/predictors/per_bucket_multiclass.py`](../../ml/predictors/per_bucket_multiclass.py)
  — `PerBucketMulticlassPredictor`. Per-bucket class probabilities
  + marginal fallback.
- [`ml/trainers/regime_classifier.py`](../../ml/trainers/regime_classifier.py)
  — `RegimeClassifierTrainer`. Per-bucket modal counts → class
  probabilities; OOV class handling; leakage guard at fit-time.
- [`ml/evaluators/multiclass_classification.py`](../../ml/evaluators/multiclass_classification.py)
  — `MulticlassClassificationEvaluator`. Accuracy + per-class
  precision/recall/f1 + macro/weighted f1 + n_eval. Type-narrows
  to `MulticlassPredictor`.
- [`ml/configs/baseline-regime-classifier.yaml`](../../ml/configs/baseline-regime-classifier.yaml)
  — 3-class regime baseline manifest.

Builder framework auto-forward (mirror of PR 2A so PR 2B can
land without depending on the 2A merge):
- [`ml/datasets/builder.py`](../../ml/datasets/builder.py)
  — `DatasetBuilder.build` auto-forwards `symbol_scope` /
  `timeframe` into `iter_rows_kwargs` via `setdefault`.
- [`ml/datasets/families/market_raw.py`](../../ml/datasets/families/market_raw.py)
  — `MarketRawBuilder.iter_rows` translates them into adapter
  defaults.

Tests:
- [`tests/ml/datasets/test_market_features.py`](../../tests/ml/datasets/test_market_features.py)
  — 15 cases: schema, regime-labeling rule, edge-row dropping,
  log_return correctness, invalid windows, missing path, short
  input, full builder round-trip with `validate_dataset`,
  registry inclusion.
- [`tests/ml/test_regime_classifier.py`](../../tests/ml/test_regime_classifier.py)
  — 15 cases: trainer fit, leakage guard, OOV class handling,
  predictor label/proba/marginal fallback, evaluator metrics,
  evaluator's predictor-narrowing guard, perfect-eval = 1.0
  metrics check, end-to-end CSV→market_raw→market_features→
  trainer→evaluator pipeline.

Docs:
- [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md)
  — `market_features` row updated to "buildable"; owner subsystem
  updated.
- [`docs/data/dataset-schema.md`](../data/dataset-schema.md)
  — `market_features` per-field schema + leakage discipline
  section.
- [`docs/ml/training-center.md`](../ml/training-center.md)
  — predictor table extended; multiclass predictor surface;
  regime classifier end-to-end demo.
- [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
  — five-layer model + audit rows + Forbidden rule + change log.
- [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
  — PR 2B closed.
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) +
  [`ROADMAP.md`](../../ROADMAP.md) — ledgers + non-negotiable.
- This file.

## Acceptance

- [x] `market_features` family is buildable from a `market_raw`
  dataset. Forward-window labels guarantee no leakage.
- [x] `RegimeClassifierTrainer` fits on synthetic data and pairs
  with `PerBucketMulticlassPredictor` via `PREDICTOR_CLASS`.
- [x] Leakage guard at fit-time refuses forward / label columns
  as features.
- [x] `MulticlassClassificationEvaluator` reports accuracy +
  per-class metrics + macro/weighted f1 + n_eval, narrows to
  `MulticlassPredictor`.
- [x] `baseline-regime-classifier.yaml` manifest parses via
  `TrainingManifest.from_yaml`.
- [x] End-to-end pipeline (CSV → market_raw → market_features →
  trainer → evaluator) round-trips on a synthetic fixture.
- [x] Three regime classes (trend, range, volatile) are all
  represented in the synthetic fixture's output, validating the
  labeling rule.
- [x] Full `tests/ml/` suite green (131 tests).

## Out of scope (filed for follow-ups)

- Train-only quantile thresholds for `vol_bucket` (currently
  computed across the entire dataset; promotion-ready variants
  should freeze train-set thresholds into `model_state`).
- Aggregated walk-forward evaluation (averaging metrics across
  folds).
- Per-class precision-recall confusion-matrix artifact.
- `python -m ml.datasets publish` HF subcommand for
  `market_features`.
- Marginal-only sanity baseline (analogous to
  `baseline-trade-outcome-global.yaml`) so the operator can
  `compare` per-bucket against marginal-only.

## Hand-off

1. **Operator (off-VM build host)**: stage credentials per PR 2A's
   runbook, run the market_raw build, then chain the
   market_features build + the regime classifier train per
   `docs/ml/training-center.md` § "Regime classifier demo".
2. **Next sprint (S-AI-WS5-C)**: setup quality scorer.

## Live runtime impact

None. All changes are under `ml/` + `docs/`. Operator-hold paths
(`src/runtime/`, `src/units/accounts/`, `src/main.py`,
`config/accounts.yaml`, `deploy/*`) untouched. Manifest's
`target_deployment_stage: research_only` keeps the model out of
any live tier without operator approval.
