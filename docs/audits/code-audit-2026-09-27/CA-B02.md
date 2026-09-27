# CA-B02 — code audit of `scripts/ml/` + `scripts/training/`

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Lane CA-B02 of the 2026-09-27 code audit · audited `origin/main` at `cf51e0b` (2026-09-27 ~14:20Z) · read-only analysis. Nothing in code, config or live state was changed.

**The question:** does the code in `scripts/ml/` (72 files, ~16.0k lines) and
`scripts/training/` (3 non-`__init__` files, ~310 lines) do what its
docstrings, tests and the repo's docs (`CLAUDE.md`, `model-training`,
`ml-review`) say it does?

**Scope note.** `scripts/ml/` is the CLI/orchestration layer around the `ml/`
package; sibling lane CA-B01 (merged, `docs/audits/code-audit-2026-09-27/CA-B01.md`)
already audited `ml/` itself and, because several `scripts/ml/` files are the
serving-side half of a train/serve question that starts in `ml/`, already
filed findings that name `scripts/ml/publish_live_forecasts.py`,
`scripts/ml/fit_confidence_calibrators.py`, `scripts/ml/hpo_sweep.py`, and
`scripts/ml/replay_pregate_live.py` by line number. This lane did not re-derive
those; see "What I read" below for exactly which files that covers.

**Answer:** **4 findings**: 0 critical, **2 high**, 1 medium, 1 low. Both highs
are filed to the pipeline as `PI-20260927-CAB02-0001` and `-0002`. The
machine-readable version is [`CA-B02.findings.jsonl`](CA-B02.findings.jsonl),
one object per line. Planted-input reproductions are in
[`CA-B02-repros/`](CA-B02-repros/) as `.py.txt` (so no guard or collector picks
them up) — run with `PYTHONPATH=. python3 <file>`.

