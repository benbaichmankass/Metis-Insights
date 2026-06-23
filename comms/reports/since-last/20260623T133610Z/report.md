# System report — since-last

- Generated: 2026-06-23T13:36:10+00:00
- Window: 2026-06-23T05:11:00+00:00 → 2026-06-23T13:36:10+00:00
- Roll-up grade: caution

Window 05:11Z->13:36Z. System healthy: heartbeat live, 36 strategies evaluating, deployed current. The grading lapse that under-reported the last two reports is RESOLVED (VM->repo bridge + live-DB freshness; 2492 packages graded incl. today). Real money ~flat (one BTC long -$1.64 on 0.001 sizing); paper +$110 in-window (-$4288/24h). CAUTION: in-window order packages orphan at target_qty=0 (BUG-049) and all closes were reconciler-driven, not clean TP/SL.

## P&L by class
- **real**: window $-1.64 (prior —, flat)
- **paper**: window +$110.08 (prior —, up)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Investigate zero-qty order packages orphaning (BUG-049) — Every in-window package shows aggregated_target_qty=0 -> created then reconciler-orphaned ~5m later. Signals generate but size to zero, so real execution is near-nil. Confirm whether this is correct sub-conviction sizing or a sizing/rounding defect.
2. Exits are reconciler-driven, not TP/SL — All 5 in-window closes had closeReason=reconciler (incl. the real BTC trade and its paper twin). Verify the monitor/exit path is closing on strategy SL/TP, not relying on reconciliation.
3. Paper reconciler closes show entry==exit with non-zero P&L — Paper ADA/XRP closes report +$507/+$333/+$98 with entryPrice==exitPrice -> likely a reconciler PnL-attribution artifact inflating paper P&L. Audit realizedPnl on reconciler closes.
4. Add /api/bot/ml/status (+strategies, prop) to the vm-diag relay allowlist — These read-only endpoints aren't on the relay's path allowlist, so ML registry detail can't be pulled over the issue relay. A one-line allowlist add restores full report coverage.
5. Optional: add GRADING_PAT secret for unattended grading auto-merge — The grading bridge opens a grades PR each run; without a GRADING_PAT it can't auto-merge (GITHUB_TOKEN PRs get no CI), so a session must adopt the branch. A fine-grained PAT (contents+PR write) makes it fully hands-off.

_report_id RPT-20260623-133610-since-last_