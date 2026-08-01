# System report — weekly

- Generated: 2026-08-01T09:00:00+00:00
- Window: 2026-07-25T08:30:00+00:00 → 2026-08-01T08:45:00+00:00
- Roll-up grade: investigate

Recovery week under load: a huge shipped burst (provenance overhaul, W1 journal cleanup, guard hardening, trainer honesty) VERIFIED WORKING with live evidence - but the token-rotation fallout took the trader down 2h today and the Telegram alerting layer is STILL dark on a malformed re-pasted token (operator re-paste needed). Real money -$6.99 on the week (measured, broker-reconciled); paper lost ~$4.2k on measured venues; prop flat.

## P&L by class
- **real**: window $-6.99 (prior $-9.77, up)
- **paper**: window — (prior —, down)
- **prop**: window $-18.06 (prior $-182.84, up)

## Operator priorities
1. Re-paste BOTH full Telegram tokens ('<botid>:<secret>') into Actions secrets, re-sync + restart — TELEGRAM_BOT_TOKEN on the VM is malformed (secret half only, no bot-id prefix) - ict-telegram-bot at restart 700, claude-bridge down, liveness-watchdog pages 404. ALL Telegram alerting is dark until this lands; the trader itself is fine.
2. fc-pcv v2 advisory swap is 4 days overdue and its gate evidence is blocked - unblock readiness, then decide — BTC+SOL 15m fc-pcv-v2 soaks met volume (758/723 scores in 7d). Readiness packets 0-byte on the trainer (OOM, MB-20260719) and relay drift reads time out. Fix or hand-run the packet, then Tier-3 swap + restore the SOL advisory head.
3. slv_trend_1h: approve a min-confidence floor (~0.3) or regime gate — Second consecutive review with should_skip-grade entries (conf 0.04-0.15), 5 graded entries all losses (paper-only today). Exact change proposed in PB-20260801-SLV-TREND-DEGENERATE-CONFIDENCE.
4. Decide backfill-fabricated-exits --apply (Tier-2, dry-run validated, recovers ~4%) — The apply was never run (operator go not given). Decide run-or-close so the item stops carrying; the read-side filter already quarantines the 206 fabricated rows either way.
5. Retire (or commit to building) the 3 never-trained MES baseline manifests — 64.6 days of every-cycle skips; the new staleness escalation will flag them nightly until dispositioned.

## Review coverage
- Strategy promotion: Real-money book: all HOLD (n=10 window trades is below any gate; no KILL/DEMOTE candidate emerged - eth_pullback_2h real drag is 2 trades, watch only). Paper/promotion pipeline: M27 scalp legs (SOL/AVAX/XRP/ETH) soaking toward their Tier-3 gate, not yet due; slv_trend_1h must clear the degenerate-confidence floor before ANY promotion talk (2nd-review escalation); htf_pullback_trend_2h stays demoted-to-paper (PRB-20260716 watch); SPLG/IAUM alpaca_live promotion still soak-gated (PB-20260707). The one promotion decision DUE is the fc-pcv v2 advisory swap (see ml) - overdue, evidence-blocked, escalated. Per-strategy M7 review packets not regenerated this window (generate-strategy-review-packets action available; n has barely moved since last packets).
- ML training health: Cycles ran nightly all window; 08-01 cycle trained 75 / failed 0 / 2 enforced audit-skips (named, genuine); dataset builds OK with real row_counts (post-P1 fix); no manifest_quarantine_tripped events in the window tail; trade-outcome-lgbm-v1 unquarantined and training; staleness escalation live (3 never-trained MES baselines routed to a retire-vs-build decision); trainer disk 79%, timer armed.
- Soak `btc-regime-15m-lgbm-fc-pcv-v2 (shadow)`: GATE DUE - overdue — 758 scores/7d since 07-21; advisory-swap decision due ~07-28, 4 days past; gate evidence blocked by readiness OOM - escalated.
- Soak `sol-regime-15m-lgbm-fc-pcv-v2 (shadow)`: GATE DUE - overdue — 723 scores/7d; same swap decision; also gates restoring the SOL advisory head.
- Soak `conviction-meta-v1`: accruing — 1368 scores/7d; A/B AUC read queued (MB-20260727-CONVICTION-AB-AUC).
- Soak `regime bar-scoring fleet (BTC/ETH/SOL/MES heads)`: accruing — Continuous scoring through 08:25Z today across 5m/15m/1h heads.
- Soak `M27 ict_scalp altcoin paper legs (SOL/AVAX/XRP)`: accruing — Toward the Tier-3 real-money gate (SRQ-20260728) - not yet due.
- Soak `execution-quality-baseline-v0 + setup-quality-audit-baseline-v0`: degenerate (known) — Constant scores (min==max) - known class, backlog-tracked.
- Soak `exit-ladder soak`: structural zero — differing=0 by construction (only turtle_soup declares tp2 and it is shadow) - PB-20260617-002.
- Soak `fc-geometry soak`: awaiting n — Insufficient paired rows; re-check ~2026-08-25 (MB-20260705-FC-SLTP-GEOMETRY).
- Soak `pairs sleeve (SOL/ETH 1h)`: active — Live legs open on bybit_1; watch winner-clipping via intent_reduce (graded C premature_exit this window).
- Soak `prop shadow tickets (trend_donchian eth/sol prop)`: accruing — Shadow-only emissions; reconcile clean (51/27/0 unacted).
- Execution capture: Real-money capture healthy this window (scalp TPs capturing 90%+ of MFE; stale_stop lever verified firing live); one dollar-immaterial donchian round-tripper logged for aging. Paper capture measured on broker-truth venues only (journal dollars provenance-contaminated). (dollars reconciled: True)
  - `trend_donchian` [real_money]: round-trip 100.0%, giveback 39.40R, hold 14.20/12.00h → degraded
  - 🔴 `trend_donchian|real_money`: Single-trade 39R giveback (MFE +34.5R -> SL -4.8R over 14h) (open 1 review(s), PB-20260730-SCALP-CAPTURE-STANDING-WATCH)
  - 🔴 `slv_trend_1h|paper`: Entry-side degeneracy: should_skip confidence 0.04-0.15, 5 graded entries all losses (open 2 review(s), PB-20260801-SLV-TREND-DEGENERATE-CONFIDENCE) ⚠️ ESCALATE
