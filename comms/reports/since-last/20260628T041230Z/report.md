# System report — since-last

- Generated: 2026-06-28T04:12:30+00:00
- Window: 2026-06-27T12:00:00+00:00 → 2026-06-28T04:12:00+00:00
- Roll-up grade: caution

~16h window. REAL MONEY +$1.78 (2/2 ict_scalp_5m BTCUSDT wins in $60k chop) — the only clean trading. Paper -$845.65 is a RECONCILER ARTIFACT (6 alpaca_options_paper ETF positions mass-closed at one instant with impossible exit prices), not strategy signal. VM rebooted ~20:11Z, clean recovery. ML daily cycle green; 0 advisory models.

## P&L by class
- **real**: window +$1.78 (prior +$0.00, up)
- **paper**: window $-845.65 (prior +$801.00, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Fix reconciler mass-close producing fabricated paper PnL (and touching real-money closes) — BL-20260628-RECONCILER-PAPER-ARTIFACT: 6 alpaca_options_paper ETF closes at identical 19:05:48.058497Z with impossible exit prices; real #2995 closed 'reconciler'. Paper analytics untrustworthy until fixed.
2. Fund alpaca_live to unblock ETF live execution (carry from prior report) — alpaca_live undercapitalized (~$150); ETF strategies only express on paper. BL-20260627-ALPACA-LIVE-API-UNAUTH also flags a real-money Alpaca key auth failure to resolve.
3. Refine-or-retire the degenerate trade-outcome-winrate baseline (f1=0) — MB-20260623-003: predicts majority class on n=73, identical every daily retrain. Needs a non-degenerate target/dataset or retirement.
4. Confirm 20:11Z VM reboot was OCI host maintenance (2nd in ~2 days) — Both units came up together; clean pipeline start, no crash signature, watchdog healthy. Likely maintenance reboot; confirm to rule out a recurring infra issue.

## Review coverage
- Strategy promotion: All HOLD. Only 2 real closed trades this window (ict_scalp_5m, 2/2 wins) — insufficient new evidence to move any gate. Paper ETF 'losses' are reconciler artifacts, so NO demote/kill is justified on contaminated data. ict_scalp_5m remains the only strategy with a clean real-money edge; continue. No strategy met a promotion gate.
- ML training health: Healthy. The 2026-06-28 01:08→01:44Z daily cycle completed green (rc:0, ~40 manifests, calibrators+publish OK). ETH cross-asset manifests trained green on fresh daily data (resolved XA-DAILYBUILD). MES skips are expected; the winrate baseline is the only genuinely stuck model.
- Soak `shadow regime heads (BTC/MES/ETH)`: accruing — Daily retrains green; all at shadow/candidate. 0 met the shadow→advisory gate; regime-head promotion blocked on live_agreement gate (MB-20260626-003).
- Soak `ETH cross-asset shadow head`: accruing — Now training on fresh daily-built data (XA-DAILYBUILD resolved); accruing predictions.
- Soak `exit-ladder soak (P3)`: accruing — Observe-only; needs n>=30 real-money orders before graduation (PB-20260617-002). Real volume is tiny so this is slow.
- Soak `conviction + arbitration soak (P2/P3)`: accruing — Observe-only; no graduation gate met.
- Soak `trade-outcome-winrate baseline`: stalled — Degenerate f1=0 across daily retrains — not progressing; refine-or-retire (MB-20260623-003).
- 🚩 DATA-INTEGRITY (loud): reconciler mass-closes alpaca_options_paper ETF positions at one instant with impossible exit prices — fabricates the entire paper P&L and even touched a real-money close (#2995). Filed Tier-2 BL-20260628-RECONCILER-PAPER-ARTIFACT.
- 🚩 STALLED SOAK: trade-outcome-winrate-baseline-v0 degenerate (f1=0) every daily retrain — refine-or-retire (Tier-3).
- 🚩 CARRY-FORWARD: alpaca_live undercapitalized/key-unauth still blocks ETF live execution; 0 advisory ML models still influencing orders.
- 🚩 INFRA: VM rebooted ~20:11Z (2nd in ~2 days) — clean recovery, likely OCI maintenance, confirm.

## Monitoring (soaking / awaiting decision)
- `BL-20260628-RECONCILER-PAPER-ARTIFACT` [health · verify] Reconciler mass-closing alpaca_options_paper ETF positions with impossible exit prices; fabricates paper PnL, touched a real-money close. (next: clean intraday ETF exit observed (no mass-close))
- `MB-20260623-003` [ml · awaiting-decision] trade-outcome-winrate baseline degenerate (f1=0) every daily retrain — refine-or-retire is Tier-3. (next: operator go on retrain/retire)
- `PB-20260617-002` [performance · soaking] ExitPlan ladder graduation (P4) — observe-only soak not yet accrued. (next: n>=30 closed real-money orders with exit_ladder_soak rows)
- `PB-20260625-002` [performance · awaiting-decision] 26 unreconciled bybit_2 orphan canonicals carry possibly-phantom PnL; reconcile-history cleanup is operator-gated. (next: operator go on reconcile-history cleanup)
- `PB-20260620-001` [performance · verify] Intraday ETF cells fire+fill (verified) but exit cadence contaminated by the reconciler artifact; uso_1h not yet observed. (next: clean intraday exit + uso_1h fill)
- `MB-20260626-003` [ml · awaiting-data] Regime-head promotion structurally blocked on the live_agreement (trade-win) gate. (next: sufficient live agreement sample)

_report_id RPT-20260628-041230-since-last_