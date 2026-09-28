# H6 — Stacked best (top-2 by Sharpe): **htf_soft** + **slope_filter** (mode=rolling)

Per-candidate ranking (≥100 trades qualifies):
- **anchored_vwap**: sharpe=+0.94 win=19.37% trades=986 E[R]=+0.0719
- **slope_filter**: sharpe=+1.27 win=29.58% trades=213 E[R]=+0.1667
- **htf_soft**: sharpe=+1.39 win=35.51% trades=107 E[R]=+0.2454
- **rsi_conf**: sharpe=+1.13 win=29.44% trades=231 E[R]=+0.1405
- **vol_spike**: sharpe=+1.07 win=29.48% trades=173 E[R]=+0.1570

| metric | baseline | variant | Δ |
|---|---|---|---|
| trades | 248 | 99 | -149 (drop 60.1%) |
| win_rate | 28.23% | 36.36% | +8.14% |
| expectancy_R | +0.0909 | +0.2734 | +0.1825 |
| sharpe | +0.76 | +1.49 | +0.72 |
| max_dd_R | -24.51 | -10.38 | +14.13 |
