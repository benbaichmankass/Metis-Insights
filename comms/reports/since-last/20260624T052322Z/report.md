# System report — since-last

- Generated: 2026-06-24T05:23:22.093767+00:00
- Window: 2026-06-23T15:45:33+00:00 → 2026-06-24T05:23:22.093767+00:00
- Roll-up grade: caution

Quiet ~13.3h chop window (BTC +0.5%, ETH +0.7%, tight ranges). Real-money flat (0 closes) with one OPEN real ETH short (eth_pullback_2h, trade 2823) leaning against a mild ETH uptrend. Paper booked −$814.84 (avax_pullback_2h short −$812 stopped on the AVAX rally; sol −$2.53 reconciler artifact). Health green: heartbeat running, vm cpu47/mem11/disk26, all services active, prop bridge clean (0 un-acted). ML: daily cycle ran, 0 models promote-ready, 1 demote (5m-lgbm-yz reversed from yesterday's promote candidate). 7 in-window decisions graded 6C/1D.

## P&L by class
- **real**: window +$0.00 (prior +$0.00, flat)
- **paper**: window $-814.84 (prior +$0.00, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Open real-money ETH short (trade 2823) leaning against a +0.7% ETH uptrend — eth_pullback_2h short opened 04:01Z (entry ~1666.33); ETH grinding up 1660→1671. Monitor; the alt-pullback-2h cohort has a known chop/counter-trend entry concern (PB-20260614-001).
2. avax_pullback_2h paper short −$812 (held 3d, stopped on AVAX rally) — Alt-pullback-2h soak producing large adverse holds. Evaluate trend/regime entry filter before any real-money routing widens (PB-20260614-001).
3. ML: 5m-lgbm-yz-v1 flipped promote→demote; only advisory (1h-yz) still degenerate — 0 models promote-ready (stage-guard promote=[]). btc-regime-5m-lgbm-yz-v1 now in demote set. btc-regime-1h-lgbm-yz-v1 (the only advisory) remains degenerate — MB-20260623-001 Tier-3 demote awaiting operator.
4. Degenerate f1=0 baselines + trade-outcome shadow models failing gates after 36d soak — 5 *-baseline-v0/v1 models predict majority class only; trade-outcome shadow models fail beats_baseline/live_agreement/drift. Refine-or-retire (MB-20260623-003).
5. Diag-relay burst-webhook drops persist (BL-20260611-002) — ~half of bursted diag-request issues skipped this session (create-with-label race delivers empty labels → job-if skip). Reliable only filed one-at-a-time. Tier-1 CI fix candidate (job should read labels via API, not webhook payload).

## Review coverage
- Strategy promotion: No model gate-ready for promotion (stage-guard promote=[]). 1 stage-guard demote + 1 standing Tier-3 advisory demote. Strategy fleet: vwap killed (configured-not-loaded); alt cohort in demo-soak; no M7 packet PROMOTE/KILL changes this window.
- ML training health: 1 training cycle ran (00:56Z 06-24, ExecMainStatus=0). Dataset builds OK for market families; decision-model families starved by low real-trade volume. Registry healthy at 40 models; no cycle failure.
- Soak `shadow regime models (40)`: accruing — daily retrains; market-feature heads healthy (lgbm-v2/yz f1_vol 0.2–0.64); 5 baseline-v0/v1 degenerate (f1_vol 0)
- Soak `trade-outcome decision models`: gate_met — 36d shadow-soak DAYS complete but QUALITY gates FAIL (no edge over base rate, drift) → refine/retire, not promote
- Soak `conviction (P2/P3)`: accruing — trainer built conviction_meta 152 rows this cycle; P4 real-money sizing is Tier-3 soak-gated (MB-20260616-CONVICTION-P4)
- Soak `exit-ladder shadow-soak`: accruing — unavailable: /api/bot/exit-ladder/soak not separately pulled this window (relay budget); observe-only, P4 graduation backtest-gated
- 🚩 OPEN real-money ETH short (2823) leaning against a +0.7% ETH uptrend — alt-pullback-2h cohort chop-entry risk now on real money
- 🚩 avax_pullback_2h paper short −$812 (3-day adverse hold) — alt-pullback-2h soak quality concern
- 🚩 btc-regime-5m-lgbm-yz-v1 reversed from promote-candidate (06-23) to DEMOTE (06-24) — edge not holding
- 🚩 trade-outcome shadow models failing promotion gates after a complete 36-day soak
- 🚩 Diag-relay burst-webhook drops persist (BL-20260611-002) — degrades autonomous data pulls

_report_id RPT-20260624-052322-since-last_