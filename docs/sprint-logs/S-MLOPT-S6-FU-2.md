# Sprint Log: S-MLOPT-S6-FU-2 (backtest-labeled setup_candidates)

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
The next lever after the S-MLOPT-S6-FU honest negative. The signal-log eval
(#2716) ruled out the **event sampler** as the lever: swapping CUSUM momentum
events for the strategies' real decision points barely moved the synthetic train
win rate (0.457 → 0.469), and accuracy got *worse* (0.526 < the S6 CUSUM 0.670 <
the 0.756 majority baseline). The diagnosis: the **synthetic triple-barrier
LABEL** is the train↔eval domain gap, not the sampler — candle barriers assume
optimistic fills, no slippage, and no risk-manager filtering, so both synthetic
populations win ~0.46 while the real trades (which real execution + risk gating
filter the obvious winners out of) win 0.244.

Attack the label directly: use the strategies' **standalone backtest harnesses**
(`scripts/backtest_squeeze.py`, `backtest_fade.py`, `backtest_trend.py`,
`backtest_ict_scalp.py`, `src/backtest/run_backtest_vwap.py`) as the label source
for `setup_candidates`. Those harnesses already model real slippage + each
strategy's actual entry rule + per-strategy exit logic, so every backtest trade
is a real-distribution label at a real signal time. Port the S-MLOPT-S7
`backtest_recorder` pattern (source column + `include_backtest` flag) to
`setup_candidates`, then re-run the live_holdout eval with **backtest-train +
real-eval** — the apples-to-apples version of what the signal-log run
approximated.

Success: Tier-1 enabler shipped; new Tier-3 manifest proposed (`research_only`);
the eval evidence appended to `MB-20260603-003`.

## Tier
- **Tier-1** for: the `backtest_trades_db` event source in
  `setup_candidates.py`, the `record_harness_trades` recorder bridge, the tests,
  the backlog + doc updates. All additive, read-only against the DB
  (`is_backtest=1` rows only; the bridge writes a TEMP DB, never the money DB).
  No `src/runtime/`, order-path, or live file touched.
- **Tier-3** (operator-gated) for:
  `ml/configs/setup-candidates-metalabel-backtest-v1.yaml` and any promotion
  past `shadow`. The manifest ships at `research_only`; the PR is a draft. This
  sprint **proposes**, the operator approves.

## Starting Context
- S-MLOPT-S6 (CUSUM): meta-label acc 0.670 < 0.756 baseline on 352 real BTCUSDT
  trades (synthetic CUSUM win 0.457 vs real 0.244).
- S-MLOPT-S6-FU (signal-log, #2716): acc 0.526 — *worse* than CUSUM. Win rate
  0.469 ≈ CUSUM 0.457 ⇒ the event sampler is not the lever; the synthetic
  triple-barrier label is. `MB-20260603-002` RESOLVED-NEGATIVE.
- S-MLOPT-S7 already shipped `ml/datasets/backtest_recorder.py` (maps a
  `SimTrade`-shaped mapping → `is_backtest=1` trades row) and the
  `source`/`include_backtest` pattern on `trade_outcomes`/`setup_labels`. The
  S6-FU branch already carries the `event_source ∈ {cusum, signal_log, live}`
  schema column on `setup_candidates`. This sprint stacks on both.

## Files and Systems Inspected
- `ml/datasets/families/setup_candidates.py` (the event-sampling + emit core,
  the `_load_live_trades`/`_load_signal_log_events` readers, `_feature_fields`,
  `event_source` schema), `ml/datasets/backtest_recorder.py` (the S7
  mapper + schema-adaptive `write_backtest_trades`), `ml/datasets/families/
  trade_outcomes.py` (the S7 `source`/`include_backtest` reference shape),
  `ml/configs/setup-candidates-metalabel-{v1,siglog-v1}.yaml` (the manifests
  mirrored), `ml/datasets/cli.py` (`key=value` kwarg + bool coercion off the
  `iter_rows` signature), `scripts/ml/eval_split_compare.py` (the trainer-side
  eval driver), `scripts/backtest_{trend,squeeze,ict_scalp}.py` (the harnesses'
  `Trade` dataclasses + per-trade JSONL emit schema).

## Work Completed
- **`ml/datasets/families/setup_candidates.py`** — new `backtest_trades_db`
  kwarg reads `is_backtest=1` rows from the canonical `trades` table (the rows
  the S7 recorder writes from the standalone harnesses), locates each at the bar
  covering its entry `timestamp`, and emits a candidate in the same past-only
  feature space tagged `event_source: "backtest"`, `barrier_touched:
  "backtest"`, `is_live_trade: false` (train side of `live_holdout`), carrying
  the harness's **actual realized outcome** (`won` from the recorded R-proxy
  `pnl`; `r_multiple` recovered from `pnl`) — never a synthetic triple-barrier.
  `include_backtest=True` is the S7-style convenience: when no dedicated
  `backtest_trades_db` is given it reads the `is_backtest=1` rows from
  `live_trades_db` (the single-journal flow the S7 demo used).
  `backtest_strategies` optionally restricts to specific harness strategies.
  New `_load_backtest_trades` reader mirrors `_load_live_trades` (best-effort:
  missing DB / table / column → `[]`).
- **`scripts/ml/record_harness_trades.py` (new)** — the bridge that lets the
  standalone harnesses feed the source. The harnesses emit a per-trade JSONL
  (`{strategy, entry_time, direction, gross_r, net_r, confidence}`) whose keys
  differ from the recorder's `SimTrade` shape and which carries no `exit_ts`
  (the recorder requires one). `harness_row_to_sim_trade` normalises a harness
  row (prefers fee-adjusted `net_r` for `r_multiple`; `exit_ts` falls back to
  `entry_time`, since `setup_candidates` locates a backtest trade by its entry
  `timestamp` only) and `write_backtest_trades` persists `is_backtest=1` rows —
  reusing the exact S7 INSERT path + safety contract.
- **`ml/configs/setup-candidates-metalabel-backtest-v1.yaml`** *(Tier-3
  proposal, draft)* — meta-label manifest mirroring the S6 / S6-FU stack
  (`LightGBMRegressionTrainer → won`, `ClassificationEvaluator`,
  `live_holdout`, `research_only`, identical features + `forbidden_features`)
  but training on the backtest distribution. Promotion past `shadow` stays
  operator-gated.
- **Tests** — `tests/ml/test_setup_candidates.py` (8 new: backtest sampling +
  realized-outcome labeling, strategy filter, `include_backtest` single-DB
  reuse, missing-DB no-op, the pure backtest-train + real-eval split, the
  four-source mix, the comma-string CLI filter) and
  `tests/ml/test_record_harness_trades.py` (new: the mapper's net_r/gross_r
  preference + direction/strategy normalisation + skip rules, and the CLI
  recording `is_backtest=1` rows against the real NOT-NULL schema).

### Design note — `source` vs `event_source`
The S7 pattern names its train/eval discriminator `source` (`live`/`backtest`).
`setup_candidates` already has a `source` column meaning the *candle data
origin* (e.g. `bybit`), and already carries `event_source ∈ {cusum, signal_log,
live}` as the sampler discriminator (added in S6-FU). So the S7 `source` maps to
`setup_candidates`' **`event_source`**, extended here with `"backtest"` — not a
second overloaded `source` column. `is_live_trade` remains the boolean the
`live_holdout` splitter partitions on (backtest rows = `False` = train side).

## Validation Performed
- `pytest tests/ml/test_setup_candidates.py tests/ml/test_metalabel.py
  tests/ml/test_splitters.py` → **55 passed**;
  `tests/ml/test_record_harness_trades.py + test_backtest_labels.py` → all pass;
  full `tests/ml/` (ex. the 3 pandas-only modules absent from this sandbox) →
  **518 passed, 1 skipped**. `ruff` clean.
- Both manifests load via `TrainingManifest.from_yaml`,
  `target_deployment_stage: research_only`, **no feature/outcome leak**
  (`feature_columns ∩ forbidden_features = ∅`; `event_source` forbidden).
- **End-to-end build** (`SetupCandidatesBuilder().build(...)` with
  `backtest_trades_db` + `live_trades_db` + `include_cusum=false`):
  `leakage_test_status: passed`; rows partitioned backtest (train,
  `is_live_trade=False`) + live (eval, `is_live_trade=True`) — exactly the
  manifest's split.
- **Integration smoke**: harness JSONL → `record_harness_trades` →
  `is_backtest=1` rows → `setup_candidates` located + emitted them as
  `event_source="backtest"` with `won`/`r_multiple` recovered from the realized
  R. No-leakage holds by construction (backtest rows reuse `_feature_fields`
  past-only window; the label is the harness's realized outcome, not a
  forward-looking barrier).

## Trainer-VM eval — ⏳ pending (dispatched)
The headline number (build backtest-train + real-eval → train
`setup-candidates-metalabel-backtest-v1` → score the 352 real BTCUSDT holdout vs
the majority baseline) runs on the trainer VM, same flow as #2716:
1. record harness trades for BTCUSDT into a TEMP DB via
   `python -m scripts.ml.record_harness_trades`;
2. `python -m ml.datasets build setup_candidates ... -- backtest_trades_db=<tmp>
   live_trades_db=data/trade_journal.db include_cusum=false`;
3. `python -m ml train ml/configs/setup-candidates-metalabel-backtest-v1.yaml`
   via `.venv/bin/python`.
Result will be appended to `MB-20260603-003` `evidence_log` (and this section)
when it lands. The PR is **draft** until then.

## Documentation Updated
- `docs/claude/ml-review-backlog.json` — `MB-20260603-002` evidence line +
  status (superseded-by 003); new `MB-20260603-003` (in_progress) tracks the
  backtest-label lever + the pending eval.
- `docs/ml/optimization-roadmap.md` — Session 1.2 block: S6-FU eval negative +
  the new S6-FU-2 backtest-label follow-up.
- This sprint log. The PR body documents the change-set, tier split, test plan.

## Risks and Follow-Ups
- **Eval is the open step.** The enabler + bridge + manifest are shipped and
  locally verified; the headline real-holdout number is pending the trainer-VM
  run (MB-20260603-003).
- **Backtest fills are modeled, not live.** The harnesses model slippage +
  real entry/exit logic — closer to the real distribution than a synthetic
  triple-barrier, but still not live fills. Backtest rows are a *training*
  augmentation; the REAL trades remain the only `live_holdout` eval population
  (enforced by `is_live_trade`).
- **`pnl` is an R-proxy** for backtest rows (the recorder stores realized R in
  `pnl`); `won` / `r_multiple` are exact, dollar magnitude is not reconstructed
  — fine for the meta-label (`won`) target.
- **If the negative persists across all three label sources** (CUSUM,
  signal-log, backtest), the read is a data-scale floor at n≈352 real trades;
  the next lever is better features (S-MLOPT-S9 range-based vol estimators /
  Phase 2), not another label distribution.
- **Tier-3 gate stands**: the manifest is a proposal; the model is
  `research_only`; promotion past `shadow` is operator-gated.

## Next Recommended Sprint
- **S-MLOPT-S9 (2.1) range-based vol estimators** (Yang-Zhang, Garman-Klass,
  Tier-1). The three label-distribution experiments (CUSUM, signal-log,
  backtest) converge on the same lesson: at n≈352 real trades, *better features*
  may matter as much as a better label. S9 is the highest-leverage feature lever
  and is already enabler-ready.
- **S-MLOPT-S13 per-bar regime scoring** (Tier-2, live-runtime) remains the
  highest-leverage *regime* unblock — operator-gated, parked as its own arc.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; manifest is a Tier-3 proposal;
      the recorder bridge writes a TEMP DB (`is_backtest=1` only).
- [x] Roadmap status checked + updated (S6 follow-up on the closed S5–S8 block;
      the backlog item MB-20260603-003 tracks it).
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns stated clearly (the headline eval number is pending the
      dispatched trainer-VM run; the PR is draft until it lands).
