# H6 — Stacked best (top-2 by Sharpe): **htf_soft** + **slope_filter** (mode=rolling)

Per-candidate ranking (≥100 trades qualifies):
- **anchored_vwap**: sharpe=+0.79 win=19.21% trades=989 E[R]=+0.0604
- **slope_filter**: sharpe=+1.42 win=30.14% trades=209 E[R]=+0.1890
- **htf_soft**: sharpe=+1.62 win=36.89% trades=103 E[R]=+0.2938
- **rsi_conf**: sharpe=+1.27 win=29.96% trades=227 E[R]=+0.1606
- **vol_spike**: sharpe=+1.19 win=30.00% trades=170 E[R]=+0.1774

| metric | baseline | variant | Δ |
|---|---|---|---|
| trades | 244 | 95 | -149 (drop 61.1%) |
| win_rate | 28.69% | 37.89% | +9.21% |
| expectancy_R | +0.1088 | +0.3270 | +0.2182 |
| sharpe | +0.90 | +1.72 | +0.82 |
| max_dd_R | -24.51 | -10.38 | +14.13 |
