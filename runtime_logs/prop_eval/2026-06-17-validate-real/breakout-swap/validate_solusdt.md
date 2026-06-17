# Prop validation — `trend_donchian` on `SOLUSDT` (real Bybit perp + funding)

_Generated 2026-06-17T07:32:15.269758+00:00_

## VERDICT: **PASS**

- **Funding:** daily_swap_0.0009/day (Breakout venue model) — total drag $327.63, 15.24% of gross (pre $2150.17 → post $1822.54)
- **Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00, clock 1h, flip hold, 238 ledger trades

### Full-period funded 12-mo EV (best cell @ risk 1.5)

| risk | mean net $ | median | p5 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|
| 0.5 | $380 | $396 | $-90 | 57.63% | 1.2 | $54 | 7.038 |
| 1.0 | $1,028 | $957 | $-135 | 85.37% | 2.23 | $100 | 10.271 |
| 1.5 | $1,693 | $1,565 | $-135 | 93.73% | 4.53 | $204 | 8.306 |

### Walk-forward (4 evaluable folds; 4 positive, need 3 for PASS)

| fold | window | risk | trades | mean net $ | P(net>0) | ROI/fees |
|---|---|---|---|---|---|---|
| 1 | 2023-01-01→2023-10-16 | 1.5 | 54 | $635 | 78.67% | 2.374 |
| 2 | 2023-10-16→2024-07-31 | 1.5 | 60 | $256 | 60.9% | 1.277 |
| 3 | 2024-07-31→2025-05-16 | 1.5 | 53 | $3,240 | 99.63% | 32.376 |
| 4 | 2025-05-16→2026-02-28 | 1.5 | 58 | $2,310 | 99.47% | 9.836 |
