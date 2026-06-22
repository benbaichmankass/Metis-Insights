# System report — monthly

- Generated: 2026-06-22T07:51:39+00:00
- Window: 2026-05-23T07:45:57+00:00 → 2026-06-22T07:51:39+00:00
- Roll-up grade: caution

30d: real-money +$23.73 across 30 trades (46.7% win, profit factor 3.12, max DD -$3.70). Paper book -$28,642 across 86 trades — dominated by mgc_trend_1h (commodity, -$19,991), ict_scalp_5m (-$7,950) and htf_pullback_trend_2h (-$9,262). System healthy (heartbeat live, deployed 0defa9b). Prop: no fills reported this window.

## P&L by class
- **real**: window +$23.73 (prior —, None)
- **paper**: window $-28,642.07 (prior —, None)
- **prop**: window — (prior —, None)

## Operator priorities
1. Paper book bleeding -$28.6k/30d — mgc_trend_1h -$19,991 (commodity, 15.8% win), ict_scalp_5m -$7,950, htf_pullback_trend_2h -$9,262. mgc_trend_1h already a demote candidate — confirm via /performance-review.
2. Real-money vwap the lone drag — vwap -$1.73 over 7 real trades (14.3% win) vs the book's +$23.73. Everything else real is green.
3. First system-report — wire the deeper sections — This initial report is assembled from live aggregates; Claude per-trade grades, ML fleet + market context populate once the three reviews run under /system-report.

_report_id RPT-20260622-075139-monthly_