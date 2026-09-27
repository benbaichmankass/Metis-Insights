# CA-B01 — code audit of `ml/`

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Lane CA-B01 of the 2026-09-27 code audit · session `session_01M6NBQHKgKXsvrB3Wm5HAdW` · audited `origin/main` at `3d038c3` (2026-09-27 ~13:45Z) · read-only analysis. Nothing in code, config or live state was changed.

**The question:** does the code in `ml/` (210 files, ~24.4k lines) do what its docstrings, tests and the canonical docs say it does?

**Answer:** **54 open findings**: 0 critical, **8 high**, 27 medium, 19 low. All 8 highs are filed to the pipeline as `PI-20260927-3WM5HADW-0001` … `-0008`. The machine-readable version is [`CA-B01.findings.jsonl`](CA-B01.findings.jsonl), one object per line. The planted-input reproductions are in [`CA-B01-repros/`](CA-B01-repros/); they are `.py.txt` so no guard or collector picks them up. Run them with `PYTHONPATH=. python3 <file>`.

**Test suite:** `python3 -m pytest tests/ml -q` gave **1135 passed, 2 skipped**; the skips are the two torch trainers, because torch is not installed in the sandbox. Every high finding below reproduces with the suite still green. The tests do not pin these contracts.

## Live context (MEASURED 2026-09-27 13:31Z, `scripts/ops/diag_fetch.sh`)

- `shadow_stats` lists 32 registry models: **29 shadow and 3 advisory**. The advisory heads are `btc-regime-15m-lgbm-fc-pcv-v2`, `sol-regime-15m-lgbm-fc-pcv-v2` and `mes-regime-5m-lgbm-v2`.
- `log_coverage.oldest_retained = 2026-09-26T06:39:57Z`. **The active shadow log holds about 31 hours.**
- `conviction-meta-v1` is at **shadow** live, although its manifest says `candidate`. `btc-regime-{5m,15m,1h}-lgbm-yz-v1` are at shadow.
- `audit?limit=600` contains `regime_ml_vol_shadow` rows sourced from `ml-advisory:sol-regime-15m-lgbm-fc-pcv-v2`, so `REGIME_ML_VERDICT_MODE` is not `off`. I did **not** read whether it is `use` (real-money vol axis) this session; `docs/reference/env-vars.md` says BTC `use` has been live since 2026-06-28.

## High findings

| id | claim (short) | pipeline |
|---|---|---|
| `shadow-gates-read-active-log-only` | The #13146 rotation blind spot is **still live in every ml/ gate**: gate-check `drift_clean`, the stage-guard/readiness sweep, attribution, live_parity and drift-retrain all read the active log only. A planted case **passes** `drift_clean` truncated (KS 0.096) and **fails** on the full window (KS 0.571). The archives are already synced to the trainer and nobody reads them. | PI-20260927-3WM5HADW-0001 |
| `oos-edge-discards-manifest-purge-horizon` | `build_cv_config` overwrites the manifest's `label_horizon: 5` / `embargo_fraction: 0.01` with 1 / 0.0. This hits 19 manifests, including **both live advisory fc-pcv-v2 heads**, whose promotion `oos_edge` was measured with 4 bars of label overlap per fold boundary. | PI-20260927-3WM5HADW-0002 |
| `advisory-hold-when-nothing-measured` | An advisory head with drift=None and attribution=None gets "hold: no demote trigger tripped", the same verdict as a healthy measured head. "We did not look" reads as "fine". | PI-20260927-3WM5HADW-0003 |
| `multiclass-served-score-is-max-proba` | The logged shadow score is `max(proba)`, which drops the winning class. A near-certain loss (0.958) and a near-certain win (0.961) look the same. `conviction-meta-v1` (a `won` head) is live at shadow, so its attribution AUC/brier are computed on a score with no direction. | PI-20260927-3WM5HADW-0004 |
| `yz-vol-bucket-edges-wrong-basis` | yz heads freeze edges as the per-bucket max of YZ vol, but the trained bucket is close-to-close. The served bucket matches the trained one on only 53% of the model's own training bars (b2: 1657 → 67). Three of these heads are live at shadow. | PI-20260927-3WM5HADW-0005 |
| `regime-scoring-uses-forming-bar` | Per-bar regime scoring and the `xa_*` row are computed on the **still-forming** candle; training uses closed bars. The advisory heads' P(volatile) feeds `ml_vol_verdict`. | PI-20260927-3WM5HADW-0006 |
| `fc-live-no-staleness-check` | The served forecast row is merged at any age with no `as_of_ts` check. A dead producer freezes 6 of the 13 features of both advisory fc heads, and nothing alarms. | PI-20260927-3WM5HADW-0007 |
| `fc-producer-forecasts-forming-bar` | The live forecast producer forecasts from the forming bar (`out[-1]`, no trim, timer not aligned to bars); training forecasts use closed bars. | PI-20260927-3WM5HADW-0008 |

Four of the eight (`yz`, forming bar, and both fc findings) touch serving code in `src/runtime/` and `scripts/ml/` that consumes `ml/` output. They are listed here because they are the serving half of a train/serve parity question that starts in `ml/`. The fixes are **Tier 2** (runtime) and are for the manager and operator. This lane changed nothing.

**Severity caveat, stated plainly.** I rated nothing critical. The forming-bar and fc findings reach real money only if `REGIME_ML_VERDICT_MODE=use` **and** an OFF cell exists for the BTC `(trend, vol)` pair. I established the first only as "not off" (from the audit rows), and I did not check the second. If both hold, `regime-scoring-uses-forming-bar` and `fc-*` are money-at-risk today and should be re-rated critical.

