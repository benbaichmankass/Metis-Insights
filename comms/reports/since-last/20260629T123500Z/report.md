# System report — since-last

- Generated: 2026-06-29T12:35:00+00:00
- Window: 2026-06-28T04:12:30+00:00 → 2026-06-29T12:35:00+00:00
- Roll-up grade: caution

~32h window. Core platform healthy: services green, DB clean, regime gate enforcing + BTC ML vol-gate confirmed firing live. Quiet trading — real money 2 small htf_pullback BTC losses (-$1.12), bybit_2 now flat; prop +$18.81 (operator-reported ETH close, documented this session). Two live issues surfaced + actioned: Alpaca positions sitting naked (root-caused + fix in draft PR #5018) and IB gateway down (MES/MGC/MHG dark). ML daily cycle green; 0 of 45 models promotable. Grading refreshed (2/2 closed trades graded C/D via the new diag grader).

## P&L by class
- **real**: window $-1.12 (prior +$1.78, down)
- **paper**: window — (prior —, flat)
- **prop**: window +$18.81 (prior —, up)

## Operator priorities
1. Approve + alpaca_paper-verify the naked-bracket fix (draft PR #5018) — Alpaca day-TIF protective legs cancel at RTH close, leaving multi-session ETF holds naked with no re-arm (IB-only). PR adds GTC OCO re-arm. Needs live OCO/GTC verification before merge/deploy.
2. Recover IB gateway — MES/MGC/MHG dark — ib_paper/ib_live read null; mgc_trend_1h went dry_run. Recurring BL-20260623-002. Run vm-ib-gateway-recover.
3. Approve prop-sizing bypass (draft PR #5018) — FIXED in draft: prop (breakout) accounts now bypass balance()-based sizing; the ruleset sizes the leg + the assistant places it. Needs operator approval + live verify.
4. Vol-gate enforce decision (~06-30 first look) — BTC ML vol-gate firing live with the advisory 15m head. MB-20260627-001 enforce-decision window opens ~06-30.

## Review coverage
- Strategy promotion: All 45 models HOLD per the 2026-06-29 promotion-readiness report (0 promote / 0 demote); most blocked on live_regime_discrimination. Strategy-level: no M7 packets pulled this run (relay-bounded); refine/retire candidates above are the live degenerate/stalled shadow models.
- ML training health: Daily cycle ran clean (rc=0, ~30 manifests OK, calibrators+publish OK). Only the 3 MES journal-backed manifests skipped for empty_dataset — known data-block, not a failure.
- Soak `BTC ML vol-verdict (advisory 15m head)`: gate_met — first live enforced fire observed 12:13Z; enforce decision ~06-30 (operator)
- Soak `ETH/SOL regime heads (shadow)`: accruing — fresh predictions ~12:10-12:20Z; not yet promotable (live_regime_discrimination)
- Soak `conviction-meta-v1 + cross-asset eth-xasset (shadow)`: accruing — 3084 + 455 preds, fresh
- Soak `exit-ladder ladder-vs-single`: stalled — accruing rows but all differs=false (no rungs) — graduation gate structurally un-trippable until rungs configured
- Soak `2 advisory BTC heads (1h-yz, 5m-yz)`: stalled — no predictions since 06-23/06-25 — investigate
- 🚩 IB gateway DOWN — MES/MGC/MHG dark (ib_paper/ib_live null; mgc_trend_1h dry_run).
- 🚩 Alpaca positions naked (no resting bracket) — root-caused + fix in draft PR #5018.
- 🚩 Prop SOL leg blocked: balance()-None sizing failure on API-less breakout_1.
- 🚩 2 advisory BTC heads silent since 06-23/06-25 while a third is fresh.
- 🚩 Exit-ladder soak structurally stalled (no rungs → gate un-trippable).

## Monitoring (soaking / awaiting decision)
- `MB-20260627-001` [ml · awaiting-decision] BTC ML vol-gate firing live with the advisory 15m head; soak accruing toward enforce decision. (next: ~2026-06-30 first look, enforce call)
- `PB-20260617-002` [performance · soaking] Exit-ladder soak accruing but every row differs_from_single_target=false (no rungs) — gate un-trippable until partial-TP rungs configured. (next: rung config + n>=30 differing)
- `PB-20260625-001` [performance · verify] ETH prop legs emit+journal confirmed; SOL prop leg blocked on balance()-sizing. Watching real swap rate + edge. (next: prop sizing fix + more ETH/SOL prop fills)
- `BL-20260628-RECONCILER-PAPER-ARTIFACT` [health · verify] Options-account orphan-adoption + equity-pricing fix holding — alpaca_options_paper flat, no new option-leg orphans. (next: 48h clean + operator OK on supersede writeback)
- `MB-20260623-003` [ml · awaiting-decision] Degenerate shadow baselines (all-zero / f1=0) each retrain. (next: operator go on refine/retire)
- `MB-20260626-003` [ml · awaiting-data] Regime-head promotion structurally blocked on live_regime_discrimination (live-agreement sample). (next: sufficient live agreement sample)
- `BL-20260629-ALPACA-NAKED-BRACKET` [health · verify] GTC OCO re-arm fix in draft PR #5018 — needs alpaca_paper live verification of OCO/GTC semantics. (next: operator approval + alpaca_paper verify)

_report_id RPT-20260629-123500-since-last_