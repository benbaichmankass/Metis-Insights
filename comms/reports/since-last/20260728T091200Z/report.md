# System report — since-last

- Generated: 2026-07-28T09:12:00Z
- Window: 2026-07-27T08:10:05Z → 2026-07-28T09:12:00Z
- Roll-up grade: caution

CAUTION (plumbing-healthy since-last window; nothing new at money-risk, and real money edged POSITIVE). Services all active (trader ticking, fresh shadow scoring to 09:07Z), web-api up, all live accounts reachable, notifications clean (0 banners), prop reconcile clean. Real money +$0.49 in-window (3 closes: ict_scalp_5m BTC +$5.35 TP, trend_donchian -$1.04 SL, eth_pullback_2h -$3.82 SL; PF 1.10) - up from the prior window's -$3.75, still tiny dollars in a chop tape. Paper soak large & isolated (-$6,949/24h, big notionals). Grading ran fresh (19 order-packages: B6/C12/D1; the sole D is a PAPER htf leg, no real should_skip loss). ML healthy (trainer read confirmed): 2 cycles/24h rc=0, 92 builds OK / 0 failed, soaks accruing to 09:07Z, fc-pcv-v2 maturing toward its ~07-28 gate. Money-risk reduced this window: fvg_range_15m demoted live->shadow (#7792, operator-approved) - removed a -41R real-money bybit_2 route. Live flags (none money-risk): ict-mes-ibkr-pull.service still FAILED (MES base stale, #7635 fix didn't clear it); DIAG_BASE_URL stale (relay-only); alpaca_options_paper still 0 fills.

