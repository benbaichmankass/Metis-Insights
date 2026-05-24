# Volatility-squeeze breakout — best member-#3 candidate (S-STRAT-IMPROVE-S9)

**Date:** 2026-05-24 · **Status:** validated in backtest; **next: wire to
`execution: shadow`** (same path the fade took) · **Harness:**
`scripts/backtest_squeeze.py` · **Data:** `data/backtest_BTCUSDT_5m.csv`
(6yr, resampled 4h) · **Evidence:** trainer runs #1905 (edge+corr) /
#1906 (nested WF + blend).

## What it is

TTM-style squeeze: when Bollinger Bands contract **inside** the Keltner
Channels, volatility is compressed; when the BBs expand back **outside**
the KC, the squeeze "fires" — enter in the direction of price vs the
basis MA, with the wide-ATR-stop + Chandelier-runner exit (the
let-winners-run lever). A different **entry trigger** (volatility
compression) than the price-channel strategies (trend breakout / fade),
so it fires on a different subset of bars.

## Why it's the best member-#3 candidate

The member-#3 hunt tested many structures; this is the one that cleared
every gate:

| Candidate | net (6yr) | corr vs trend | verdict |
|---|---|---|---|
| slow-trend (12h/6h) | + | **0.40–0.44** | works but a momentum cousin (correlated) |
| 6h-fade | + | −0.02 | strong OOS but **knife-edge plateau** (fragile) |
| funding-fade | − | −0.1 | no edge (thesis falsified) |
| ES/MES cross-asset | ~0 | n/a | edge doesn't transfer (crypto-specific) |
| ML entry-filter on trend | hurts | n/a | anti-predictive OOS (edge is exit-driven) |
| **squeeze 4h** | **+35.4** | **0.30** | **robust plateau + diversifying** ✅ |

- **Diversifying:** corr **0.30** vs the live 2h trend (lower than any
  trend variant), −0.05 vs the fade.
- **Strong standalone:** +35.4R / 6yr, exp 0.325, **max-DD only 6.0R**,
  ret/DD 5.74, monthly concentration 0.26 (4h, bb20/std2.0/kc1.0).
- **Robust plateau:** the 4h nested walk-forward (train 2020-23 / OOS
  2024-26) is net-positive in **both** windows for **all six**
  bb-std{1.5,2.0,2.5} × kc{1.0,1.5} cells — not a lucky corner.
- **Blend payoff:** 3-way (trend2h + fade4h + squeeze4h) full-history
  ret/DD 5.81, max-DD 8.3R; the squeeze's low standalone drawdown lifts
  the book.

## Caveats (the watch-items for shadow)

- **Monthly concentration:** OOS top-month-share runs 0.45–0.99 across
  the plateau — the OOS returns lean on a few months. The
  most-diversifying corner (kc1.0) is the most concentrated (0.64) and
  lowest-frequency (34–91 OOS trades).
- **OOS expectancy decays** vs train (e.g. 0.37→0.23) but stays positive.
- **Config tradeoff:** kc1.0 = best diversifier (corr 0.30) but
  concentrated/low-freq; kc1.5 = more robust (126 trades, conc 0.45) but
  more correlated (0.54). Lean **kc1.0** for the diversification the
  decider needs; shadow data decides.

## Recommended config

`squeeze_breakout_4h`: 4h, bb_period 20, bb_std 2.0, kc_mult 1.0,
atr_stop 2.5, Chandelier trail 3.5, timeout 48.

## Next step

Wire as `execution: shadow` (the fade's exact path: module + signal
builder + intent/pipeline registration + YAML + tests, routed to bybit_1
demo for data collection — never real money until proven). Once live
shadow data confirms the edge (and the concentration holds up), promote
to its own funded account per the decider multi-account plan
(`docs/sprint-plans/DECIDER-MULTI-ACCOUNT-PLAN-2026-05-24.md`) as the
3rd blended member.
