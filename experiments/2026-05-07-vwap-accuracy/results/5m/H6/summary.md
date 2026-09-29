# H6 — Stacked best (top-2 by Sharpe): **anchored_vwap** + **htf_soft** (mode=anchored)

Per-candidate ranking (≥100 trades qualifies):
- **anchored_vwap**: sharpe=+2.16 win=20.99% trades=991 E[R]=+0.1702
- **slope_filter**: sharpe=+1.01 win=28.63% trades=227 E[R]=+0.1269
- **htf_soft**: sharpe=+1.25 win=33.63% trades=113 E[R]=+0.2160
- **rsi_conf**: sharpe=+0.68 win=28.22% trades=241 E[R]=+0.0812
- **vol_spike**: sharpe=+1.09 win=30.17% trades=179 E[R]=+0.1564

| metric | baseline | variant | Δ |
|---|---|---|---|
| trades | 261 | 525 | +264 (drop -101.1%) |
| win_rate | 29.12% | 21.33% | -7.79% |
| expectancy_R | +0.1263 | +0.1726 | +0.0463 |
| sharpe | +1.08 | +1.59 | +0.51 |
| max_dd_R | -22.48 | -60.59 | -38.11 |
