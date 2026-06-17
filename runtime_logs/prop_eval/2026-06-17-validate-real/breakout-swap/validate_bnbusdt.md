# Prop validation — `trend_donchian` on `BNBUSDT` (real Bybit perp + funding)

_Generated 2026-06-17T07:39:49.831387+00:00_

## VERDICT: **PASS** (label only — see NOTE.md; pre-swap realised ledger is NEGATIVE)

- **Funding:** daily_swap_0.0009/day (Breakout venue model) — total drag $487.49, -1334.8% of gross (pre $-36.52 → post $-524.01)
- **Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00, clock 1h, flip hold, 233 ledger trades

### Full-period funded 12-mo EV (best cell @ risk 1.5)

| risk | mean net $ | median | p5 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|
| 0.5 | $4 | $-90 | $-135 | 17.07% | 1.91 | $86 | 0.048 |
| 1.0 | $300 | $242 | $-270 | 60.33% | 3.98 | $179 | 1.676 |
| 1.5 | $665 | $583 | $-405 | 78.17% | 7.26 | $327 | 2.034 |

### Walk-forward (4 evaluable folds; 3 positive, need 3 for PASS)

| fold | window | risk | trades | mean net $ | P(net>0) | ROI/fees |
|---|---|---|---|---|---|---|
| 1 | 2023-01-01→2023-10-16 | 1.5 | 53 | $1,013 | 80.97% | 2.706 |
| 2 | 2023-10-16→2024-07-31 | 1.5 | 58 | $-332 | 7.13% | -0.87 |
| 3 | 2024-07-31→2025-05-16 | 1.5 | 51 | $947 | 87.23% | 3.288 |
| 4 | 2025-05-16→2026-02-28 | 1.5 | 54 | $1,070 | 94.27% | 3.566 |
