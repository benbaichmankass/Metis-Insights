# System report — since-last

- Generated: 2026-07-20T08:15:00+00:00
- Window: 2026-07-17T05:56:00+00:00 → 2026-07-20T08:15:00+00:00
- Roll-up grade: caution

System healthy end-to-end (all services active, IB connected, no alert banners, DB intact, trainer cycling). The since-last window had NO real-money closes; recent real book bleeds modestly (7d -$8.90, 30d -$13.95 on ~$287 bybit_2) led by ada/xrp pullback + ict_scalp_5m. PROP FLAG: breakout_1 logged two losing closes (-$85.90, -$96.94 SL) leaving ~$4831 effective — only ~$131 above the $4700 blow-up floor, and the on-file balance snapshot is stale (07-17). Paper QQQ closed -$405 (extended-hours flatten). Trainer disk 90%.

## P&L by class
- **real**: window +$0.00 (prior $-8.90, flat)
- **paper**: window $-376.45 (prior —, down)
- **prop**: window $-182.84 (prior —, down)

## Operator priorities
1. Prop breakout_1 cushion to the $4700 static-DD floor is thin (~$131) and the balance snapshot is stale — After the -$96.94 SL close just logged, effective balance ~$4831 vs the $4700 blow-up floor. Latest prop_account_status snapshot is 07-17 22:14 (pre-close). Report a fresh `bal <balance> <equity>` and decide whether to keep sizing 2-lot ETH shorts this close to the floor.
2. Real-money alt-pullback legs (ada_pullback_2h, xrp_pullback_2h) + ict_scalp_5m are net-negative — DEMOTE_SHADOW candidates — 30d: ada -$12.25 (16.7% win), xrp -$7.88 (20%), ict_scalp_5m expectancyR -2.0 over 7. eth_pullback_2h is the real winner (+$2.2, 60%). Tier-3 demotion proposed, not enacted — confirm before flipping execution:shadow.
3. Trainer VM disk at 90% (4.9G free) — Not yet failing builds (246 ok / 0 failed) but compounds the known trainer memory-saturation / OOM items (BL-20260715, MB-20260719-PROMOREADY-OOSEDGE-OOM). Prune old artifacts / datasets before it strands a cycle.
4. Paper QQQ closed -$405 via delayed extended-hours flatten — qqq_pullback_1h alpaca_paper trade #3269 opened 07-07 finally closed 07-17 at -$405 (paper). Consistent with the known extended-hours-exit handling (ALPACA_EXT_LIMIT_BUFFER_BPS). Paper only — no money at risk; noting for the exit-behavior trail.

## Review coverage
- Strategy promotion: HOLD/positive: eth_pullback_2h (+$2.21, 60%), trend_donchian_eth_4h (+$9.96). Already-demoted: htf_pullback_trend_2h. Pairs sleeve still SHADOW-soaking. Per-strategy M7 packets not pulled individually this session (relay budget); stance from /performance 7d+30d. All demotions are Tier-3 PROPOSED, not enacted.
- ML training health: Last cycle rc=0 (05:18Z). Manifests 87 ok / 0 failed / 7 empty_dataset-skipped (MES/exit-head gaps). No manifest_quarantine/OOM. Registry 90 (advisory 1 / shadow 28 / candidate 60). Mirror fresh, head current. Dataset-audit noise persists (MB-20260719, observe-only).
- Soak `pairs_sol_eth_a/b`: soaking — bybit_1 paper; 10 closed shadow/unfilled (grade C) + real fills this window — accruing.
- Soak `shadow models (28)`: soaking — Registry shadow stage accruing predictions; 1 advisory (BTC 15m vol).
- Soak `cross-asset / exit-ladder`: soaking — Live observe-only soaks emitting; none observed stalled.
- Soak `MES exit-head / execution-quality`: awaiting-data — empty_dataset skips — known MES native-history limitation, not a new stall.
- 🚩 PROP: breakout_1 ~$131 effective cushion to the $4700 static-DD floor after two losing closes; balance snapshot stale (07-17) — request fresh bal. (money-at-risk, Tier-3 sizing decision)
- 🚩 REAL: alt-pullback + ict_scalp_5m net-negative on real money — DEMOTE_SHADOW proposal (Tier-3)
- 🚩 INFRA: trainer VM disk 90% (4.9G free)
- 🚩 PAPER: QQQ paper -$405 extended-hours flatten (observability only)

## Monitoring (soaking / awaiting decision)
- `PROP-CUSHION` [cross · awaiting-decision] breakout_1 ~$131 above the $4700 floor; awaiting fresh balance report + operator sizing call. (next: fresh bal report)
- `PB-20260625-001` [performance · soaking] ETH prop legs real-money watch. (next: n>=20 closed across ETH legs)
- `MB-20260719-DATASET-AUDIT-NOISE` [ml · awaiting-decision] De-noise build-time dataset audit (many manifests flagged nightly). (next: de-noise PR)
- `PB-20260630-ICTSCALP-DEGRADE` [performance · soaking] ict_scalp_5m real-money degrading. (next: confirm +EV or propose demote)

_report_id RPT-20260720-081500-since-last_