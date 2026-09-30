# AUD-4 — ML serving fidelity (E75 system audit, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane AUD-4, dispatched by AUD-LEAD. Findings: `AUD-4.findings.jsonl` (schema-complete, one object per line).

## 1. Question and answer

**Question.** For every model at shadow stage or above, does the live feature-serving path produce the same feature values as training-time computation on real recent rows? And does any gate other than `drift_clean` read only the active shadow log?

**Answer (partial).** *Value-level parity on real rows was NOT established*: the trainer-VM job that computes per-feature train-vs-live distributions (#14064) never returned within this lane's window. What WAS established is **presence-level** parity, from the live `/api/bot/shadow/stats` `row_keys_seen` against each registry manifest's `feature_columns` (32 shadow+ models; 23 declare features). 19 declared-feature models serve every declared feature; **4 do not**: `conviction-meta-v1` (7/10 missing), `setup-quality-lgbm-v2` (4/9), `btc-regime-1h-lgbm-funding-svble-v1` (3/10, no producer in `src/`), `btc-regime-5m-lgbm-flow-v1-offload` (6/13, no producer in `src/`). All four are shadow-stage, so no money path is touched. **Second rotation-blind gate: yes** — `labels_accruing` (required for regime heads) and the RG4 live AUC read the active log only via `scripts/ml/replay_pregate_live.py:225`; FIX-CA-20 did not cover it. FIX-CA-24 is observed working live (fc_stale=0, lag 0). FIX-CA-23 criticality (`REGIME_ML_VERDICT_MODE`) is **unresolved** (#14065 never returned).

## 2. Findings

| id | severity | tier | claim |
|---|---|---|---|
| SA-AUD-4-conviction-meta-serves-3-of-10-features | medium | 1 | conviction-meta-v1 (stage shadow) is scored on a 6-key signal-time row that lacks 7 of its 10 declared feature_columns (c_strat,c_setup,c_wr,c_reg,adx_14,regime,vol_regim… |
| SA-AUD-4-setup-quality-lgbm-v2-serves-5-of-9-features | low | 1 | setup-quality-lgbm-v2 (shadow) is scored on a row missing 4 of its 9 declared features (bias, account_id, hour_of_day, dayofweek). |
| SA-AUD-4-funding-svble-head-funding-features-never-served | medium | 1 | btc-regime-1h-lgbm-funding-svble-v1 (shadow, described as 'serveable') has no live producer for its 3 funding features (funding_rate, funding_rate_zscore, funding_rate_ab… |
| SA-AUD-4-flow-offload-head-orderflow-features-never-served | medium | 1 | btc-regime-5m-lgbm-flow-v1-offload (shadow) scores with 6 of 13 declared features (ofi, ofi_zscore, vpin, order_imbalance, rel_spread_mean, microprice_dev) absent in ever… |
| SA-AUD-4-labels-accruing-gate-active-log-only | high | 1 | A second promotion gate shares the rotation-window blind spot: the REQUIRED labels_accruing gate (and the advisory live_regime_discrimination AUC) is computed by replay_p… |
| SA-AUD-4-fleet-rg4-scorecard-scripts-active-log-only | low | 1 | scripts/ml/fleet_scorecard.sh, rg4_targeted.sh, rg4_vt_sweep.sh and gate_check_candidates.sh pass the active shadow log only (the default runtime_logs/shadow_predictions.… |
| SA-AUD-4-api-shadow-predictions-and-promotion-clock-active-only | low | 1 | Three API/CLI surfaces still read the active log alone: GET /api/bot/shadow/predictions (shadow.py:204), the soak/promotion-clock endpoint (shadow.py:341, which discloses… |
| SA-AUD-4-two-shadow-heads-never-scored | low | 1 | mes-setup-quality-baseline-v0 and mes-trade-outcome-winrate-baseline-v0 are registered at shadow but have zero logged predictions in the active log and archives. |
| SA-AUD-4-fix-ca-20-21-22-deployed-trainer | info | 1 | NON-ISSUE: FIX-CA-20/21/22 are merged AND present on the trainer VM's checked-out code. |
| SA-AUD-4-fix-ca-24-observed-live | info | 1 | NON-ISSUE: FIX-CA-24 (forecast staleness check) is deployed and OBSERVED working on the live trader for both advisory fc-pcv-v2 symbols. |
| SA-AUD-4-live-trader-restart-pending | info | 1 | NON-ISSUE (with caveat): the live trader runs git b9d518516 while disk is at 11b9e688a with restart_pending=true; both post-date the FIX-CA-23/24/25 merges (#13253, 2026-… |

Counts: {'medium': 3, 'low': 4, 'high': 1, 'info': 3}. No critical.

## 3. Coverage

**Behavioural.** Exercised against real data: public registry mirror (96 rows, all 32 shadow+ models' manifests), `/api/bot/shadow/stats` (34 records, active log n=15,721), `/api/bot/shadow/drift` for 6 models (archive-inclusive path), live-VM diag `version` + `shadow_stats` (#14066), trainer diag inventory (#14055) and another session's deploy-verification output (#14044, read not run). Capabilities asserted but not exercised: value-level train/serve parity (0 of 32 models), gate-check/`labels_accruing` behaviour after rotation (code-read only), FIX-CA-21/22 behaviour (signatures only), FIX-CA-23/25 live effect.
**Reading.** Read: `ml/promotion/live_parity.py` (lines ~197-430), `ml/cli.py` gate-check and shadow commands, `scripts/ml/replay_pregate_live.py` (head + run), `ml/shadow/inspector.py` (archive union), `src/web/api/routers/shadow.py`/`trade_scores.py` readers (excerpts), `src/runtime/advisory_sizing.py`, `strategy_signal_builders.py` (shadow emit), `scripts/ops/sync_trainer_data.sh` (archive sync), CA-B01 findings + FIX-CA-20..25 text. Explicitly NOT read: model trainers, `regime_bar_scoring.py` internals, `forecast_live.py`/`publish_live_forecasts.py` bodies, ml_vol_verdict decision path, `CA-B01.md` narrative.

## 4. Could not look

- **#14064** trainer parity job (run 36552262919) started 09:55Z, no comment by 10:11Z; cause unknown (size/timeout unverified). Script preserved in this session's scratchpad only; re-run needed.
- **#14065** `get-env REGIME_ML_VERDICT_MODE`: no result by 10:11Z; the value is unknown, so FIX-CA-23 severity stays unrated.
- Feature *values* on the live VM (public API strips `feature_row`); the live relay is fixed-curl only.

## 5. Spend

Roughly 50 tool calls, 4 relay issues (#14055, #14064, #14065, #14066), no builds. Estimated well under the $30 ceiling; the meter is not readable.
