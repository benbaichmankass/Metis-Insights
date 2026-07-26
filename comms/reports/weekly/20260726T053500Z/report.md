# System report — weekly

- Generated: 2026-07-26T05:35:00+00:00
- Window: 2026-07-19T05:20:12+00:00 → 2026-07-26T05:35:00+00:00
- Roll-up grade: caution

Quiet, plumbing-healthy week in a chop regime. Real money (bybit_2, the only live real account) lost -$13.83 over 7 closed (1W/6L, PF 0.15) — small dollars, all SL-appropriate exits; entry-edge got whipsawed (worst: eth_pullback_2h -$8.69). Services/DB/accounts all healthy, no alert banners. ML: 1 advisory head flagged for drift-demote (sol-regime fc-pcv-v1); promote decisions still blocked by the readiness oos_edge OOM.

## P&L by class
- **real**: window $-13.83 (prior $-7.40, down)
- **paper**: window $-27,963.56 (prior —, flat)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. eth_pullback_2h real-money leg bleeding — worst leg this week (-$8.69, 0/2), fires long into chop — Add the ADX/regime entry gate PB-20260618-015 / PB-20260614-001 propose. Real-money ETH pullback long keeps entering chop; grade C 'late' on the executed close.
2. sol-regime-15m-lgbm-fc-pcv-v1 (advisory) flagged for DEMOTE — significant score drift — Today's promotion-readiness recommends advisory->shadow. Fresh-data v2 sibling is soaking (n=486). Decide: demote v1, or gate-check + swap v2 in.
3. Promotion pipeline stalled — readiness can't compute oos_edge (no datasets_root / 6GB trainer OOM) — 92 models, 0 promote signals derivable because the readiness sweep ran without datasets. MB-20260719-PROMOREADY-OOSEDGE-OOM blocks every shadow->advisory promote decision.
4. Prop breakout_1: thin DD cushion (~$125 to floor) + 6-day-stale balance — equity $4825.61 vs $4700 static-DD floor = $125.61 cushion; last account-status 2026-07-20. Request a fresh balance report-back; 1 open ticket awaiting_report.
5. Degenerate low-confidence emissions — uso_trend_1h / slv_trend_1h at conf ~0.03 — 3 D-grade packages this window emitted at conf 0.03-0.04 'should_skip' (never filled). Review the confidence floor / emission on these legs (PERF-20260601-010).

## Review coverage
- Strategy promotion: Promotion-readiness (92 models): 0 promote, 1 demote (sol-regime fc-pcv-v1 drift), 91 hold. No M7 KILL/PROMOTE surfaced on live strategies. Real-money legs all HOLD; eth_pullback_2h is a demote-watch on real money (bleeding, Tier-3 entry-gate proposal). Promote signals are otherwise uncomputable this run (oos_edge OOM).
- ML training health: Daily cycle ran today 01:49Z; most manifests OK; MES intraday manifests skip empty_dataset (known base-stale). No manifest_quarantine/OOM cycle event in the tail. Registry 92 healthy. Readiness sweep evidence-blocked.
- Soak `5m regime heads (btc/eth/sol/mes)`: accruing — n ~3000-5300 shadow, means healthy 0.67-0.99.
- Soak `15m regime heads (btc/eth/sol/mes)`: accruing — n ~500-1900 shadow; fc-pcv v2 siblings n~486-508.
- Soak `sol-regime-15m-lgbm-fc-pcv-v1 (advisory)`: gate_met — DEMOTE gate met — significant drift; flagged.
- Soak `conviction-meta-v1`: accruing — shadow n=4962.
- Soak `exit heads (donchian 1h/peak)`: accruing — advisory n=96 / shadow n=95.
- 🚩 Real-money week net NEGATIVE: -$13.83, PF 0.15, 1/7 wins (small $; chop-driven, exits fine).
- 🚩 eth_pullback_2h worst real leg (-$8.69, 0/2) — fires long into chop; Tier-3 ADX-gate proposed.
- 🚩 sol-regime-15m-lgbm-fc-pcv-v1 advisory head flagged for DEMOTE (significant drift) — order-influencing for SOL.
- 🚩 Promotion pipeline evidence-blocked: readiness oos_edge sweep OOMs / lacks datasets_root => 0 promote signals computable (91/92 hold on insufficient-data).
- 🚩 Prop breakout_1 thin: ~$125 to the $4700 static-DD floor + balance snapshot 6 days stale.
- 🚩 account_reachability: ALL declared-live accounts reachable — no down account this window.

## Monitoring (soaking / awaiting decision)
- `MB-20260721-FCPCV-V2-SOAK` [ml · soaking] fc-pcv v2 fresh-data siblings soaking (btc n=508, sol n=486) as swap for the frozen/drifting v1 advisory heads. (next: v2 gate-check ready)
- `MB-20260628-REGIME-SOAK-READINESS` [ml · soaking] ETH/SOL regime shadow heads accruing (n=1200-1300); RG4 re-check when mature. (next: readiness oos_edge unblocked)
- `MB-20260721-MES-15M-HEAD-PARKED` [ml · awaiting-decision] mes-regime-15m-lgbm-v2 reads gate-ready (n=1204) — 2nd same-symbol advisory-head decision parked. (next: operator go)
- `PB-20260618-015` [performance · awaiting-decision] eth_pullback_2h real-money cell under watch — worst leg this week; ADX-gate proposal ready. (next: operator go on Tier-3 gate)
- `PB-20260617-002` [performance · soaking] Graduate the ExitPlan ladder to the real exit once the soak has accrued (exit-head-donchian-1h-v1 advisory n=96). (next: soak volume + backtest gate)
- `BL-20260624-MHG-CLOSE-CONFIRM-VERIFY` [health · verify] IB close-confirm fix holding (MHG/MGC healthy, IB reconnected clean, no naked-orphan alert). (next: next IB reset window)

_report_id RPT-20260726-053500-weekly_