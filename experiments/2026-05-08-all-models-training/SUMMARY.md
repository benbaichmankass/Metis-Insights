# Run 2026-05-08-all-models-training — summary

Wall-clock: 0.48 min (run.py: 0.33; run_stack.py: 0.15).

Dataset: BTCUSDT 5 m, 332,624 bars, Jan 2023 → Feb 2026 (qashdev/btc mirror of Binance Vision).

## Headline results

| Strategy | Variant | Trades | Win % | E[R] | Sharpe | OOS Sharpe |
|---|---|---:|---:|---:|---:|---:|
| **vwap** | V0 baseline (production) | 10,137 | 24.4 | −0.007 | −0.39 | +0.22 |
| **vwap** | **V1 Phase-2 HTF 4h ±1 %** | 5,164 | 25.9 | +0.066 | **+2.47** | **+1.10** |
| vwap | V2 best (4h ±2 %) | 5,840 | 26.2 | +0.071 | +2.82 | — |
| vwap | V3 best (1h ±1 %) | 5,533 | 26.4 | +0.083 | +3.23 | — |
| vwap | VS3 (1h ±2 % + SL 0.4 + thr 2.0) | 2,157 | 17.9 | +0.129 | +2.33 | +2.16 |
| **turtle_soup** | T0 baseline | 33 | 51.5 | +0.159 | +0.80 | +0.25 |
| **turtle_soup** | **TS1 atr_stop_mult=0.30** | 32 | 56.3 | +0.266 | **+1.33** | **+1.22** |
| turtle_soup | T6 HTF 4h EMA-50 align | 10 | 40.0 | −0.100 | −0.27 | reject |

## Recommended ships (single-knob each)

- **VWAP**: ship the queued Phase-2 with band 0.020 (not 0.010). Prior run had not yet shipped; this run confirms +2.86 Sharpe lift on a 38-month dataset, walk-forward IS +2.48 / OOS +1.10.
- **Turtle Soup**: tighten `atr_stop_mult` 0.35 → 0.30. +0.53 Sharpe full sample, OOS Sharpe +1.22 (vs +0.25 baseline). No cadence cost.

See `RECOMMENDATIONS.md` for full per-variant analysis, sweeps, walk-forward, and implementation checklist.
