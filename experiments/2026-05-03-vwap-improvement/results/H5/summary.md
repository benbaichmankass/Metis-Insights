# H5 — Partial scale-out at VWAP + trail to opposite 1σ band

Two-stage exit: 50% off at VWAP touch, SL → BE, remainder targets
the opposite 1σ band (i.e. ~2R past entry on the reversion side).

- Expectancy (R, final leg only — see caveat below): 0.014 vs baseline -0.002 (target +20%)
- Trades: 946 vs 946
- Win rate: 37.42% vs 65.12%
- Sharpe: 0.82 vs -0.12
- Max DD (R): -19.97 vs -21.22

**Caveat:** simple_backtest computes r_mult from a single exit
price; the partial-take leg is reported in exit_reason but not
folded into the aggregate r_mult. Stage-4 reviewer should
recompute the blended expectancy from the per-trade reasons
before deciding adopt/reject. If H5 looks promising, the IMPLEMENT
PR should add a `multi_leg_backtest` helper to backtest_helpers.py
that aggregates blended legs natively.
