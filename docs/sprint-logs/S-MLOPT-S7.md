# Sprint Log: S-MLOPT-S7

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
- Primary goal: manufacture more labeled decision-model training data from
  **backtests** — have the backtest harnesses' per-trade results flow into the
  `setup_labels` / `trade_outcomes` families tagged `source=backtest`, so the
  decision models train on live+backtest while evaluating on a **REAL-trade
  holdout only**. Directly enlarges the sparse real population the S-MLOPT-S6
  live-holdout eval was starved by (and gap G4 generally). Closes MB-20260530-001.

## Tier
- **Tier-1.** The family change is a pure read (surfacing the existing
  `is_backtest` column). The recorder is opt-in tooling: it writes **only
  `is_backtest=1` rows**, which every live/stats/default-dataset path already
  excludes (`is_backtest=0` filter) — the same contract the M5 `backtest_results`
  writer relies on — and it never runs against the production DB autonomously
  (a caller passes the `db_path`). No `src/runtime/`, order-path, or live file
  touched.

## Starting Context
- M14 Phase 1.3, builds on S-MLOPT-S5/S6. The S6 live-holdout eval returned an
  honest negative (meta-label lost to baseline on 352 real BTCUSDT trades) — the
  diagnosis was too little / too-unrealistic training data vs the real
  population. S7 is one of the levers: backtest trades follow real strategy
  logic (unlike the S5 CUSUM synthetic candidates), so they are a more faithful
  augmentation source.
- The `sim/` engine already produces per-trade `SimTrade` records
  (entry/sl/tp/exit/r_multiple); the `trades` table already carries an
  `is_backtest` column — the schema anticipated exactly this. No harness
  currently writes per-trade rows to the DB, so S7 supplies the writer + the
  family read path.

## Files and Systems Inspected
- `ml/datasets/families/{trade_outcomes,setup_labels}.py` (the `is_backtest=0`
  filter + row schema), `sim/ledger.py` (`SimTrade` dataclass + `to_dict`),
  `scripts/backtest_*.py` + `src/backtest/` (harness inventory — varied
  internals, hence a generic SimTrade-dict recorder rather than per-harness
  coupling), `ml/experiments/splitters.py` (`split_live_holdout` from S6).

## Work Completed
- **`source` + `include_backtest` on `trade_outcomes` + `setup_labels`** — add a
  `source` field (`"live"`/`"backtest"`) to every row + an `include_backtest`
  kwarg (default **False** → reads only `is_backtest=0`, unchanged behavior;
  **True** → drops the filter and tags each row by its `is_backtest` flag). The
  `is_backtest` column is read for routing only (not emitted).
- **`ml/datasets/backtest_recorder.py` (new)** — `sim_trade_to_trade_row(d,
  run_tag, risk_pct)` maps a `SimTrade.to_dict()` to a trades-table row
  (`is_backtest=1`); the label columns are derived so the families work
  unchanged (`pnl`=realized R proxy so `won=pnl>0`; `pnl_percent`=`r_multiple *
  risk_pct` so `setup_labels`' `r_multiple = pnl_percent/risk_pct` recovers the
  sim R; `setup_type` falls back to the strategy so `setup_labels` includes it).
  Open/unlabeled trades → `None` (skipped). `write_backtest_trades(db, trades,
  run_tag)` INSERTs `is_backtest=1` rows only; returns the count.
- **Generalized `live_holdout`** with `live_flag_true_value` — so the
  source-tagged families use `live_flag_column: source` + `live_flag_true_value:
  live` to **train on live+backtest, hold out REAL trades only** (boolean
  `is_live_trade` path from S6 unchanged). Keeps the domain-shift discipline:
  never evaluate on synthetic/backtest rows.
- `execution_quality` deliberately **left untouched** (it must learn real
  slippage; synthetic/backtest fills would poison it — per the roadmap).
