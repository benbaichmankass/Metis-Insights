# System report — since-last

- Generated: 2026-08-14T12:20:00+00:00
- Window: 2026-08-07T00:00:00+00:00 → 2026-08-14T12:20:00+00:00
- Roll-up grade: investigate

Root-caused the operator's MONITOR BLIND alerts to TWO independent IB defects. (1) An event-loop race introduced by the M20 exit-loop decouple - mitigated, fix in PR #9240. (2) The MGC take-profit never fired because IB protection is attributed to a SYMBOL, not a TRADE: any sibling MGC strategy re-arming cancels this trade's legs, and the account-wide boolean protection check then reads 'protected'. No surface exists to observe IB open orders at all. Real money is near-flat (-$1.47 / 3 trades / 7d); ML training has silently stalled for 9 of 76 manifests since 07-26.

## P&L by class
- **real**: window $-1.47 (prior —, flat)
- **paper**: window — (prior —, flat)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Merge PR #9240, then CLEAR EXIT_LOOP_DECOUPLE_DISABLED — The mitigation is live, so the M20 30s exit cadence is OFF and exits are back to ~1/tick - the exact condition BL-20260810-TICK-TAKES-253S exists to flag. A deliberate trade, not a fix; must not stand.
2. Ship an IB open-orders read surface before any protection fix — No route answers 'does a protective leg rest on IB for this trade?'. That blindness is why the MGC defect survived 7 days beside three firing alarms. Tier-1, read-only, safe alone.
3. Make IB protective legs TRADE-attributable, not symbol-attributable — place_protective pre-cancels on contract.symbol alone, so any of the 3 MGC strategies re-arming strips the others' brackets; has_protective_orders is then account-wide AND boolean, so a sibling leg reads as cover.
4. ML training stalled: 9 of 76 manifests unadvanced since 2026-07-26 — 7 stale >7d + 2 with ZERO registered runs ever (setup-candidates-metalabel-paper-v1, exit-policy-v1) while the cycle reports outcome=already_complete / rc=0. A green cycle over a stalled subset.
5. Two shadow heads have soaked 87 days emitting a CONSTANT score — execution-quality-baseline-v0 (-0.20166275180833781) and setup-quality-audit-baseline-v0 (-0.0571959596) have min==max across 1531 predictions. Dead predictors accruing soak - the ETH-xa pattern.
6. 51 graded packages this window: zero A grades, 40 of 51 are C — Window dominated by the pairs sleeve trading micro-edges (|pnl| often < $3) whose dominant exit_quality is premature_exit. Real money closed just 3 trades in 7d for -$1.47.

## Review coverage
- Strategy promotion: NOT a full per-strategy promotion pass. Measured: real-money attribution over 12 strategies with any closed history — vwap is the clear demote/kill candidate (318 closed, 23.6% win, -$50.71, the largest real-money loss). mgc_trend_1h is DARK (not un-promoted): it emits signals and every one is rejected by flip_suppressed_hold_policy against the stranded MGC long — a routing blockage, not a performance verdict. No M7 review packets were pulled this run, so no KILL/DEMOTE/HOLD/PROMOTE badges were read; this block is therefore evidence-backed but INCOMPLETE.
- ML training health: Cycles ARE running (2 in 24h, timer active, next 2026-08-15T00:02:32Z) and dataset builds are clean (120 ok / 0 failed). But training has NOT advanced: last cycle outcome=already_complete, trained=0, and the staleness summary reports scanned=76 stale=7 never_trained=2 awaiting_source=1 against a 7d threshold. All 7 stale manifests last trained 2026-07-26 (19.0d). Two have ZERO registered runs across all 95 registry files and have been skipped/failed every cycle since landing: setup-candidates-metalabel-paper-v1, exit-policy-v1. No manifest_quarantine_tripped events observed. The cycle reports overall_rc=0 throughout — a green cycle over a stalled subset.
- Soak `shadow fleet (29 shadow + 3 advisory)`: accruing — Cadence ~315s crypto / ~440s MES; last_seen 12:14Z on every head.
- Soak `execution-quality-baseline-v0`: STALLED-IN-SUBSTANCE — 87.3d soak, 1531 predictions, score min==max==-0.20166275180833781 — a constant, non-discriminating predictor.
- Soak `setup-quality-audit-baseline-v0`: STALLED-IN-SUBSTANCE — 87.3d soak, 1531 predictions, score min==max==-0.05719595959595961 — constant.
- Soak `mes-regime-5m-lgbm-v2 (ADVISORY)`: live-influencing — 24.5d at advisory; score_mean 0.746, range 0.500–0.986.
- Soak `conviction-meta-v1`: accruing — 52.2d, score_mean 0.685.
- Soak `training pipeline`: STALLED — 9 of 76 manifests unadvanced since 2026-07-26 (7 stale >7d, 2 never trained) while the cycle reports rc=0 / already_complete.
- Execution capture: unavailable (m20_exit_analysis not run - needs the trainer relay + its market_raw store; the session budget went to the live IB incident). No roundtrippers_pct / mean_giveback_r / anomaly ageing. NOT a clean result. The one hold-vs-expected datum obtained without it is severe and IS root-caused: ict_scalp_mgc_15m, a 15-minute strategy, has held a position 7 days past a take-profit that was blown through on day one - because IB protection is symbol-scoped, not trade-scoped (BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY, now CRITICAL). (dollars reconciled: False)
- 🚩 IB event-loop contention degraded ALL IB symbols for ~2h+ in 120s cycles — blind monitors, strategy errors, AND refused entries. Mitigated; fix in PR #9240.
- 🚩 A 15-minute scalp has held 105 MGC contracts for 7 days past its take-profit. ROOT-CAUSED: monitor() has no TP close path by design, and the broker bracket that owns TP enforcement is cancelled whenever any sibling MGC strategy re-arms, after which an account-wide boolean check reports it protected.
- 🚩 ML training stalled for 9 of 76 manifests since 2026-07-26 while the cycle reports success.
- 🚩 Two shadow heads have soaked 87 days emitting a constant score.
- 🚩 exit_loop_health reports state=fresh / age_seconds=0.0 indefinitely after its writer stops — the ONE surface that can say whether decoupled exit evaluation is alive cannot distinguish 'fresh' from 'frozen'.
- 🚩 No read surface exists for IB open orders, so IB exit coverage is unverifiable from any session.

## Monitoring (soaking / awaiting decision)
- `BL-20260814-IB-EVENTLOOP-CONTENTION` [health · awaiting-decision] Mitigated by env flip; fix in PR #9240. (next: PR #9240 merged + deployed, EXIT_LOOP_DECOUPLE_DISABLED cleared, then a multi-hour window with zero 'event loop is already running')
- `MB/soaks-constant-score` [ml · awaiting-decision] execution-quality-baseline-v0 + setup-quality-audit-baseline-v0 emit a constant score over 87.3d. (next: operator call: retire or re-train; further soak accrues nothing)

_report_id RPT-20260814-122000-since-last_