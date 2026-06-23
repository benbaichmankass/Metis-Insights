# System report — since-last

- Generated: 2026-06-23T05:11:00+00:00
- Window: 2026-06-22T12:33:30+00:00 → 2026-06-23T05:11:00+00:00
- Roll-up grade: caution

CAUTION. System healthy & relaxed (heartbeat live, services up, VM cpu 0%, trainer cycle clean). Real money quiet: -$0.90 in-window (1 ict_scalp loss as BTC faded ~3% off 65.6k). Paper bled -$3.6k (trend_donchian BTC -$3,350 + sol_pullback). Recurring: mgc/eth pkgs size to 0 qty->orphaned; M13 insights under-counts closed trades (0 vs 12); 2 un-acted prop tickets.

## P&L by class
- **real**: window $-0.90 (prior +$1.30, down)
- **paper**: window $-3,569.59 (prior —, down)
- **prop**: window +$0.00 (prior —, flat)

## Operator priorities
1. Zero-qty orphaned packages strand mgc_trend_1h (MGC) & trend_donchian_eth (ETH) — Both emit order packages that size to <1 contract -> per-trade refusal -> reconciler orphans them (BUG-049). The strategies fire but never trade on those accounts. Verify intended whole-contract refusal vs stranded capability.
2. M13 insights generator under-counts closed trades/signals (0 vs 12) — template:v1 cache reports 0 closed trades / 0 signals / $0.00 for 24h while the book has 12 closed + 37 pkgs. Likely the epoch-ms closed_at + signal-classification window bug the prior report fixed for /performance — confirm the generator adopted _closed_at.py.
3. Diversified paper cohort still bleeding (-$3.6k window, -$38.9k lifetime) — trend_donchian BTC -$3,350 (1 trade) + sol_pullback_2h dominate. Isolated paper soak (no real-money risk) but the verdict is leaning decay; evaluate DEMOTE_SHADOW/KILL on the worst cells.
4. Prop manual bridge: 2 un-acted tickets, no report-backs — trend_donchian_eth ETH-short tickets emitted (1 in-window) but never placed/reported; no account-status ever reported -> rule-distance cushions blind. The supervised executor isn't acting on the bridge.
5. ML: conviction-meta soak thin (n=28) + 3 empty MES datasets — Learned-conviction stacker far from shadow-ready; 3 MES quality manifests skip on empty_dataset. No order-influencing model degrading. Keep soaking; backfill MES datasets.

_report_id RPT-20260623-051100-since-last_