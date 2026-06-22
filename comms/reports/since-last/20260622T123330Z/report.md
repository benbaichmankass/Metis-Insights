# System report — since-last

- Generated: 2026-06-22T12:33:30+00:00
- Window: 2026-06-22T07:51:39+00:00 → 2026-06-22T12:33:30+00:00
- Roll-up grade: investigate

INVESTIGATE. The first report ran clean but UNDER-REPORTED: real-money bybit_2 DID close trades in the window (trade #2768 trend_donchian BTCUSDT +$1.30, closed 11:37Z) — they were hidden by a KPI bug where the reconciler writes closed_at as epoch-ms, which datetime()/substr() drop from every windowed query (/performance, /stats pnl24h, /pnl/history). Found from the operator's Bybit transaction log; fixed read-side in this PR (shared _closed_at.py). Also flagged: 3 real bybit_2 positions ended 'orphaned' via stuck_strategy_watchdog with no realized PnL. System otherwise healthy (clean restart 12:09Z deploy b2b85f37, services up, VM relaxed). Paper book 8 open, lifetime -$31,972 (isolated, large notional). ML fleet nominal. Markets calm: BTC +1.2%, MES +0.5%, MGC +0.7%.

## P&L by class
- **real**: window +$1.30 (prior +$0.00, up)
- **paper**: window +$0.00 (prior +$0.00, flat)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. Approve read-side closed_at KPI fix (this PR) + schedule the writer-side normalise — Read-side guard wired into /performance, /stats pnl24h, /pnl/history (Tier-1, in this PR). Writer-side (order_monitor: normalise Bybit ms->ISO + migrate existing ms rows) is Tier-2, tracked BL-20260620-RECONCILER-CLOSEDAT-MS.
2. Investigate real bybit_2 positions orphaned by stuck_strategy_watchdog — Journal ids 2762/2757/2746 ended 'orphaned' with no exit/pnl though the exchange shows the account flat; #2765 closed with NULL pnl. Real money should record a clean close + realized PnL, not orphan.
3. Paper research book deeply negative (standing, not new) — Lifetime paper -$31,972 on large paper notional (bybit_1 $273k). 0 closed in window. Revisit in /performance-review.

_report_id RPT-20260622-123330-since-last_