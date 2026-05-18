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

4. **`vwap` baseline re-validation.** Originally framed as confirming
   the pre-revert config (HTF 4h ±2 %, ENTRY_STD_THRESHOLD 1.5σ since
   PR #1205, SL_STD_MULT 0.75 since PR #1183) hadn't drifted. The
   ablation outcome instead drove the 2026-05-17 revert (commit
   66e520c, PR #1372): `V_1175_htf_only` (1.0σ / 0.5σ) won the sweep
   at +411.8 R / Sharpe +2.82, vs `V_PROD` (1.5σ / 0.75σ) at +133.1 R
   / Sharpe +1.38. Live config now matches `V_1175_htf_only`.

## Data

Reuses the 2026-05-08 dataset:
- Source: qashdev/btc 5m monthly archive (Binance Vision mirror).
- Window: Jan 2023 → today (extended from the 2026-05-08 run's
  Feb 2026 cutoff).
- Cached at `/home/ubuntu/ict-trader-data/btc_5m.parquet` via
  `scripts/ops/fetch_qashdev_btc_archive.py`.

## Variants

### vwap — ablation across PR #1175 / #1183 / #1205

Each variant adds one production change so each PR's contribution
can be read off the table independently.

| Tag | HTF gate | ENTRY thr | SL mult | Notes |
|---|---|---|---|---|
| `V_BASELINE` | none | 1.0σ | 0.5σ | Pre-PR #1175 baseline. |
| `V_1175_htf_only` | 4h EMA-200 ±2% | 1.0σ | 0.5σ | After PR #1175 only. |
| `V_1175_1183_htf_sl` | 4h EMA-200 ±2% | 1.0σ | 0.75σ | After PR #1175 + #1183 (SL widened). |
| `V_PROD` | 4h EMA-200 ±2% | 1.5σ | 0.75σ | Pre-revert production. After this sweep, reverted to `V_1175_htf_only` config (commit 66e520c, PR #1372). |

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

## Gate criteria — cadence-aware

The harness's ``sharpe`` is ``mean(R) / std(R) × sqrt(N)``, so it grows
with trade count. A single threshold across vwap (~3 k trades) and
turtle_soup (~37 trades) doesn't compare like-for-like — the high-
cadence strategy is always favoured. Two gates, picked by trades-per-
year:

**Low-cadence (≤ 100 trades/yr)** — same as #1143 / PR #1156:

| Criterion | Threshold |
|---|---|
| Win rate | ≥ 40 % |
| Expectancy R | ≥ +0.20 |
| Max DD R | ≥ -8 |
| Sharpe | ≥ 0.5 |

**High-cadence (> 100 trades/yr)** — appropriate for vwap and any
future market-making style strategy:

| Criterion | Threshold |
|---|---|
| Total R | > 50 R |
| Max DD R | ≥ -0.5 × Total R (DD budget) |
| Win rate | ≥ 25 % |
| Sharpe | ≥ 1.0 |

A variant clearing the applicable gate is recommended for production.
A variant clearing most but losing cadence vs production is flagged for
operator decision.

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