**Test suite.** `python3 -m pytest tests/ml -q` gave **1135 passed, 2 skipped**
(matches CA-B01's count exactly), after installing `requirements-test.txt` +
`pandas`/`numpy`/`scikit-learn`/`lightgbm`/`fastapi` into the sandbox (none of
these were preinstalled here). The 2 skips are the torch trainers (torch not
installed). `tests/scripts/*` and the scattered `tests/test_exit_head_*`,
`tests/test_strategy_tune_sweep.py`, `tests/test_trend_harness_levers.py` etc.
that exercise files under this lane's scope: **400 passed, 3 failed**
(`tests/scripts/test_llm_delegate_scope.py` — missing `httpx` in the sandbox,
outside this lane's scope, not investigated further). **Every finding below
reproduces with the relevant test suite still green** — none of these tests
pin the contracts the findings break.

## High findings

| id | claim (short) | pipeline |
|---|---|---|
| `training-harness-swallows-signal-errors-as-zero-trades` | `scripts/training/backtest_helpers.py:simple_backtest()` swallows every `build_signal()` exception and renders it byte-identically to a legitimate "no setup" result (both: `trades=0, expectancy_r=0.0, ...`). The autonomous `training-run.yml` → `run_experiment.py` pipeline renders this as a clean `OK` row in `SUMMARY.md`, which a Stage-4 reviewing session (per `docs/claude/training-improvement-workflow.md`) reads to decide adopt/reject per hypothesis. `scripts/training/**` has **zero** test coverage. | `PI-20260927-CAB02-0001` |
| `fc-geometry-join-ignores-strategy` | `scripts/ml/fc_geometry_resolve.py:_join_real_r()` joins the "real" realized-R arm of the fc-geometry live-vs-counterfactual comparison by `(symbol, nearest timestamp)` only — no `strategy_name` filter — although 9 distinct strategies trade `BTCUSDT` alone (measured from `config/strategies.yaml`) and each can close a trade inside the same 900s window. Reproduced: a synthetic two-strategy fixture returns the wrong strategy's R (`-3.0` instead of the correct `+2.0`). Corrupts the evidence for the live SL/TP-geometry design question this script exists to answer (`MB-20260705-FC-SLTP-GEOMETRY`). No test file exists for this script. | `PI-20260927-CAB02-0002` |

## Medium (1) and low (1)

- **`m23-ev-gate-in-sample-threshold-selection`** (medium) —
  `scripts/ml/m23_ev_gate.py` sweeps ~50 thresholds over the same held-out
  cohort it reports metrics on, and its `best`-threshold pick is the max of
  that same sweep — the identical in-sample selection-bias shape CA-B01 rated
  **low** in `hpo_sweep.py` (0 adopted manifests there). Here the shape feeds a
  verdict (`SELECTION EV POSITIVE/NOT POSITIVE`) that 6 committed
  `docs/research/M23-*.md` memos and one sprint-log status review cite
  directly as GO/NO-GO evidence on a real promotion candidate
  (`setup-candidates-metalabel-backtest-v1`). Quantified via a 300-trial
  Monte-Carlo repro: a scorer with **zero** real predictive skill (uniform
  random probability, independent of the R outcome) still "beats take-all" by
  the in-sample-max threshold in **300/300** trials, and clears **both**
  `beats_takeall` **and** the usable-volume floor in **3/300 (1%)** trials —
  purely from evaluating many thresholds against one dataset with no held-out
  split for the threshold choice itself. Every verdict observed in committed
  docs to date reads conservatively ("below usable-volume floor / NOT (yet)"),
  so this is a methodology finding about the gate's bias direction, not a
  claim that a specific past verdict was wrong. Not filed to the pipeline —
  same low-urgency class already tracked via the CA-B01 `hpo_sweep` finding,
  0 GO verdicts produced by this gate to date.
- **`recurring-in-sample-sweep-selection-class`** (low) — the same shape
  recurs a third time in `scripts/ml/window_recency_sweep.py` (a "best config"
  reported from one fixed holdout, no caveat in the docstring/CLI output), and
  a fourth, purely cosmetic time in `scripts/ml/train_entry_head.py:216-217`
  (a per-fold `best_skip` **print line** only — the actual walk-forward gate
  fixes `tau` across folds correctly and never reads this value). Recorded for
  the record per `CLAUDE.md`'s recurrence-class discipline; not filed.

## What I read

- **Read fully:** `scripts/training/{run_experiment,data_loader,backtest_helpers}.py`;
  `scripts/ml/{gate_check_candidates.sh,evidence_floor_report.py,m23_ev_gate.py,
  fc_geometry_resolve.py,record_harness_trades.py,export_entry_head.py,
  export_exit_head.py,train_entry_head.py,backfill_dataset_vol_threshold.py,
  window_recency_sweep.py}`; `scripts/ml/gpu_burst/{runpod_burst.py,_remote.py}`
  (SSH-argv construction, teardown-guarantee `finally`, pod-scope guard).
- **Read partly (targeted grep + spot-read of the risk-relevant functions):**
  `scripts/ml/strategy_review_packet.py` (1814 lines — the `decide()`
  neighborhood, `tuning_attempt_on_record`, `compute_execution_diagnostics`;
  did not read the full file), `scripts/ml/strategy_tune_sweep.py` (1156
  lines — `_data_provenance`, `run_sweep` header, the JSON-extraction helper),
  `scripts/ml/offload/emit_bundle.py` (grep for error handling + self-test
  presence only).
- **Not read (or grep-only):** `scripts/ml/{_labeling_gap_probe,_rg3_print,
  _rg4_print,_verify_score_semantics,_regime_score_semantics,
  _feature_parity_probe,build_calibration_corpus,build_corpus_embeddings,
  build_cross_asset,build_embeddings,build_forecasts,eval_split_compare,
  fetch_fred_corpus,fetch_fred_macro,fetch_funding_oi,fetch_macro,
  fit_regime_alignment_calibrators,orderflow_capture,pregate_stream,
  replay_pregate,replay_pregate_fleet,replay_pregate_summary,
  spike_a_pooled_labels,sweep_conviction_weights,backtest_augment_runner,
  build_exit_head_dataset,train_exit_head,eval_split_compare,
  walkforward_cell_selection,build_eth_finetf_datasets.sh,gate4_vol_threshold_run.sh,
  fleet_scorecard.sh,m23_phase1_*.sh,m23_phase2_*.sh,rg4_*.sh,
  train_and_rg3_eth_finetf.sh,verify_optionA_gate.sh,walkforward_*.sh}`,
  `scripts/ml/offload/drain_inbox.py`, `scripts/ml/gpu_burst/{ingest_bundle,preflight,record_run}.py`.
  These were left unread because CA-B01's file-count/line-count precedent (a
  ~24k-line package in one lane) and this lane's own budget meant a full read
  of every one of 72 files was not achievable at the depth needed to trust a
  finding; the ones read were chosen by name-relevance (promotion/gate
  evidence, live-artifact export, remote execution) per the audit's own
  stated ordering (money-at-risk / promotion-adjacent first).
- Four files already carry **CA-B01** findings and were deliberately not
  re-derived here: `scripts/ml/publish_live_forecasts.py` (forming-bar +
  staleness), `scripts/ml/fit_confidence_calibrators.py` (in-sample
  brier/ECE), `scripts/ml/hpo_sweep.py` (in-sample threshold selection —
  the same class as this lane's `m23_ev_gate` finding), `scripts/ml/replay_pregate_live.py`
  (a `SystemExit` escaping a "never raises" docstring).

**Behavioural coverage:** all 4 findings were reproduced by running the actual
code (or a fixture built from its real query/logic) on planted input; none
were reproduced against live trainer-VM data or the live shadow log — this
lane did no diag/VM reads at all (offline-only, matching its Tier-1 scope).
The 9-strategies-on-BTCUSDT population claim for the `fc_geometry_resolve.py`
finding was measured directly from `config/strategies.yaml` on `main`, not
assumed.

## What I could not settle

1. Whether any fc-geometry soak row currently on the trainer VM has actually
   been mis-attributed by the `_join_real_r` bug (this lane did not pull
   `runtime_logs/fc_geometry_soak.jsonl` from the live/trainer side) — the
   finding is a code-level reproduction, not a measurement against real soak
   data.
2. Whether the `simple_backtest` zero-trades collapse has ever actually fired
   on a real `hypotheses.py` run: only 2 `experiments/*/hypotheses.py` exist
   in the repo (both 2026-05, no committed zero-trade result found in
   `experiments/*/results/*/metrics.json`), so the finding is about the code's
   behavior under a planted defect, not a claim that a past run was
   miscategorized.
3. A line-by-line read of `scripts/ml/strategy_tune_sweep.py` and
   `scripts/ml/strategy_review_packet.py` beyond the neighborhoods above —
   both are large, carry extensive existing collapsed-state discipline in the
   parts read, and were not fully read for time/budget reasons.
4. The ~45 files listed as "not read" above received no attention at all
   beyond being named in a directory listing; a full pass on those (backfill
   scripts, the `offload/`+`gpu_burst/` ingest/preflight/record-run trio, the
   shell-script sweeps) is unstarted.

---
_Session budget target: ~$30. Read-only analysis; nothing in code, config, or
live state was changed by this lane._
