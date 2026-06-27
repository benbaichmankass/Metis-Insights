# System report — since-last

- Generated: 2026-06-27T12:00:00+00:00
- Window: 2026-06-26T06:02:00+00:00 → 2026-06-27T11:35:00+00:00
- Roll-up grade: caution

~29.5h window. REAL MONEY FLAT: 0 closed trades; ETH short #2990 open. All ETF strategies (spy/qqq/gld) generating valid signals but blocked — alpaca_live balance ~$150 (CONFIRMED not a key issue; undercapitalized). Bot watchdog auto-restarted within window (recovery clean). Paper +$801 is reconciler-artifact-heavy, not a clean read. 0 advisory ML models.

## P&L by class
- **real**: window +$0.00 (prior +$0.00, flat)
- **paper**: window +$801.46 (prior +$0.00, up)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. Fund alpaca_live to >=2000 USD to unblock ETF execution — CONFIRMED: alpaca_live api_ok=true, keys valid. Balance ~$150 insufficient for 1-share of SPY (~$732)/QQQ (~$713)/GLD (~$370) at 0.3% risk (whole-share constraint). 14+ valid signals lost this session alone. Fund account to >=2000 USD.
2. Investigate bybit_1 paper target_qty=0 for ada/xrp — ada_pullback_2h and xrp_pullback_2h packages all show aggregated_target_qty=0 on bybit_1. Daily risk cap exhaustion or depleted paper balance likely. Affects paper P&L fidelity.
3. Confirm bot restart root cause — check journalctl around restart event — Watchdog auto-restart detected (uptime=58s at snapshot). Recovery clean. Pull journalctl from restart timestamp to confirm no new crash loop or OOM pattern.
4. No advisory ML models — 0 advisory models influencing signals — btc-regime-5m-lgbm-yz-v1 remains at shadow after prior demotion. 41 models at shadow/candidate, 0 advisory. No model met promotion gate this window. Review shadow model track records to assess promotion readiness.

## Review coverage
- Strategy promotion: All strategies HOLD. 0 real closed trades in window — insufficient data to update M7 gates. ETF fleet (spy/qqq/gld) signal-healthy but execution-blocked by alpaca undercapitalization; this is infra, not a strategy-quality demotion. eth_pullback_2h has 1 open real trade (#2990). trend_donchian and ict_scalp_5m generated 0 packages in window — not active / conditions not met.
- ML training health: Training health data unavailable this session (trainer relay not queried). Prior report (RPT-20260626-060200-since-last) confirmed: 1 cycle ran clean overnight; dataset builds OK; no stuck cycle. 0 advisory models. 41 shadow/candidate models soaking.
- Soak `shadow regime heads (41 models)`: accruing — all at shadow/candidate stage; accruing prediction volume in shadow_predictions.jsonl. No model has met shadow→advisory promotion gate.
- Soak `exit-ladder soak (P3)`: accruing — observe-only soak comparing laddered exit vs single TP; not yet at graduation gate (need n>=30 real-money orders)
- Soak `conviction + arbitration soak (P2/P3)`: accruing — observe-only; no graduation criteria met yet
- 🚩 OPERATOR ACTION REQUIRED: alpaca_live execution blocked — balance ~$150, insufficient for whole-share ETF sizing. Keys ARE valid (api_ok=true confirmed). 14+ valid signals lost in this window alone (SPY/QQQ/GLD/IWM shorts).
- 🚩 Bot watchdog auto-restart within window — root cause unknown. Recovery clean. Recommend checking journalctl for OOM or crash context.
- 🚩 bybit_1 paper sizing returning target_qty=0 for ada/xrp — daily cap or balance issue. Separate from alpaca. Paper KPIs unreliable.
- 🚩 0 advisory ML models — no model-influenced advisory-layer downsize active. All 41 models observing in shadow.
- 🚩 All 7 in-window paper closes are reconciler artifacts (openedAt≈closedAt) — paper book $+801 is phantom, not strategy performance.

## Monitoring (soaking / awaiting decision)
- `MB-20260601-002` [ml · soak] regime-classifier f1=0 baseline — shadow-only; awaiting retrain experiment (next: after retrain experiment completes)
- `PB-20260617-002` [performance · soak] exit-ladder graduation — soak not yet accrued (P3 observe-only) (next: n>=30 closed real-money trades with exit_ladder_soak rows)
- `PB-20260625-002` [performance · awaiting-decision] 26 unreconciled bybit_2 orphan canonicals — operator-gated reconcile-history cleanup (next: operator go on reconcile-history cleanup)

_report_id RPT-20260627-113500-since-last_