## Medium (27) and low (19)

See the jsonl for the full records. The main themes:
- **More rotation blind spots and log handling:** the CLI `shadow-*` commands, plain+`.gz` double-counting in `rotated_log_paths` (the fixed endpoint double-counts too), a truncated `.gz` raising out of `iter_records` (which turns the fixed endpoint into a 500), live_parity false dead-feature, and a missing log collapsed into `skip_thin_data`.
- **Gates that pass on nothing:** `drift_clean` passes on n=1 vs n=1, `sample_sufficiency` passes with 0 live trades on offline n_eval, demote triggers fire on n=1, and the nightly audit exception is reported as `OK`. The audit also misses 98.5%-zero features and frozen tails.
- **Registry / factory robustness:** non-atomic registry writes (one bad file blinds `list()`), a constructor error dropping every head in `resolve_predictors`, `register()` accepting `advisory` directly (latent), and `promote-stage` needing no gate packet (low, Tier 3).
- **Dataset provenance / leakage:** `exit_candidates` and `conviction_meta` admit paper rows as live, `setup_labels_audit` matches future audit events and fabricated pnl, backtest rows are located on the previous day (string bisect), live rows use the forming bar, `live_holdout` has no temporal/twin separation, FRED H.10 has a weekly-release look-ahead, and macro missing values become level 0.0.
- **Modelling:** the ADWIN bound assumes [0,1] (20/20 false drift on N(5,3)), the HMM filter resets on a second read, the HMM has no regime_spec, the fc stride carry-forward skews train vs serve, the calibrator report is in-sample, and `time_aware_holdout` has no purge (44 manifests).

## What I read

Five parallel sub-lanes, one question each. I spot-verified every high myself (each repro re-run, or the quoted lines re-read) before writing it.

- **Read fully:** `ml/promotion/{gates,live_parity,attribution,stage_guard,readiness_report}.py`, `ml/shadow/{__init__,adwin,backfill,drift,drift_retrain,factory,inspector}.py`, `ml/registry/{model_registry,reconcile}.py`, `ml/manifest.py`, `ml/predictors/{lightgbm,shadow,constant,multiclass,causal_hmm_regime,per_group}.py`, `ml/experiments/{splitters,runner}.py`, `ml/trainers/{lightgbm_multiclass,causal_hmm_regime,per_strategy_winrate}.py`, `ml/evaluators/*.py`, `ml/calibration/{fit,regime_alignment}.py`, `ml/datasets/{cross_asset_features,forecast_features,macro_features,audit,validate,corpus_embedding_features,corpus_store}.py`, `ml/datasets/adapters/{bybit_offvm,bybit_funding_oi,fred_macro,yfinance_offvm,yfinance_macro}.py`, `ml/datasets/labeling/triple_barrier.py`, `ml/datasets/families/{setup_candidates,market_features,exit_candidates,conviction_meta,setup_labels}.py`.
- **Read partly:** `ml/cli.py` (the shadow, gate-check, train and compare paths), `ml/promotion/oos_edge.py`, `ml/trainers/{lightgbm_regression,sample_weights,ssl_corpus_encoder,torch_sequence,regime_classifier}.py`, `ml/predictors/per_bucket_multiclass.py`, `ml/calibration/calibrators.py`, `ml/datasets/{funding_oi_features,continuous}.py`, `ml/datasets/adapters/{ibkr_offvm,fred_corpus,yf_symbols}.py`, `ml/datasets/families/{trade_outcomes,setup_labels_audit,account_context,execution_quality,review_journal,corpus_panel,market_raw}.py`, and `ml/configs/*` (split, stage and feature fields parsed for all 79).
- **Not read:** `ml/promotion/checklist.py`, `ml/predictors/{torch_sequence,ssl_corpus_encoder}.py`, `ml/trainers/{onnx_export,constant_baseline}.py`, `ml/datasets/{embedding_features,orderflow_features,sequence_window,backtest_recorder (partly),builder,cli,percontract_pull,metadata,registry}.py`, `ml/datasets/adapters/{resample,csv}.py`, `ml/datasets/families/{market_sequences,backtest_results}.py`, `ml/datasets/labeling/trend_regime.py`, `ml/offload-inbox/**`, and test-file bodies beyond grep/run. The torch trainers were not executed.

**Behavioural coverage:** 8 of 8 highs and most mediums were reproduced by running the actual `ml/` code on planted input. Live state was read for model stages, log coverage and the vol-verdict mode. **No finding was reproduced on the trainer VM's real datasets.**

## What I could not settle

1. Whether `REGIME_ML_VERDICT_MODE=use` holds in the live process, and whether a BTC OFF cell exists. Read the env with `scripts/ops/get_env.py` via the relay. **This decides whether the forming-bar and fc findings are critical.**
2. The size of the `oos_edge` optimism: re-run `gate-check` for the two advisory fc heads with `--label-horizon 5 --embargo-fraction 0.01` on the trainer.
3. The live served forecast's age: compare the `as_of_ts` in `runtime_logs/trainer_mirror/forecasts/*.json` with the bar timestamps.
4. Whether the three live yz heads' real registered edges are as skewed as the planted case.
5. Whether the trainer already holds plain+`.gz` duplicate archives (`ls runtime_logs/shadow_predictions.*` via trainer-vm-diag).
6. Whether a `regime_alignment` calibrator section ships in `calibrators.json`. If one does, `c_reg` is live on max-proba scores.
7. Why `conviction-meta-v1` is at shadow in the registry when its manifest says candidate. I did not trace the promotion record.
8. The twin count and the real-data inflation of the `live_holdout` / M23 EV-gate metrics.
