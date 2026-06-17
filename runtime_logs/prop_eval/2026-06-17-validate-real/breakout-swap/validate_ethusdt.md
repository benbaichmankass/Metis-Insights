# Prop validation — `trend_donchian` on `ETHUSDT` (real Bybit perp + funding)

_Generated 2026-06-17T07:33:50.897732+00:00_

## VERDICT: **PASS**

- **Funding:** daily_swap_0.0009/day (Breakout venue model) — total drag $386.52, 57.56% of gross (pre $671.46 → post $284.94)
- **Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00, clock 1h, flip hold, 238 ledger trades

### Full-period funded 12-mo EV (best cell @ risk 1.5)

| risk | mean net $ | median | p5 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|
| 0.5 | $89 | $-45 | $-90 | 28.13% | 1.43 | $64 | 1.39 |
| 1.0 | $524 | $506 | $-180 | 76.67% | 2.95 | $133 | 3.947 |
| 1.5 | $1,050 | $1,012 | $-225 | 91.53% | 5.49 | $247 | 4.247 |

### Walk-forward (4 evaluable folds; 4 positive, need 3 for PASS)

| fold | window | risk | trades | mean net $ | P(net>0) | ROI/fees |
|---|---|---|---|---|---|---|
| 1 | 2023-01-01→2023-10-16 | 1.5 | 54 | $272 | 63.7% | 0.654 |
| 2 | 2023-10-16→2024-07-31 | 1.5 | 56 | $919 | 85.07% | 3.647 |
| 3 | 2024-07-31→2025-05-16 | 1.5 | 64 | $473 | 77.9% | 2.295 |
| 4 | 2025-05-16→2026-02-28 | 1.5 | 54 | $2,178 | 99.63% | 17.602 |