## P&L by class
- **real**: window +$0.49 (prior $-3.75, up)
- **paper**: window $-6,949.30 (prior $-4,141.60, down)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. MES IBKR pull service still FAILED after the #7635 fix - decide next MES-base remediation — ict-mes-ibkr-pull.service = failed (live-confirmed this window). The per-timeframe-retry fix (#7635) was 'landed/monitoring' per 07-27 but has NOT cleared the failure - the MES market_raw base stays frozen, keeping the MES 1d/other datasets blinded + mes-regime-1d audit-quarantined. Track-1 has a native-15m MGC study in flight that may re-target the gap. Recommend: read the failed-unit journal on next trainer/diag window and decide re-pull vs a different MES source. Not money-at-risk (mes-regime-5m advisory still scores).
2. M27 alt-scalp real-money graduation (SOL/XRP/AVAX/ETH 5m+15m) - Tier-3, per-leg operator call — SRQ-20260728-M27-WINNERS-SHIP (corrected #7796): only the SOL/XRP/AVAX/ETH 5m+15m paper legs are graduation candidates (XAUUSD 15m venue-blocked; no GLD ict_scalp leg exists). Under the merged soak-doctrine (#7813) their OFFLINE edge is already proven (k-fold); the paper soak is a MECHANICS check, not a wait-for-edge. Track-1 is running the alt-leg mechanics-match read now. Real-money (bybit_2) graduation is a per-leg Tier-3 operator decision once mechanics confirm - not a time-gated wait.
3. Protected-main autonomous-commit bypass (op-approved 07-26) - still awaiting execution — BL-20260723-WORKFLOW-COMMIT-TO-PROTECTED-MAIN: operator granted an Actions-bot bypass 07-26 to push the data paths (gpu-burst spend ledger + FRED valuation snapshots) to protected main; still needs the branch-protection setting OR a PAT secret value applied. Blocks the gpu-ledger persistence (BL-20260706-GPU-BURST-LEDGER) AND the M28 valuation-snapshot producer (MB-20260723-M28-VALUATION-PRODUCER-UNWIRED). Carried from 07-27.
4. fc-pcv v2 advisory swap gate-check due ~07-28 (today) — MB-20260721-FCPCV-V2-SOAK: btc/sol-regime-15m fc-pcv-v2 fresh-data siblings soaking (btc v2 732 preds, accruing 09:06Z). Gate-check + swap for the frozen v1 advisory heads is due today; SOL's advisory head (demoted 07-26) is restored by its v2 swap, which then unblocks SOL trend_vol cell authoring. Needs the trainer gate-check read.
5. ruff 0.16 residual cleanup (op-decided 07-26) - still awaiting the cleanup PR(s) — BL-20260723-RUFF-016: operator decided 07-26 to bump to ruff 0.16 + clear the ~8228 pre-existing residuals (6558 autofixable). The interim <0.16 pin holds CI green; the cleanup PR(s) are still pending. Carried.

## Review coverage
- Strategy promotion: Mostly HOLD; the material move this window was a DEMOTION (fvg_range_15m live->shadow, executed #7792 - money-risk reduced). No strategy auto-met a promote gate. M27 alt-scalp legs are the graduation candidates, gated on the Track-1 mechanics read + per-leg operator approval. fc-pcv-v2 advisory swap (ML head) due ~07-28. Automated promotion-readiness packets are restored (07-27 fix); promotion decisions run off drift + gate-checks.
- ML training health: —
- Soak `Per-bar regime heads (btc/eth/sol/mes 5m+15m)`: — — Fresh to 09:00-09:07Z. btc-5m variants 5964 preds, eth-5m 5834, sol-5m 5624, mes-5m 3492, mes-15m 1352. No stall.
- Soak `fc-pcv v2 advisory-candidate (btc-regime-15m-lgbm-fc-pcv-v2)`: — — 732 preds, last_seen 09:06Z - maturing toward the ~07-28 gate-check/swap for the frozen v1 advisory head.
- Soak `mes-regime-5m-lgbm-v2 (advisory)`: — — 1360 preds live at advisory, last_seen 09:00Z - the MES ML fleet is live+scoring at 5m despite the stale base blinding the 1d datasets.
- Soak `conviction-meta-v1 + setup-quality heads`: — — 5530 preds each; last_seen 00:44Z - signal-triggered (not per-bar), so the overnight gap reflects fewer actionable signals, not a stall.
- Soak `eth cross-asset shadow heads (xasset/selfonly-ctrl)`: — — 1101/1099 preds, last_seen 09:07Z - the A/B control soak is live and fresh.
- Soak `—`: — — 
- 🚩 ict-mes-ibkr-pull.service = FAILED (live-confirmed) - the #7635 fix did NOT clear it; MES base stays stale, MES 1d/other datasets blinded, mes-regime-1d audit-quarantined. NOT money-at-risk (mes-5m advisory still scores). Operator-priority #1.
- 🚩 Real-money 7d edge remains thin (in-window +$0.49 PF 1.10 is small-positive but tiny dollars; the prior report's 7d was PF 0.12) - chop-regime entry whipsaw; being addressed by regime/vol-gate work. Watch, not act.
- 🚩 Paper soak -$6,949/24h (isolated big notionals) - expected soak variance under the soak doctrine, NOT money-at-risk.
- 🚩 alpaca_options_paper still 0 fills since go-live 07-07 (BL-20260720-OPTIONS-PAPER-ZERO-FILLS) - 100% selection-refusal; options-portfolio direction pending operator (PRB-20260720).
- 🚩 DIAG_BASE_URL cloud-env stale (retired micro) - relay-only; BL-20260705 re-confirmed.
- 🚩 NO account down, NO trainer_down banner, NO orphan_unreconciled banner, prop reconcile clean - the money-critical surfaces are all green this window.

## Monitoring (soaking / awaiting decision)
- `MB-20260721-FCPCV-V2-SOAK` [ml · awaiting-decision] btc/sol fc-pcv-v2 siblings soaking (btc v2 732 preds); gate-check + swap due today. (next: trainer gate-check (~07-28))
- `REGIME_ML_VERDICT-SOL-ADVISORY` [ml · awaiting-data] SOL has no advisory head since the 07-26 fc-pcv-v1 drift-demote; v2 sibling restores it on swap, then SOL trend_vol cell authoring unblocks. (next: fc-pcv-v2 swap)
- `MB-20260719-PROMOREADY-OOSEDGE-OOM` [ml · awaiting-decision] promotion-readiness packets restored (07-27 fix, #7716/#7722); only the secondary oos_edge-inline-ON under-cap measurement remains. Not blocking. (next: clean under-4500M measurement)
- `BL-20260726-MES-IBKR-PULL / BL-20260727-ICT-MES-IBKR-PULL` [health · verify] ict-mes-ibkr-pull.service STILL failed this window despite #7635; MES base stays stale. (next: next MES pull / trainer window)
- `PB-20260721-ICTSCALP-ALTCOIN-PAPER-SOAK-CHECK` [performance · awaiting-data] Track-1-owned: alt-leg mechanics-match read (M27 ict_scalp executions vs simulator) in progress - the evidence for the M27 real-money graduation proposal. (next: Track-1 mechanics read result)
- `PB-20260620-001` [performance · verify] 6 intraday ETF cells confirmed AGAIN producing live paper fills (alpaca_paper 6 open ETF shorts this window). Resolvable after this 2nd confirming window. (next: resolve next review)
- `BL-20260720-OPTIONS-PAPER-ZERO-FILLS` [health · verify] alpaca_options_paper still 0 positions/fills (100% selection-refusal since go-live 07-07). Options-portfolio direction is under operator decision (PRB-20260720). (next: options-portfolio direction call)

_report_id RPT-20260728-091200-since-last_