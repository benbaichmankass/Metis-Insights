# System report — since-last

- Generated: 2026-08-10T05:15:00+00:00
- Window: 2026-08-07T14:55:00+00:00 → 2026-08-10T05:15:00+00:00
- Roll-up grade: investigate

Real money closed no trades this window and sits -$25.16 over 30d on 29 trades (PF 0.58); paper carries all the activity and is bleeding (-$38.9k/7d, PF 0.26). Two high-severity findings dominate: the new tick-cost instrument's first reading shows a 251s mean tick, now attributed to a serial per-strategy market-data fetch, and the netting-attribution remediation is shipped but inert (apply globally, allowlisted nowhere) while journal-vs-exchange divergence accrues. ML lifecycle is healthy.

## P&L by class
- **real**: window +$0.00 (prior +$0.00, flat)
- **paper**: window +$885.49 (prior —, up)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. Tier-2: collapse the per-strategy market-data fetch in the tick loop (251s mean tick) — 52 strategies x ~3.2s serial fetches. Fix is a per-tick (symbol,timeframe) candle cache, mirroring the grouping regime_bar_scoring already does. Touches the live hot loop, so needs your OK. Do NOT add a tick budget instead.
2. Tier-2: netting attribution is 'apply' globally but allowlisted for no account — decide which — Every soak row reads apply_scope=not_allowlisted incl. real-money bybit_2. Either add bybit_1 (then bybit_2) to NETTING_ATTRIBUTION_ACCOUNTS, or set global back to annotate so the declared mode matches reality.
3. Open the Android app once — the push channel is dark (device_tokens = 0) — Every FCM push reaches nobody, including the latched account-down/trainer-down WARNING alerts. Nothing repo-side can create a token; this needs the phone.
4. Real-money bybit_2 is running ~0.99x gross exposure with no declared ceiling — First real exposure-soak readings: bybit_2 0.9888x, bybit_portfolio 1.0513x, all accounts policy_declared:false. The ceiling VALUE stays Tier-3 and still needs max_multiple over a full window before it can be set.
5. Decide the prop account's status — the rule cushion is rendered from a 21-day-old snapshot — prop/status reports $144.77 daily-loss and $125.61 DD-floor cushion from a balance last reported 2026-07-20. 0 unacted tickets, so the bridge is idle rather than broken.

## Review coverage
- Strategy promotion: No promotion or demotion is actionable, and the reason is a COVERAGE problem rather than a stance: real money closed ZERO trades in the window and only 29 in 30d, so no live leg accrued gate evidence. The only net-positive real legs are ict_scalp_5m (4 trades, 50%, +$3.17) and trend_donchian_eth_4h (2, 50%, +$8.97) — both also under-powered. NO strategy meets a promote gate; no strategy has enough n to justify a KILL/DEMOTE.
- ML training health: HEALTHY — the strongest domain this window. Last cycle outcome 'trained', overall_rc 0. Manifests 68 ok / 0 failed / 3 skipped; dataset builds 108 ok / 0 failed / 2 skipped. Registry 95 models (3 advisory, 29 shadow, 62 candidate, 1 research_only). ZERO manifest_quarantine_tripped/quarantined events. Trainer VM up 25 days, load 0.02, head_sha f91e32b6 = current main. One watch item: trainer disk 83% (8G free). NOTE the lifecycle was sourced from the live VM's /api/bot/ml/status, because the trainer-vm-diag relay silently truncated the command and reported success (BL-20260807, reproduced this run).
- Soak `pairs`: progressing (first close ever) — 2,483 scanned. The 2026-08-10 fix produced the sleeve's FIRST close (by_event.close 1) and 2 half_open rows with cleanup_confirmed. BUT skip_state_unreadable is still 959/2483 = 38.6% CUMULATIVE — the log spans pre-fix rows, so this rate cannot grade the fix. Needs a post-fix-only window.
- Soak `netting-attribution`: 🔴 INERT — not progressing toward its purpose — global_mode=apply but apply_scope=not_allowlisted on every row, both bybit_1 and real-money bybit_2. Nothing is written to the money DB; divergence accrues (trade 4529 ict_scalp_sol_15m: journal 26.7 vs exchange 3.1, basis leg_gone).
- Soak `allocator`: accruing but headline contaminated — 173 scanned, disagree 52.0%, mean_regret 0.619 — CONFIRMED the real-vs-prop variant-pair artifact (BL-20260806): the sampled tick's two candidates are eth_pullback_2h vs eth_pullback_prop_2h, the same setup on two books.
- Soak `exit-ladder`: measured structural zero — 411 scanned (api 378 / prop 33), differing 0.0%, laddered_rows 0. Honestly declared as a structural zero — no live strategy emits a multi-rung exit — rather than an unrun comparison. Exemplary provenance.
- Soak `exposure`: now accruing (first weekday readings) — bybit_2 (real money) 0.9888x, bybit_portfolio 1.0513x, bybit_1 0.5516x, ib_paper 0.4442x. breakout_1/ib_live correctly measured:false (equity_unavailable), NOT reported as flat. Every account policy_declared:false — no ceiling anywhere. max_multiple over a full window still needed before a ceiling VALUE can be set.
- Soak `fc-geometry / news`: accruing — No anomaly surfaced this window.
- Execution capture: Measured via m20_exit_analysis (4d + 14d) on the trainer. THE POPULATION IS THE HEADLINE: the 4d window holds 6 closes, ALL PAPER, zero real-money — so its clean 0.0% roundtripper rate is over a sample far too thin to clear the standing anomaly, and mean_giveback 6.09R against mean_r 8.997 says most value reached was still handed back. The 14d window still shows 5m/15m legs holding 100h+. Dollars reconciled against /api/bot/pnl/exchange and /api/bot/pnl/broker-truth, with the population stated: the exchange-fills store is NOT account-filtered, so its 3d +$2,732.76 / 7d -$7,374.75 are dominated by paper bybit_1/bybit_portfolio and are NOT real-money P&L. Real money over 30d is -$25.16 at pnlCoverage 0.931. (dollars reconciled: True)
  - `ALL strategies (4d window)` [paper]: round-trip 0.0%, giveback 6.09R, hold 4.60/1.00h → degraded
  - `ict_scalp_sol_5m` [paper]: round-trip —, giveback —R, hold 102.80/0.50h → anomaly
  - `ict_scalp_sol_15m` [paper]: round-trip —, giveback —R, hold 100.20/1.50h → anomaly
  - `ict_scalp_avax_5m` [paper]: round-trip —, giveback —R, hold 26.20/0.50h → anomaly
  - `real-money legs` [real_money]: round-trip —, giveback —R, hold —/—h → degraded
  - 🔴 `ict_scalp_* (sub-hour legs)`: Holding 26-103 hours against a 5m/15m timeframe — one to two orders of magnitude over expected — and exiting via reconciler_filled / intent_reduce, NEVER via tp or sl. The per-trade bracket is not what ends these trades. (open 3 review(s), PB-20260730-SCALP-CAPTURE-STANDING-WATCH) ⚠️ ESCALATE
