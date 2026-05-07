# H6 — Stacked best (top-2 by Sharpe): **htf_soft** + **slope_filter** (mode=rolling)

Per-candidate ranking (≥100 trades qualifies):
- **anchored_vwap**: sharpe=-3.94 win=22.14% trades=3528 E[R]=-0.1143
- **slope_filter**: sharpe=-2.35 win=24.82% trades=2168 E[R]=-0.0832
- **htf_soft**: sharpe=+1.59 win=28.43% trades=999 E[R]=+0.0903
- **rsi_conf**: sharpe=-3.26 win=23.04% trades=2665 E[R]=-0.1077
- **vol_spike**: sharpe=-3.13 win=22.17% trades=1872 E[R]=-0.1245

| metric | baseline | variant | Δ |
|---|---|---|---|
| trades | 2909 | 871 | -2038 (drop 70.1%) |
| win_rate | 23.41% | 29.28% | +5.87% |
| expectancy_R | -0.1152 | +0.1067 | +0.2219 |
| sharpe | -3.71 | +1.76 | +5.48 |
| max_dd_R | -386.54 | -61.89 | +324.65 |