- 🚩 ALERTING DARK (standalone high-priority): both Telegram channels + liveness-watchdog pages dead on a malformed re-pasted TELEGRAM_BOT_TOKEN (no bot-id prefix). Trader healthy; operator re-paste required. This flag fires its own high-priority ping.
- 🚩 Trader was DOWN 05:29-07:33Z today (round-1 rotation synced an empty secret) - recovered; positions were bracket-protected broker-side throughout.
- 🚩 fc-pcv v2 advisory swap gate met but unactioned 4 days past due, and its evidence path (readiness packet) is itself broken (OOM) - escalated.
- 🚩 slv_trend_1h|paper degenerate-confidence entries recurred (reviews_open=2) - escalated with an exact Tier-3 proposal (confidence floor ~0.3).
- 🚩 Paper journal dollar PnL not quotable this window (44/69 rows legacy-fabricated); measured paper venues lost ~$4.2k led by MGC scalps -$2,868.
- 🚩 prop breakout_1 status snapshot 12d stale with $125.61 recorded DD-floor cushion (prop flat, so no live risk; freshness blocked on the dead Telegram nag channel).

## Monitoring (soaking / awaiting decision)
- `BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE` [health · awaiting-decision] Token rotation round 3 - operator re-paste of both full tokens (next: getMe 200 + services stable after set-env)
- `MB-20260721-FCPCV-V2-SOAK` [ml · awaiting-decision] Swap gate evidence blocked by readiness OOM (next: readiness packet produced for the two v2 heads)
- `BL-20260801-STUCK-PACKAGE-SWEEP-AFTER-W1P2` [health · verify] 3 packages force-closed right after the W1 P2 cleanup - expected one-off (next: no organic sweep firing by next review)
- `MB-20260705-FC-SLTP-GEOMETRY` [ml · awaiting-data] fc-geometry soak n insufficient (next: re-check ~2026-08-25)
- `PB-20260617-002` [performance · awaiting-data] Exit-ladder soak structurally zero-differing (only turtle_soup declares tp2, and it is shadow) (next: first live laddered strategy)
- `PB-20260630-002` [performance · soaking] TQQQ/QLD leveraged-Nasdaq paper soak (next: clean track record + account_compat_matrix before alpaca_live)
- `SRQ-20260728-M27-WINNERS-SHIP` [performance · soaking] M27 SOL/XRP/AVAX/ETH scalp paper legs toward real-money gate (next: soak window closes + M7 gate)
- `prop-status-staleness` [health · awaiting-data] breakout_1 account-status snapshot 12d stale (prop flat, no live risk); nag channel dead with Telegram (next: first status report-back after token restore)

_report_id RPT-20260801-090000-weekly_