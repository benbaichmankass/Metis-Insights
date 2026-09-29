# H6 — Stacked best (top-2 by Sharpe): **htf_soft** + **slope_filter** (mode=rolling)

Per-candidate ranking (≥100 trades qualifies):
- **anchored_vwap**: sharpe=+1.41 win=19.92% trades=979 E[R]=+0.1096
- **slope_filter**: sharpe=+1.43 win=30.14% trades=219 E[R]=+0.1864
- **htf_soft**: sharpe=+1.57 win=36.36% trades=110 E[R]=+0.2750
- **rsi_conf**: sharpe=+1.35 win=30.08% trades=236 E[R]=+0.1687
- **vol_spike**: sharpe=+1.25 win=29.94% trades=177 E[R]=+0.1835

| metric | baseline | variant | Δ |
|---|---|---|---|
| trades | 254 | 102 | -152 (drop 59.8%) |
| win_rate | 28.74% | 37.25% | +8.51% |
| expectancy_R | +0.1096 | +0.3049 | +0.1954 |
| sharpe | +0.93 | +1.67 | +0.74 |
| max_dd_R | -24.51 | -10.38 | +14.13 |
