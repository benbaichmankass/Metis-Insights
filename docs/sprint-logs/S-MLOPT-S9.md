# Sprint Log: S-MLOPT-S9 (range-based volatility estimators)

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
M14 Phase 2.1 — the lowest-effort "better features" lever. Close-to-close
realized vol (`rolling_log_return_vol`) throws away each bar's intrabar
high/low/open, so it estimates vol inefficiently and reacts slowly — gap **G5**
("no range-based vol estimators"). Add the canonical **range-based volatility
estimators** to `market_features` so the regime heads can separate the
`volatile` class better, and let a regime manifest select the new vol feature.

The three label-distribution experiments (S6 CUSUM, S6-FU signal-log, S6-FU-2
backtest) converged on the same lesson — at n≈352 real trades, *better features*
may matter as much as a better label. S9 is the cheapest feature lever and is
independent of the labeling arc (it runs in parallel).

## Tier
- **Tier-1** for the estimator module, the `market_features` columns, and the
  tests (additive, read-only over built `market_raw`; past-only features →
  `leakage_test_status` stays `passed`). No `src/runtime/`, order-path, or live
  file touched.
- **Tier-3** for `ml/configs/btc-regime-1h-lgbm-yz-v1.yaml` and any promotion
  past `research_only` — operator-gated. The manifest ships at `research_only`;
  this sprint **proposes** + provides the A/B harness, the operator promotes.

## Starting Context
- M14 Phase 2 ("better features"), parallel to the Phase-1 labeling arc.
- `market_features` (the regime feature dataset) carried only a close-to-close
  `rolling_log_return_vol` + `vol_bucket`; the v2 LightGBM regime heads
  (`btc-regime-{5m,15m,1h}-lgbm-v2`) already expose a `vol_feature_column` knob
  (with `freeze_regime_spec`) — so swapping the vol feature is a one-line
  manifest change once the columns exist.

## Files and Systems Inspected
- `ml/datasets/families/market_features.py` (the past-window feature core +
  schema + leakage discipline), `ml/datasets/builder.py` (schema validation +
  `builder_version` is metadata-only, does not gate dataset path resolution),
  `ml/trainers/lightgbm_multiclass.py` (`vol_feature_column` + `freeze_regime_spec`
  — the live-scoring spec freeze), `ml/configs/btc-regime-1h-lgbm-v2.yaml` (the
  champion mirrored), `scripts/ops/build_trainer_datasets.sh` (builds BTCUSDT
  market_features at 1h/5m/15m daily — so the v3 rebuild is automatic).

## Work Completed
- **`ml/datasets/volatility_estimators.py` (new)** — four canonical range-based
  variance estimators over a window of OHLC bars: **Parkinson** (1980, high-low
  range), **Garman-Klass** (1980), **Rogers-Satchell** (1991, drift-independent),
  **Yang-Zhang** (2000, overnight-gap-aware, minimum-variance — the roadmap's
  headline). Each reads only the bars it's given (leakage-safe when fed a
  past-only window); best-effort on non-positive prices / short windows.
  `_sqrt_or_zero` converts a variance estimate to a stdev for feature emit.
- **`market_features` columns** — four new schema fields (`parkinson_vol`,
  `garman_klass_vol`, `rogers_satchell_vol`, `yang_zhang_vol`) computed over the
  **same inclusive past window** as `rolling_log_return_vol` (`[t-n+1 .. t]`),
  emitted as a stdev. `builder_version` bumped `v2 → v3` (metadata-only).
  Earlier baselines reading only `vol_bucket` / `rolling_log_return_vol` are
  unaffected by the wider schema.
- **`ml/configs/btc-regime-1h-lgbm-yz-v1.yaml`** *(Tier-3 proposal, draft)* — a
  clean A/B against `btc-regime-1h-lgbm-v2`: identical trainer / split / recency
  + class weighting / dataset, the ONLY change is the vol feature set (adds the
  four range estimators; freezes the live regime spec on `yang_zhang_vol`).
  Ships at `research_only`.
- **Tests** — `tests/ml/test_volatility_estimators.py` (Parkinson known-value;
  range-width / positivity / drift-independence ordering; Yang-Zhang term
  combination + the ≥2-usable-bars guard; non-positive/empty → `None`;
  `_sqrt_or_zero`) and `tests/ml/datasets/test_market_features.py` additions
  (the four columns present + non-negative on every row, in-schema, and the full
  build round-trips through `validate_dataset`; `builder_version == v3`).

## Validation Performed
- `pytest tests/ml/test_volatility_estimators.py
  tests/ml/datasets/test_market_features.py tests/ml/test_regime_classifier.py
  tests/ml/datasets/test_cli.py` → **all pass** (50 in the core set). `ruff`
  clean. The new regime manifest loads via `TrainingManifest.from_yaml`
  (`research_only`, `vol_feature_column: yang_zhang_vol`).
- **No-leakage by construction**: the estimators read only the inclusive past
  window `[t-n+1 .. t]` (same window as `rolling_log_return_vol`); the manifest's
  leakage gate still forbids `forward_log_return` / `forward_log_return_vol` /
  `regime_label`.

## Trainer-VM A/B — ⏳ pending
The headline number (rebuild `market_features` on `builder_version v3` →
`python -m ml compare btc-regime-1h-lgbm-v2 btc-regime-1h-lgbm-yz-v1` under the
Phase-0 CV → read the `f1_volatile` delta) runs on the trainer VM after the next
daily build (or an on-demand rebuild). Result will be appended to
`MB-20260603-004` and this section.

## Documentation Updated
- `docs/data/dataset-taxonomy.md` (market_features range-vol columns);
  `docs/architecture/ai-model-platform.md` change-log; `docs/ml/optimization-roadmap.md`
  Session 2.1; `ROADMAP.md` S-MLOPT-S9 row; `docs/claude/ml-review-backlog.json`
  (`MB-20260603-004`, the A/B eval follow-up); this sprint log.

## Risks and Follow-Ups
- **A/B eval is the open step** — the columns + estimators + manifest are
  shipped and locally verified; the `f1_volatile` lift number needs the v3
  `market_features` rebuild + the `compare` run (MB-20260603-004).
- **Other timeframes / symbols**: the columns land on every `market_features`
  build (1h/5m/15m BTC + MES), so 5m/15m YZ variants are a trivial follow-up if
  the 1h A/B is positive.
- **Garman-Klass / Rogers-Satchell per-bar terms can dip negative** on a single
  bar; the window-mean variance is clamped at 0 so the sqrt stays real (standard
  practice).
- **Tier-3 gate stands**: the manifest is a proposal at `research_only`;
  promotion past `shadow` is operator-gated.

## Next Recommended Sprint
- **S-MLOPT-S9 follow-up**: if the 1h A/B shows a positive `f1_volatile` lift,
  extend the YZ variant to 5m/15m + MES and propose promotion to `shadow`.
- **S-MLOPT-S2.3 crypto funding-rate + open-interest features** (Tier-1) — the
  next cheap, high-value, currently-unused feature family.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; manifest is a Tier-3 proposal.
- [x] Roadmap status checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns stated clearly (the A/B `f1_volatile` number is pending
      the v3 rebuild + compare run).
