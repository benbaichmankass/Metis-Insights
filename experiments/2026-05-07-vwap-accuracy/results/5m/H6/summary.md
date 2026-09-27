# H6 — Stacked best (top-2 by Sharpe): **anchored_vwap** + **htf_soft** (mode=anchored)

Per-candidate ranking (≥100 trades qualifies):
- **anchored_vwap**: sharpe=+3.47 win=33.26% trades=962 E[R]=+0.2060
- **slope_filter**: sharpe=+2.88 win=31.76% trades=847 E[R]=+0.1793
- **htf_soft**: sharpe=+3.36 win=34.22% trades=526 E[R]=+0.2791
- **rsi_conf**: sharpe=+2.65 win=30.21% trades=854 E[R]=+0.1700
- **vol_spike**: sharpe=+2.61 win=31.29% trades=636 E[R]=+0.1962

| metric | baseline | variant | Δ |
|---|---|---|---|
| trades | 950 | 523 | -427 (drop 44.9%) |
| win_rate | 31.05% | 36.33% | +5.28% |
| expectancy_R | +0.1631 | +0.3147 | +0.1516 |
| sharpe | +2.74 | +3.90 | +1.15 |
| max_dd_R | -33.85 | -21.70 | +12.15 |
