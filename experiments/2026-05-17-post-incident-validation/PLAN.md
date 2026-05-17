# Post-incident validation backtest — 2026-05-17

## Why

The 2026-05-17 PR #1358 incident (full record in
`docs/sprint-logs/S-AUDIT-PIPELINE-2026-05-17.md` § Addendum)
disabled `ict_scalp_5m` without authorization. Before re-deploying the
restored config to live (PR #1364), the operator wants an independent
re-validation:

1. **`ict_scalp_5m` v2 still clears the pre-live gate** that PR #1156
   passed (59.3 % win rate, +0.301 R expectancy, max DD 3.47R on 90
   days of fresh BTCUSDT 5m candles). Re-confirm on a wider dataset.

2. **`turtle_soup` cadence diagnosis.** The live audit at 2026-05-17
   10:18–11:10 UTC showed 0/42 evaluations passed the stage-1 sweep
   gate (`min_sweep_buffer_bps: 10`). The 2026-05-08 T3 sweep covered
   `min_sweep_buffer_bps ∈ {4, 8, 12, 18, 25}` — we extend to {3, 5, 7,
   10, 12} to bracket the live silence and characterise the
   cadence / Sharpe tradeoff in the regime below 8 bps.

3. **5m turtle_soup naive variant.** Whether dropping `turtle_soup`'s
   setup TF from 15m to 5m has any plausible edge, on the same
   dataset, with the bar-count parameters re-scaled (`atr_period: 14
   → 42`, `sweep_lookback_15m: 60 → 180`) so the wall-clock lookback
   window is preserved.

4. **`vwap` baseline re-validation.** Confirm V1 (HTF 4h ±2 %,
   ENTRY_STD_THRESHOLD 1.5σ since PR #1205, SL_STD_MULT 0.75 since
   PR #1183) hasn't drifted on the same harness.

## Data

Reuses the 2026-05-08 dataset:
- Source: qashdev/btc 5m monthly archive (Binance Vision mirror).
- Window: Jan 2023 → today (extended from the 2026-05-08 run's
  Feb 2026 cutoff).
- Cached at `/home/ubuntu/ict-trader-data/btc_5m.parquet` via
  `scripts/ops/fetch_qashdev_btc_archive.py`.

## Variants

### vwap

| Tag | Params | Notes |
|---|---|---|
| `V_PROD` | HTF 4h EMA-200 ±2 %, ENTRY 1.5σ, SL 0.75σ | Current production. Re-validation. |
| `V_BASELINE` | No HTF, ENTRY 1.0σ, SL 0.5σ | Pre-PR #481 baseline for delta. |

### turtle_soup (15m)

| Tag | Params | Notes |
|---|---|---|
| `TS_PROD` | `atr_stop_mult: 0.30, min_sweep_buffer_bps: 10, setup_lookback_bars: 4, tp1_at_r: 1.00` | Current production. Re-validation against #1156 + #1175 tuning. |
| `T3_3` | `min_sweep_buffer_bps: 3` | Extends 2026-05-08 T3 below the prior floor. |
| `T3_5` | `min_sweep_buffer_bps: 5` | Live diag showed swept=0 at 10 bps; sample below it. |
| `T3_7` | `min_sweep_buffer_bps: 7` | Mid-point between prior {4, 8} grid. |
| `T3_10` | `min_sweep_buffer_bps: 10` | Production reference (sanity). |
| `T3_12` | `min_sweep_buffer_bps: 12` | Mid-point between prior {8, 18} grid. |

### turtle_soup (5m, naive port)

| Tag | Params | Notes |
|---|---|---|
| `T5M_NAIVE` | `timeframe: 5m, sweep_lookback_15m: 180, atr_period: 42`, other params at TS_PROD | Single-config "is this even plausible" run. If Sharpe ≪ TS_PROD, the 5m direction is dead; if comparable, schedule a proper sweep. |

### ict_scalp_5m

| Tag | Params | Notes |
|---|---|---|
| `IS_PROD_V2` | YAML defaults (wick_rejection, HTF 1h EMA-20, displacement 1.3 × ATR) | Re-validation of PR #1156 pre-live gate. |

ict_scalp uses its own harness (`scripts/backtest_ict_scalp.py`); the
sweep orchestrator invokes that for IS_PROD_V2 and reads the JSON
output. The vwap + turtle_soup variants share the engine from
`experiments/2026-05-08-all-models-training/scripts/run.py` (imported
via importlib because the source dir is not a valid Python package
name).

## Gate criteria

Same as #1143 / PR #1156 (the canonical pre-live gate):

| Criterion | Pass threshold |
|---|---|
| Win rate | ≥ 40 % |
| Expectancy R | ≥ +0.20 |
| Total R | > 0 |
| Max DD R | ≤ 8 |
| Per-trade Sharpe | ≥ 0.5 (or annualized ≥ 1.5) |

A variant that clears all five is recommended for production. A
variant that clears most but loses cadence vs production is flagged
for operator decision.

## Outputs

The orchestrator (`scripts/ops/run_backtest_sweep.sh`) writes:

- `/home/ubuntu/ict-trader-data/backtests/<UTC-date>/all_metrics.json`
  — per-variant Metrics dataclass dump.
- `/home/ubuntu/ict-trader-data/backtests/<UTC-date>/SUMMARY.md`
  — comparable table (trades, win %, E[R], Sharpe, max DD R) per variant.
- stdout — copy of the SUMMARY.md so the diag-relay comment carries
  the headline numbers without an artifact fetch.

## Comparison to 2026-05-08 run

Same engine, same data source, wider window (3 extra months of 2026
data). Numbers should match within sampling error on the variants
common to both runs (`V0_baseline`, `V1_htf`, `T0_baseline`,
`T1..T7`). Any large delta on a shared variant is a regression to
investigate.
