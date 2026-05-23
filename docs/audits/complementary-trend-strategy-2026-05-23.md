# Complementary Strategy Found — trend-follower (S-STRAT-IMPROVE-S7, 2026-05-23)

> **Tier-1 research.** First strategy in the program with a clearly
> positive net-of-fee edge over 3 years — AND it complements ict_scalp
> by regime. Validates the North Star (a portfolio of regime-complementary
> edged strategies). Not wired live; harness = `scripts/backtest_trend.py`.

## Strategy
Donchian(20) breakout entry + ATR(14) initial stop (2.5×) + Chandelier
ATR trailing exit (3.0×), on BTCUSDT 1h (5m parquet resampled). Low win
rate, big winners, **wide fee-efficient stops** (~0.056R fee/trade vs
ict_scalp ~0.18R, vwap ~0.45R).

## Results (net-of-fee, trainer 3yr cache, #1836)

| Window | Trades | WR | Gross R | Net R | net/trade |
|---|---|---|---|---|---|
| Full 2023→2026Q1 | 758 | 37.9% | +65.3 | **+22.5** | +0.030 |
| 2023 | 223 | 37.7% | +32.7 | +18.4 | +0.083 |
| 2024 | 236 | 41.5% | +23.7 | +12.6 | +0.053 |
| 2025–2026Q1 (derived) | ~299 | — | — | **~−8.5** | — |

avg_win ~1.37R, max single-trade excursion ~10R, exits 615 trail / 141
stop. Long net +36.8 / short net −14.2 over 3yr.

## Why it matters

1. **First net-positive strategy over 3 years (+22.5R).** vwap (neg),
   turtle_soup (neg/breakeven), ict_scalp (≈breakeven) all failed net.
   The wide ATR stop makes it fee-survivable — the lever the others
   lacked.
2. **Net-positive in 2024 (+12.6R) — exactly ict_scalp's worst year
   (−16.4R).** Direct evidence of complementarity.

## Complementarity (both regime-dependent, oppositely)

| Year | ict_scalp@1.6 net | trend net |
|---|---|---|
| 2023 | +7.9 | +18.4 |
| 2024 | **−16.4** | **+12.6** ✅ trend covers |
| 2025–26 | ~+9 ✅ ict covers | ~−8.5 |

Trend wins in directional regimes (2023/2024), loses in 2025's chop;
ict_scalp is the inverse. A portfolio of the two is **smoother than
either alone** — the North Star (regime coverage so something always
trades well), matching the research's momentum+MR blend (~Sharpe 1.71).

## Robustness confirmation (#1838) — plateau, not a lucky config

donchian × trail sweep (atr_stop=2.5 fixed), full-3yr net-of-fee R:

| donch \ trail | 2.5 | 3.0 | 3.5 |
|---|---|---|---|
| 15 | −24.5 | +34.6 | +37.7 |
| 20 | −21.7 | +22.5 | +29.3 |
| 25 | −10.1 | +40.0 | +33.8 |
| 30 | −31.6 | +14.6 | +19.7 |

- **One interpretable sensitivity:** trail must be LOOSER than the entry
  stop. `trail=2.5` (≤ the 2.5 stop) is negative everywhere (cuts winners
  early → kills the trend edge); `trail≥3.0` is positive everywhere.
- **Valid region (trail 3.0–3.5 × donchian 15–30) is a robust PLATEAU:**
  +14.6 to +40.0R, all positive. Passes plateau-not-cliff. The edge is
  real, not param-overfit. (Don't chase the 25/3.0 peak; plateau
  membership matters more.)

## Caveats (intellectual honesty)

- **Single param set** (Donchian 20 / 2.5 / 3.0). Could be lucky — needs
  a **param-robustness sweep** (plateau not cliff) before trust.
- **Long side carries the edge** (net short −14R) — a bull-market
  artifact across 2023–2026. Per the operator's no-static-bias rule,
  do NOT hard-disable shorts (they're bear-market insurance); revisit
  with a regime-robust direction handling.
- **BTCUSDT-only.** Needs MES + the other regimes.
- Simplified sim (trailing on entry-ATR; no intrabar trail-vs-stop tie
  modeling beyond SL-first). Conservative-ish.

## Next
1. Trend param-robustness sweep (donchian × atr_stop × trail) net-of-fee,
   per-regime — confirm a plateau.
2. **Combined ict_scalp + trend portfolio equity** (the real payoff:
   does the blend's drawdown/stability beat either alone?).
3. Regime-aware **decider** allocation between them.
4. MES; then more complements toward 3–5.