- **Tests** (`tests/ml/test_backtest_labels.py`): mapper (closed→row, open→None,
  direction/`setup_type`/`won` derivation), writer (`is_backtest=1`-only, count),
  both families' `include_backtest` (live-only default vs live+backtest with
  `source` mix), source-based `live_holdout` partition.

## Validation Performed
- `tests/ml/test_backtest_labels.py` + splitters/metalabel → 38 passed. Full
  `tests/ml/` (ex. the 2 pandas-only modules) → **506 passed, 1 skipped**.
  `ruff` clean; `py_compile` clean. Default-preserving: `include_backtest=False`
  reproduces the prior families exactly (existing family tests still pass).
- Trainer-VM end-to-end demo (run a sim backtest → `write_backtest_trades` to a
  TEMP db → build `trade_outcomes`/`setup_labels` with `include_backtest=True` →
  enlarged training set with `source=backtest` rows): reported below.

## Trainer-VM demo — ✅ end-to-end on the real schema
Ran via `trainer-vm-diag` (#2699 → #2700) against a TEMP copy of the canonical
journal (never the real DB): record 400 synthetic backtest `SimTrade`s, then
build the two families with `include_backtest`.

```
closed live trades: 499
recorded is_backtest=1 rows: 400
trade_outcomes  live_only=399  with_backtest=799  by_source={live:399, backtest:400}
setup_labels    with_backtest=799  by_source={live:399, backtest:400}
```

The recorder wrote 400 `is_backtest=1` rows; both families default to **399
live-only** and, with `include_backtest=True`, surface **799** rows correctly
`source`-tagged (399 live + 400 backtest). That's the data-wall relief in one
number: ~2× the training rows, with the real trades cleanly separable for a
live-only holdout.

**A real bug the demo caught (and fixed):** the first run failed with
`NOT NULL constraint failed: trades.position_size` — the live `trades` table
constrains columns the live writer always fills but a backtest row may not. My
unit-test DDL didn't carry that constraint, so it passed locally but failed on
the real schema. Fix: `write_backtest_trades` now introspects `PRAGMA
table_info` and fills any NOT-NULL-without-default column it didn't map with a
type-appropriate default; the test DDL was tightened to carry the real
`position_size NOT NULL` so the regression is caught locally. (Honest note: the
verification step did its job — the bug would have surfaced the first time
anyone pointed the recorder at the real journal.)

## Documentation Updated
- `ROADMAP.md` S-MLOPT-S7 row; `docs/ml/optimization-roadmap.md` Session 1.3;
  `docs/architecture/ai-model-platform.md` change-log; this sprint log.
  (`docs/data/dataset-taxonomy.md` `trade_outcomes`/`setup_labels` notes updated
  for the `source`/`include_backtest` addition.)

## Risks and Follow-Ups
- **Recorder not yet wired into a harness.** The recorder is generic
  (`SimTrade.to_dict()` in → rows out) and a one-call integration
  (`write_backtest_trades(db, [t.to_dict() for t in ledger.trades], …)`), but
  this PR does not auto-wire it into a specific harness or write to the
  production money DB (the opt-in money-DB write is the follow-up — keep it
  default-off + clearly `is_backtest=1`).
- **Domain shift persists.** Backtest fills are still synthetic (modeled, not
  live) — the eval discipline (REAL-only holdout) is mandatory and enforced by
  the source-based `live_holdout`. Backtest trades are a *training* augmentation,
  never an eval population.
- **`pnl` is an R proxy** for backtest rows (real dollar PnL isn't reconstructed)
  — fine for `won` / `r_multiple`; a $-accurate backtest PnL is a follow-up if a
  model ever needs dollar magnitude.

## Next Recommended Sprint
- **S-MLOPT-S8 (1.4)** cross-symbol transfer (joint BTC+MES — another small-data
  lever), or the S6 follow-up MB-20260603-002 (wire logged signals as the
  `setup_candidates` event source) + re-run the meta-label live-holdout now that
  backtest augmentation exists.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; recorder is opt-in, `is_backtest=1`-only.
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns were stated clearly.
