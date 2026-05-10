# WS5 — Baseline models

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 🔄 IN PROGRESS — sub-sprints A + B-Part-1 + B-Part-2 (PR 2A + PR 2B) closed 2026-05-10.

## Decomposition

WS5 lands as per-baseline sub-sprints. Larger baselines (WS5-B)
further decompose into Part-1 (data prereq) + Part-2 (the
classifier).

| Sub-sprint | Baseline | Dataset prereq | Status |
|---|---|---|---|
| **S-AI-WS5-A** | Outcome probability (per-strategy historical winrate) | `trade_outcomes` | ✅ DONE 2026-05-10 |
| **S-AI-WS5-B-PART-1** | `market_raw` multi-source adapter framework + CSV adapter + Bybit off-VM scaffold | (this sprint adds the prereq builder) | ✅ DONE 2026-05-10 |
| S-AI-WS5-B-PART-2 PR 2A | Bybit off-VM `_fetch_bars` wiring (ccxt) | `market_raw` (now buildable via CSV + Bybit off-VM) | ✅ DONE 2026-05-10 |
| S-AI-WS5-B-PART-2 PR 2B | 3-class regime classifier + `market_features` family + multiclass evaluator | `market_features` (this PR adds it) | ✅ DONE 2026-05-10 |
| S-AI-WS5-C | Setup quality scorer | `setup_labels` | 📋 queued |
| S-AI-WS5-D | Execution quality | `trade_outcomes` + execution metadata | 📋 queued |
| S-AI-WS5-E | Post-trade review | `review_journal` | 📋 queued |
| S-AI-WS5-F | Prop mission policy | `account_context` | 📋 queued |

## S-AI-WS5-A — Outcome probability (closed)

Closed 2026-05-10. See
[`docs/sprint-logs/S-AI-WS5-A.md`](../../sprint-logs/S-AI-WS5-A.md).

Paired sanity baseline
[`baseline-trade-outcome-global.yaml`](../../../ml/configs/baseline-trade-outcome-global.yaml)
shipped in S-AI-WS4-FU.

## S-AI-WS5-B-PART-1 — `market_raw` adapter framework (closed)

Closed 2026-05-10. Pluggable upstream-source adapters that
normalise into the canonical `market_raw` row shape.

Deliverables:
- [`ml/datasets/adapters/`](../../../ml/datasets/adapters/) —
  `MarketRawAdapter` ABC + canonical row + adapter registry.
- `CsvMarketRawAdapter` — reads operator-staged CSVs (no network,
  no creds; the test/local adapter).
- `BybitOffvmMarketRawAdapter` — **off-VM only.** Refuses to run
  unless `ICT_OFFVM_BUILD_HOST=1` is set; the actual exchange
  call is a documented `NotImplementedError` filed for the
  operator to wire next.
- [`ml/datasets/families/market_raw.py`](../../../ml/datasets/families/market_raw.py)
  — builder dispatching to adapters by name.
- [`tests/ml/datasets/test_market_raw.py`](../../../tests/ml/datasets/test_market_raw.py)
  — CSV round-trip + bybit env-gate + registry inclusion + env-var
  name pin.
- Docs:
  [`docs/ml/market-raw-adapters.md`](../../ml/market-raw-adapters.md) +
  taxonomy + schema updates + sprint log.

The operator picked off-VM bybit as the first concrete adapter
(2026-05-10). The framework + env-gate ship in this sprint; the
actual exchange call is filed for the operator to wire on a
non-VM host with read-only credentials. See
[`docs/ml/market-raw-adapters.md`](../../ml/market-raw-adapters.md)
§ “Bybit off-VM wiring”.

## S-AI-WS5-B-PART-2 PR 2A — Bybit off-VM fetch wiring (closed)

Closed 2026-05-10. Operator chose to split Part 2 into two PRs to
keep each reviewable in isolation. PR 2A wires only the
`BybitOffvmMarketRawAdapter._fetch_bars(...)` path.

Deliverables:

- `ml/datasets/adapters/bybit_offvm.py` — live ccxt fetch path.
  Lazy-imports `ccxt`; constructs a `ccxt.bybit` client via the
  `_build_exchange` classmethod (tests monkeypatch this hook).
  Pagination by `since` (ms) over `[start, end]`; bar-length-in-ms
  map covers `1m`/`5m`/`15m`/`1h`/`4h`/`1d`. Defensive guards
  against pre-`start` bars + stale `since` + end-window cutoff.
- `ml/datasets/builder.py` + `ml/datasets/families/market_raw.py`
  — `DatasetBuilder.build` auto-forwards `symbol_scope` /
  `timeframe` into `iter_rows` kwargs (operator-supplied wins via
  `setdefault`); `MarketRawBuilder.iter_rows` translates them
  into adapter `symbol` / `timeframe` defaults.
- Tests: 7 wiring cases against a fake exchange.
- Docs: `docs/ml/market-raw-adapters.md` Bybit section refreshed.
- Sprint log: `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2A.md`.

The env-gate (`ICT_OFFVM_BUILD_HOST=1`) was NOT weakened.

## S-AI-WS5-B-PART-2 PR 2B — Regime classifier baseline (closed)

Closed 2026-05-10. 3-class regime classifier (operator picked
"3-class trend / range / volatile" over "binary high/low-vol")
on a new derived `market_features` family (operator picked
"new family" over "extend `market_raw`").

Deliverables:

- `ml/datasets/families/market_features.py` — derived family;
  reads a built `market_raw` dataset from `market_raw_path`,
  emits `log_return`, `rolling_log_return_vol` (past window),
  `vol_bucket` (quantile of past vol), `forward_log_return` +
  `forward_log_return_vol` (forward window), and a 3-class
  `regime_label`. Forward-window labels guarantee no leakage by
  construction; metadata stamps `leakage_test_status: passed`.
- `ml/predictors/multiclass.py` — `MulticlassPredictor` ABC
  (extends `Predictor` with `predict_label` + `predict_proba`).
- `ml/predictors/per_bucket_multiclass.py` —
  `PerBucketMulticlassPredictor`. Per-bucket class probabilities
  with marginal fallback for unseen buckets.
- `ml/trainers/regime_classifier.py` — `RegimeClassifierTrainer`.
  Per-bucket modal class; refuses (`ValueError`) at `fit(...)`
  if the operator points `feature_column` at any forward / label
  column. Pairs with `PerBucketMulticlassPredictor` via
  `PREDICTOR_CLASS`.
- `ml/evaluators/multiclass_classification.py` —
  `MulticlassClassificationEvaluator`. Accuracy + per-class
  precision/recall/f1 + macro-F1 + weighted-F1 + n_eval. Narrows
  to `MulticlassPredictor`; raises `TypeError` against any other
  predictor.
- `ml/configs/baseline-regime-classifier.yaml` — manifest with
  `split_strategy: time_aware_holdout` and `time_column: ts`.
- `tests/ml/datasets/test_market_features.py` (15 cases) +
  `tests/ml/test_regime_classifier.py` (15 cases).
- Docs: `dataset-taxonomy.md`, `dataset-schema.md`,
  `training-center.md`, `ai-model-platform.md`,
  `AI-TRADERS-ROADMAP.md`, `ROADMAP.md`. Sprint log:
  `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2B.md`.

## Acceptance (per baseline)

- [ ] Each baseline has a dataset, trainer, evaluator, summary.
- [ ] No advanced model family is introduced before a baseline
  exists for the same task.
- [ ] Each baseline produces decision-useful metrics, not only
  generic ML metrics.
