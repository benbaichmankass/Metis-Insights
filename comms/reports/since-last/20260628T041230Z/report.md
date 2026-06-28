# System report — since-last

- Generated: 2026-06-28T04:12:30+00:00
- Window: 2026-06-27T12:00:00+00:00 → 2026-06-28T04:12:00+00:00
- Roll-up grade: caution

~16h window. REAL MONEY +$1.78 (2/2 ict_scalp_5m BTCUSDT wins in $60k chop) — the only clean trading. Paper -$845.65 was a PRE-FIX ARTIFACT: the reverse reconciler adopted alpaca_options_paper option legs as equity orphans + priced them with the equity formula. ROOT-CAUSED + already fixed (#4858+#4867, live ~20:08); the 20:11 'restart' WAS that deploy. ML daily cycle green; 0 advisory models.

## P&L by class
- **real**: window +$1.78 (prior +$0.00, up)
- **paper**: window $-845.65 (prior +$801.00, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Tier-2 cleanup: supersede the ~8 pre-fix phantom paper rows so they stop polluting paper KPIs — Root cause FIXED in #4858+#4867 (live ~20:08). Residual: mark trades 2999-3006 (alpaca_options_paper, reconcile_status='reconciled') as 'superseded' — a live-DB writeback, operator-gated. Then verify clean over 48h.
2. Fund/auth alpaca_live to unblock ETF live execution (carry from prior report) — alpaca_live undercapitalized (~$150) + BL-20260627-ALPACA-LIVE-API-UNAUTH (real-money Alpaca key auth failure). ETF strategies only express on paper until resolved.
3. Decide: retire vs re-metric the two intentionally-trivial trade-outcome demo baselines (f1=0 by design) — trade-outcome-winrate/global baselines are non-promotable demos; f1=0 is EXPECTED on a sub-50%-win-rate holdout. Either retire from the daily cycle or switch the headline metric to Brier/AUC + mark expected-degenerate so reviews stop re-flagging.

## Review coverage
- Strategy promotion: All HOLD. Only 2 real closed trades this window (ict_scalp_5m, 2/2 wins) — insufficient new evidence to move any gate. Paper ETF 'losses' are reconciler artifacts, so NO demote/kill is justified on contaminated data. ict_scalp_5m remains the only strategy with a clean real-money edge; continue. No strategy met a promotion gate.
- ML training health: Healthy. The 2026-06-28 01:08→01:44Z daily cycle completed green (rc:0, ~40 manifests, calibrators+publish OK). ETH cross-asset manifests trained green on fresh daily data (resolved XA-DAILYBUILD). MES skips are expected; the winrate baseline is the only genuinely stuck model.
- Soak `shadow regime heads (BTC/MES/ETH)`: accruing — Daily retrains green; all at shadow/candidate. 0 met the shadow→advisory gate; regime-head promotion blocked on live_agreement gate (MB-20260626-003).
- Soak `ETH cross-asset shadow head`: accruing — Now training on fresh daily-built data (XA-DAILYBUILD resolved); accruing predictions.
- Soak `exit-ladder soak (P3)`: accruing — Observe-only; needs n>=30 real-money orders before graduation (PB-20260617-002). Real volume is tiny so this is slow.
- Soak `conviction + arbitration soak (P2/P3)`: accruing — Observe-only; no graduation gate met.
- Soak `trade-outcome-winrate baseline`: stalled — Degenerate f1=0 every retrain — but EXPECTED for a trivial winrate/threshold-0.5 baseline on a sub-50%-win-rate holdout (Brier 0.145 is fine). Decision: retire from the cycle or re-metric (Tier-3), not a repair.
- 🚩 RESOLVED THIS FOLLOW-UP: the paper -$845 'reconciler artifact' is root-caused (options-account orphan-adoption + equity pricing) and ALREADY FIXED in #4858+#4867 (live ~20:08 06-27). Residual is a Tier-2 cleanup of ~8 historical phantom rows, not a live bug.
- 🚩 RESOLVED: the 20:11Z restart was the git-sync auto-deploy of #4867, not an OCI/OOM event — benign.
- 🚩 STALLED SOAK: trade-outcome-winrate baseline degenerate (f1=0) — but this is BY DESIGN for a trivial baseline on a sub-50%-win-rate holdout; the fix is retire-or-re-metric (Tier-3), not a model repair.
- 🚩 CARRY-FORWARD: alpaca_live undercapitalized/key-unauth still blocks ETF live execution; 0 advisory ML models still influencing orders.

## Monitoring (soaking / awaiting decision)
- `BL-20260628-RECONCILER-PAPER-ARTIFACT` [health · verify] Options-account orphan-adoption + equity-pricing FIXED in #4858+#4867 (live ~20:08). Watching for zero new options adoptions; ~8 historical phantom rows pending Tier-2 cleanup. (next: 48h clean + operator OK on the supersede writeback)
- `MB-20260623-003` [ml · awaiting-decision] trade-outcome-winrate baseline degenerate (f1=0) every daily retrain — refine-or-retire is Tier-3. (next: operator go on retrain/retire)
- `PB-20260617-002` [performance · soaking] ExitPlan ladder graduation (P4) — observe-only soak not yet accrued. (next: n>=30 closed real-money orders with exit_ladder_soak rows)
- `PB-20260625-002` [performance · awaiting-decision] 26 unreconciled bybit_2 orphan canonicals carry possibly-phantom PnL; reconcile-history cleanup is operator-gated. (next: operator go on reconcile-history cleanup)
- `PB-20260620-001` [performance · verify] Intraday ETF cells fire+fill (verified) but exit cadence contaminated by the reconciler artifact; uso_1h not yet observed. (next: clean intraday exit + uso_1h fill)
- `MB-20260626-003` [ml · awaiting-data] Regime-head promotion structurally blocked on the live_agreement (trade-win) gate. (next: sufficient live agreement sample)

_report_id RPT-20260628-041230-since-last_