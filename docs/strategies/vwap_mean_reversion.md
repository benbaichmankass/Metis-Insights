# VWAP Mean Reversion Strategy

## Overview
A statistical strategy that trades price deviations from session VWAP
(Volume Weighted Average Price), expecting reversion to the mean.

## Core Logic
- Compute session VWAP anchored at the most recent UTC midnight
  (`_session_anchor_slice`, PR #481). Falls back to the full lookback
  when the session slice has < 50 bars or zero volume.
- Compute the standard deviation of price from VWAP over the same window.
- BUY when price is **ENTRY_STD_THRESHOLD** σ below VWAP and the HTF
  trend gate (4h EMA-200 ±2 % band) does not block longs.
- SELL when price is **ENTRY_STD_THRESHOLD** σ above VWAP and the HTF
  gate does not block shorts.
- Take profit at VWAP itself (the mean-reversion target).
- Stop loss at entry ± **SL_STD_MULT** × std_dev, with an ATR-derived
  floor so single-bar noise can't trigger.
- Exit early on vwap_cross when both gates pass (`min_r_for_vwap_cross`
  = 0.25 R, `min_hold_minutes_for_vwap_cross` = 10 min) — added
  2026-05-15 to suppress micro-edge exits when VWAP drifts to price.
- Time-decay close at `monitor_hold_window_minutes` (240 min default).
- SL moves to break-even at +1R via the shared `monitor_breakeven_sl`
  helper.

## Parameters (current — 2026-05-17 revert per issue #1370)

- **VWAP anchor**: most recent UTC midnight (session reset)
- **Entry Threshold**: **1.0 σ** (`ENTRY_STD_THRESHOLD`)
- **Stop Loss multiplier**: **0.5 σ** (`SL_STD_MULT_DEFAULT`) + ATR floor
- **Boundary R:R**: 2:1 (per CP-2026-05-03-20 operator directive)
- **Take Profit**: session VWAP
- **HTF trend gate**: 4h EMA-200, ±2 % band (PR #1175, 2026-05-08)
- **Recent context filter**: 1h timeframe, 24h lookback (informational
  only — does not block entries)
- **Position Size**: 1 % account risk
- **vwap_cross gates**: min 0.25 R + min 10 min hold

## Timeframes
- Primary: 5-minute candles
- HTF gate: 4-hour candles, EMA-200
- VWAP calculation: session (UTC midnight reset)
- Backtest period: 3.16 years on 5m BTCUSDT (issue #1370 validation set)

## Filters
- HTF 4h EMA-200 ±2 % band (BUY blocked in strong downtrend, SELL in
  strong uptrend)
- ATR floor on SL distance to prevent single-bar noise stops
- Session slice falls back to full lookback when < 50 bars to avoid
  silent signal suppression early in the UTC day

## Performance baseline (issue #1370, 3.16 y BTCUSDT 5m, HTF gate on)

| Variant | ENTRY | SL | Total R | Sharpe | Win % | Max DD R |
|---|---|---|---|---|---|---|
| **Current (1.0σ / 0.5σ)** | **1.0σ** | **0.5σ** | **+411.8** | **+2.82** | **26.2 %** | **-55.2** |
| Pre-revert (1.0σ / 0.75σ) | 1.0σ | 0.75σ | +148.7 | +1.34 | 33.1 % | -76.7 |
| Pre-revert (1.5σ / 0.75σ) | 1.5σ | 0.75σ | +133.1 | +1.38 | 30.7 % | -52.5 |

The wider SL (0.75σ) costs ~63 % of total R when the HTF gate is on.
The earlier 1.5σ entry threshold sweep (issue #1200) was performed
without the HTF gate and so produced a misleading optimum.

## Status
- [x] Strategy implemented (`src/units/strategies/vwap.py`)
- [x] Live since 2026-05-03 (CP-2026-05-03-20 operator directive)
- [x] HTF trend gate added 2026-05-08 (PR #1175)
- [x] Session anchor added 2026-05-07 (PR #481)
- [x] Re-validated against 3.16-year backtest 2026-05-17 (issue #1370)