- 🚩 HIGH: trader tick chain at 251s mean / 296s max — 1-2 orders of magnitude over what its components claim, attributed to 52 serial per-strategy market-data fetches. Same failure mode as the June 2026 wedges.
- 🚩 HIGH: netting-attribution remediation is INERT — global mode 'apply', allowlisted for no account, including real-money bybit_2. Divergence keeps accruing (trade 4529: journal 26.7 vs exchange 3.1).
- 🚩 HIGH: the FCM push channel is structurally dark (device_tokens = 0) — every latched alert reaches nobody.
- 🚩 HIGH (AGED — open 3 consecutive reviews, since 2026-07-30): ict_scalp_* (sub-hour legs) still holding 26-103 hours against a 5m/15m timeframe and exiting via reconciler/intent_reduce, NEVER via tp or sl. Tracked by PB-20260730-SCALP-CAPTURE-STANDING-WATCH. Escalated per the anti-normalization rule — an execution defect surviving two reviews is by definition being walked past.
- 🚩 MEDIUM: real-money bybit_2 observed at 0.9888x gross exposure with no ceiling declared on any account.
- 🚩 MEDIUM: prop rule-distance cushion rendered from a 21-day-stale snapshot with no staleness marker.
- 🚩 MEDIUM: ict_scalp_mgc_15m cannot evaluate (no MGC 15m candles) while holding a live 105-contract position.
- 🚩 MEDIUM: review tooling failed quietly three ways this session (relay allowlist gap, invented rejection cause, trainer relay silent truncation) — two fixed, one filed.

## Monitoring (soaking / awaiting decision)
- `BL-20260809-EXPOSURE-SOAK-NOT-YET-TAKEN` [health · soaking] Exposure soak now accruing on weekdays; first readings taken. Needs summary.by_account max_multiple over a full window. (next: once c80a318 merges, pull /api/bot/exposure/soak summary)
- `BL-20260807-PAIRS-STATE-UNREADABLE-38PCT` [health · verify] Fix landed 2026-08-10 and produced the sleeve's FIRST-EVER close (by_event.close 1 of 2,483 scanned). But skip_state_unreadable is still 959/2483 = 38.6% CUMULATIVE — the log spans pre-fix rows, so the cumulative rate cannot grade the fix. (next: post-fix-only window: skip_state_unreadable rate over rows logged after 2026-08-10, and close count continuing to rise)
- `PB-20260730-SCALP-CAPTURE-STANDING-WATCH` [performance · awaiting-data] 4d window is clean (roundtrippers 0.0%) but n=6 and all paper; 14d still shows 5m/15m legs holding 100h+. (next: a window with >=20 scalp closes)
- `MB-20260727-CONVICTION-AB-AUC` [ml · awaiting-data] Matched-holdout AUC read on identical live rows. (next: sufficient matched live rows)

_report_id RPT-20260810-051500-since-last_