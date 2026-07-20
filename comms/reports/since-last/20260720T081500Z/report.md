# System report — since-last

- Generated: 2026-07-20T08:15:00+00:00
- Window: 2026-07-17T05:56:00+00:00 → 2026-07-20T08:15:00+00:00
- Roll-up grade: caution

System healthy end-to-end (all services active, IB connected, no alert banners, DB intact, trainer cycling). CORRECTION vs first pass: the real-money 'bleed' legs are regime-conditioned + low-n, NOT demote candidates — ada/xrp_pullback carry a live-firing adx_min:25 gate, ict_scalp_5m is already shadow (+$142 lifetime, loses only in trending+volatile). No demotions drafted. PROP: breakout_1 flat at $4825.61, ~$125.61 to the $4700 floor (fresh snapshot logged). Trainer disk 90%.

## P&L by class
- **real**: window +$0.00 (prior $-8.90, flat)
- **paper**: window $-376.45 (prior —, down)
- **prop**: window $-182.84 (prior —, down)

## Operator priorities
1. Prop breakout_1 cushion to the $4700 static-DD floor is thin (~$131) and the balance snapshot is stale — Fresh snapshot logged this session: breakout_1 flat at $4825.61, $125.61 above the $4700 floor / $144.77 daily-loss room. Decide whether to keep sizing 2-lot ETH shorts this close to the floor.
2. Real-money 'bleed' legs are REGIME-CONDITIONED + low-n, NOT demotion candidates — the ADX gate already works — M7 verdict HOLD for ada/xrp_pullback_2h (n_closed=0, insufficient evidence); both already carry adx_min:25 (live audit shows XRP blocked at ADX 15.7<25). ict_scalp_5m is ALREADY execution:shadow and is +$142.8/66.7% lifetime, losing ONLY in the trending+volatile cell (-$8.53/25%). No demotions. Path: author 2-D regime cells + mature the shadow regime heads.
3. Trainer VM disk at 90% (4.9G free) — Not yet failing builds (246 ok / 0 failed) but compounds the known trainer memory-saturation / OOM items (BL-20260715, MB-20260719-PROMOREADY-OOSEDGE-OOM). Prune old artifacts / datasets before it strands a cycle.
4. Paper QQQ closed -$405 via delayed extended-hours flatten — qqq_pullback_1h alpaca_paper trade #3269 opened 07-07 finally closed 07-17 at -$405 (paper). Consistent with the known extended-hours-exit handling (ALPACA_EXT_LIMIT_BUFFER_BPS). Paper only — no money at risk; noting for the exit-behavior trail.

## Review coverage
- Strategy promotion: ALL HOLD — no demotions this review (corrects the initial over-flag). ada/xrp_pullback_2h: M7 HOLD (n_closed=0), adx_min:25 gate confirmed firing live (XRP blocked at ADX 15.7<25 in current chop). ict_scalp_5m: ALREADY execution:shadow; +$142.8/66.7% lifetime, loss isolated to the trending+volatile regime cell (-$8.53/25%) — regime-conditioned, not structural. htf_pullback_trend_2h: already demoted 07-15 (PRB-20260716). eth_pullback_2h: HOLD (+, low-n). The M7 matrix is documented to over-fire DEMOTE at low n (PB-20260630-002). Correct lever = regime-cell authoring + ML regime-head maturation, NOT demotion.
- ML training health: Last cycle rc=0 (05:18Z). Manifests 87 ok / 0 failed / 7 empty_dataset-skipped (MES/exit-head gaps). No manifest_quarantine/OOM. Registry 90 (advisory 1 / shadow 28 / candidate 60). Mirror fresh, head current. Dataset-audit noise persists (MB-20260719, observe-only).
- Soak `pairs_sol_eth_a/b`: soaking — bybit_1 paper; 10 closed shadow/unfilled (grade C) + real fills this window — accruing.
- Soak `shadow models (28)`: soaking — Registry shadow stage accruing predictions; 1 advisory (BTC 15m vol).
- Soak `cross-asset / exit-ladder`: soaking — Live observe-only soaks emitting; none observed stalled.
- Soak `MES exit-head / execution-quality`: awaiting-data — empty_dataset skips — known MES native-history limitation, not a new stall.
- 🚩 PROP: breakout_1 $125.61 above the $4700 static-DD floor (fresh snapshot logged) — Tier-3 sizing decision
- 🚩 REGIME (not a demote): ict_scalp_5m loses only in trending+volatile; ada/xrp pullback are low-n + already ADX-gated — refine regime routing, do not demote
- 🚩 INFRA: trainer VM disk 90% (4.9G free)
- 🚩 PAPER: QQQ paper -$405 extended-hours flatten (observability only)

## Monitoring (soaking / awaiting decision)
- `PROP-CUSHION` [cross · awaiting-decision] breakout_1 ~$131 above the $4700 floor; awaiting fresh balance report + operator sizing call. (next: fresh bal report)
- `PB-20260625-001` [performance · soaking] ETH prop legs real-money watch. (next: n>=20 closed across ETH legs)
- `MB-20260719-DATASET-AUDIT-NOISE` [ml · awaiting-decision] De-noise build-time dataset audit (many manifests flagged nightly). (next: de-noise PR)
- `PB-20260630-ICTSCALP-DEGRADE` [performance · soaking] ict_scalp_5m real-money degrading. (next: confirm +EV or propose demote)

_report_id RPT-20260720-081500-since-last_