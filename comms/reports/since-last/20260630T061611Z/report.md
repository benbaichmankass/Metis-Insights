# System report — since-last

- Generated: 2026-06-30T06:16:11Z
- Window: 2026-06-29T12:35:00Z → 2026-06-30T06:16:11Z
- Roll-up grade: caution

System healthy and ticking (trader+web-api up ~11h, all live accounts reachable). Two findings drive CAUTION: (1) Alpaca whole-share sizing — root-caused and FIXED in PR #5151 (journal==broker invariant across executor+partial-close); 3 legacy fractional paper rows pending correction. (2) RECONCILER PHANTOM CLOSES — trend_donchian's 20 closes are ALL reconciler-reconstructed (exit==entry w/ non-zero PnL, or null), making its -198R/0%-win figure unreliable; same class as the bybit orphan PnL (PB-20260625-002). Operator raised alpaca_live to a 10% test-account risk profile so the $150 account can finally trade.

## P&L by class
- **real**: window +$0.00 (prior $-1.12, flat)
- **paper**: window $-273.47 (prior —, —)
- **prop**: window — (prior —, —)

## Operator priorities
-.  — 
-.  — 
-.  — 
-.  — 

## Review coverage
- Strategy promotion: All HOLD. trend_donchian cannot be judged until the reconciler phantom-close bug is fixed (its PnL is artifact-polluted). ETF legs paper-soaking. No promote/demote this window.
- ML training health: Regime-head promotion blocked on the live_agreement gate (MB-20260626-003); MES heads anti-predictive live (MB-20260626-001/002); BTC 5m/15m promotion-candidate.
- Soak `alpaca ETF paper soak`: accruing — legacy positions rotating out (SLV +613); new entries whole-share after fix
- Soak `exit-ladder soak`: accruing — observe-only; no stall
- Soak `allocator soak (M18)`: accruing — observe-only regret metric
- 🚩 RECONCILER PHANTOM CLOSES (trend_donchian 20/20 reconciler-closed; bybit orphan phantom PnL) — analytics-polluting; investigate+fix (operator-directed).
- 🚩 Alpaca whole-share sizing — FIXED in PR #5151.
- 🚩 alpaca_live was inert at $150 — risk raised to 10% test profile (PR #5151).

## Monitoring (soaking / awaiting decision)
- `PB-20260617-002` [performance · soaking] ExitPlan ladder graduation awaits soak evidence (next: soak accrual)
- `MB-20260626-003` [ml · awaiting-decision] regime-head promotion blocked on live_agreement gate (next: operator: re-target to RG4?)
- `alpaca_live-10pct` [performance · verify] confirm alpaca_live actually places trades now at 10% risk (next: next US RTH session)

_report_id RPT-20260630-061611-since-last_