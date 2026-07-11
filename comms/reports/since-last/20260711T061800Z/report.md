# System report — since-last

- Generated: 2026-07-11T06:18:00+00:00
- Window: 2026-07-10T16:15:00+00:00 → 2026-07-11T06:18:00+00:00
- Roll-up grade: healthy

Quiet, healthy 14h window since the 16:15Z report. No real-money closed trades; paper booked +$18.94. All live + paper broker accounts reachable. Standout: the scheduled 06:05 UTC IB gateway reset was handled autonomously by the freshly-deployed #6114 fix — the exec client rotated clientId and reconnected in ~4.5 min with no wedge and no manual recovery (the ~17-min wedge this fix targeted). ML training healthy (33 manifests / 0 failures). Two low-severity watches: trainer disk at 84% and a transient /api/pnl/history fetch error.

## P&L by class
- **real**: window +$0.00 (prior unavailable: /api/pnl/history fetch_failed, flat)
- **paper**: window +$18.94 (prior unavailable: /api/pnl/history fetch_failed, up)
- **prop**: window +$0.00 (prior —, flat)

## Operator priorities
1. Trainer disk at 84% (7.3G free) — Schedule artifact/cache cleanup on the trainer VM before disk pressure affects builds.
2. /api/pnl/history fetch_failed this pull — Endpoint returned fetch_failed during the report batch; verify transient next review (prior-window trend recorded unavailable).
3. trainer cycles_24h=0 status-field oddity — Reconcile the full-cycle counter vs manifests_24h (33 ok) in trainer_status reporting.

## Review coverage
- Strategy promotion: All HOLD. 0 real-money closed trades in the 14h window → no new gate-changing evidence for any strategy; no M7 packet crossed a threshold. btc-regime-15m-lgbm-v2 already at advisory (live BTC vol-gate). setup-quality shadow heads remain weak-negative (not promotion-ready). No demote/kill candidate surfaced. A full per-strategy M7 promotion sweep belongs to the weekly review; this since-last window had no fresh closed-trade evidence to move a gate.
- ML training health: Trainer healthy + on current main (38ac1c04), publishing every ~2min, actively training. Registry 80 (1 advisory / 28 shadow / 50 candidate). Watch: cycles_24h=0 reporting oddity; disk 84%.
- Soak `shadow regime/decision heads (28 shadow-stage)`: accruing — Live order packages carry modelScores from conviction-meta-v1 + setup-quality heads → shadow predictions accruing; no stall.
- Soak `conviction sizing`: accruing (observe) — order package shows conviction_sizing_decision action=would_be_size (observe/annotate, not applied) — accruing; no stall.
- Soak `exit-ladder`: accruing — observe-only ExitPlan laddered-vs-single-target soak continues; no stall flagged.

## Monitoring (soaking / awaiting decision)
- `MB-20260705-FC-ADVISORY-READINESS` [ml · soaking] fc heads (BTC+ETH 15m) at shadow, soaking toward the fc→advisory Tier-3 gate. (next: soak criteria met → operator promotion gate)
- `MB-20260628-REGIME-SOAK-READINESS` [ml · soaking] Multi-symbol ETH/SOL regime heads soaking toward RG4/advisory. (next: RG4 re-check when soaks mature)
- `BL-20260624-MHG-CLOSE-CONFIRM-VERIFY` [health · verify] #4441 IB close-confirm fix holding (recent window clean of MHG re-adopt flaps); full-history verify pending. (next: /health-review counting since-2026-06-24 MHG adopted_orphan+superseded rows)
- `BL-20260710-RESET-DAILY-RISK-WORKFLOW-SQLITE3` [health · awaiting-decision] reset-daily-risk-state workflow broken (sqlite3 not available); benign until a real reset is needed. (next: next drawdown-reset need, or a /health-review fix)

_report_id RPT-20260711-061800-since-last_