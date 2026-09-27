# H6 — Stacked best (top-2 by Sharpe): **anchored_vwap** + **htf_soft** (mode=anchored)

Per-candidate ranking (≥100 trades qualifies):
- **anchored_vwap**: sharpe=+1.93 win=20.74% trades=998 E[R]=+0.1503
- **slope_filter**: sharpe=+0.77 win=28.11% trades=217 E[R]=+0.0976
- **htf_soft**: sharpe=+1.09 win=33.64% trades=107 E[R]=+0.1896
- **rsi_conf**: sharpe=+0.34 win=27.59% trades=232 E[R]=+0.0402
- **vol_spike**: sharpe=+1.03 win=30.23% trades=172 E[R]=+0.1507

| metric | baseline | variant | Δ |
|---|---|---|---|
| trades | 251 | 541 | +290 (drop -115.5%) |
| win_rate | 28.69% | 20.52% | -8.17% |
| expectancy_R | +0.1010 | +0.1280 | +0.0270 |
| sharpe | +0.86 | +1.21 | +0.36 |
| max_dd_R | -22.48 | -60.59 | -38.11 |
