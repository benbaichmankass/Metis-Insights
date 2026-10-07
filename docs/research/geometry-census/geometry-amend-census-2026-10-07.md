# Geometry amend census — 2026-10-07

**Provenance:** MEASURED · **since** `2026-09-01` · amend tolerance 0.05% of the entry-time level.

**Population:** closed trades with timestamp >= since and a matching order package (trades.order_package_id = order_packages.order_package_id); amend = current order_packages.sl/.tp vs exit_plan.stop.price/.final.price entry-time level.

**Pull:** trades 6513 of 6513 rows, order_packages 5351 of 5351; complete = True.

Cells read `amended / denominator`. `—` means not measured (never 0).

| class | read_state | closed in window | matched pkg | SL amended | TP amended |
|---|---|--:|--:|--:|--:|
| real_money | measured | 32 | 32 | 4 / 32 | 0 / 32 |
| mirror | measured | 52 | 52 | 16 / 52 | 0 / 52 |
| paper | measured | 668 | 668 | 111 / 664 | 0 / 664 |
| prop | no_closed_rows | 0 | 0 | — | — |
| unclassified | absent | 0 | 0 | — | — |
| fleet | measured | 752 | 752 | 131 / 748 | 0 / 748 |

Window status counts per class (all `trades` rows since the date, any status):

- **real_money**: {'closed': 32, 'open': 1, 'rejected': 9}
- **mirror**: {'closed': 52, 'open': 2, 'rejected': 9}
- **paper**: {'closed': 668, 'exchange_rejected': 13, 'open': 10, 'rejected': 417}
- **prop**: {'rejected': 20}
- **unclassified**: —

## Prop

Prop fills live in the prop journal, not `trades`; the `trades`-table row above is therefore the comparable denominator, and prop amend state beyond it is read from the executor journals (`trail_amend` / `trail_skip` rows).

- prop tickets read_state: **measured** — {'breakout_1:closed': 19, 'breakout_1:expired': 11, 'breakout_1:expiry_prompted': 3, 'breakout_1:invalidated_prompted': 6, 'breakout_1:skipped': 32, 'breakout_1:suppressed': 46, 'breakout_2:dry_filled': 5, 'breakout_2:refused': 8, 'none:orphaned': 22, 'none:shadow': 48, 'tradeify_1:closed': 1, 'tradeify_1:expiry_prompted': 1, 'tradeify_1:skipped': 2, 'tradeify_1:suppressed': 4, 'velotrade_1:expiry_prompted': 1, 'velotrade_1:suppressed': 4}
- ticket rows carry the as-emitted sl/tp only; a prop amend needs the prop journal (trail_amend rows), not this table

## Per leg (closed trades with a matching package)

| class | leg | closed | SL amended | TP amended |
|---|---|--:|--:|--:|
| real_money | ada_pullback_2h | 6 | 0 / 6 | 0 / 6 |
| real_money | ict_scalp_5m | 6 | 0 / 6 | 0 / 6 |
| real_money | xrp_pullback_2h | 6 | 0 / 6 | 0 / 6 |
| real_money | eth_pullback_2h | 5 | 2 / 5 | 0 / 5 |
| real_money | trend_donchian_eth_4h | 4 | 0 / 4 | 0 / 4 |
| real_money | trend_donchian | 2 | 1 / 2 | 0 / 2 |
| real_money | iaum_pullback_1d | 1 | 0 / 1 | 0 / 1 |
| real_money | slv_pullback_1d | 1 | 0 / 1 | 0 / 1 |
| real_money | tlt_pullback_1h | 1 | 1 / 1 | 0 / 1 |
| mirror | ada_pullback_2h | 6 | 0 / 6 | 0 / 6 |
| mirror | ict_scalp_5m | 6 | 0 / 6 | 0 / 6 |
| mirror | tlt_pullback_1h | 6 | 5 / 6 | 0 / 6 |
| mirror | xrp_pullback_2h | 6 | 0 / 6 | 0 / 6 |
| mirror | eth_pullback_2h | 5 | 2 / 5 | 0 / 5 |
| mirror | uso_trend_1h | 5 | 3 / 5 | 0 / 5 |
| mirror | qqq_pullback_1h | 4 | 2 / 4 | 0 / 4 |
| mirror | spy_pullback_1h | 4 | 2 / 4 | 0 / 4 |
| mirror | trend_donchian_eth_4h | 4 | 0 / 4 | 0 / 4 |
| mirror | trend_donchian | 2 | 1 / 2 | 0 / 2 |
| mirror | gdx_pullback_1d | 1 | 0 / 1 | 0 / 1 |
| mirror | gld_pullback_1d | 1 | 1 / 1 | 0 / 1 |
| mirror | qqq_trend_long_1d | 1 | 0 / 1 | 0 / 1 |
| mirror | slv_pullback_1d | 1 | 0 / 1 | 0 / 1 |
| paper | pairs_bnb_btc_a | 94 | 0 / 94 | 0 / 94 |
| paper | pairs_bnb_btc_b | 93 | 0 / 93 | 0 / 93 |
| paper | pairs_sol_eth_a | 85 | 0 / 85 | 0 / 85 |
| paper | pairs_sol_eth_b | 83 | 0 / 83 | 0 / 83 |
| paper | ict_scalp_avax_5m | 53 | 25 / 53 | 0 / 53 |
| paper | ict_scalp_sol_5m | 32 | 10 / 32 | 0 / 32 |
| paper | ict_scalp_sol_15m | 24 | 5 / 24 | 0 / 24 |
| paper | ict_scalp_xrp_5m | 20 | 10 / 20 | 0 / 20 |
| paper | ict_scalp_eth_15m | 19 | 7 / 19 | 0 / 19 |
| paper | ict_scalp_xrp_15m | 19 | 6 / 19 | 0 / 19 |
| paper | trend_donchian_eth | 17 | 4 / 17 | 0 / 17 |
| paper | ict_scalp_mgc_15m | 14 | 4 / 14 | 0 / 14 |
| paper | ada_pullback_2h | 12 | 2 / 12 | 0 / 12 |
| paper | ict_scalp_5m | 11 | 5 / 11 | 0 / 11 |
| paper | trend_donchian_sol | 11 | 5 / 11 | 0 / 11 |
| paper | eth_pullback_2h | 10 | 3 / 10 | 0 / 10 |
| paper | trend_donchian_avax_4h | 9 | 4 / 9 | 0 / 9 |
| paper | xrp_pullback_2h | 9 | 2 / 9 | 0 / 9 |
| paper | squeeze_breakout_4h | 8 | 0 / 4 | 0 / 4 |
| paper | trend_donchian_sol_4h | 7 | 0 / 7 | 0 / 7 |
| paper | uso_trend_1h | 6 | 4 / 6 | 0 / 6 |
| paper | qqq_pullback_1h | 5 | 3 / 5 | 0 / 5 |
| paper | trend_donchian_eth_4h | 5 | 1 / 5 | 0 / 5 |
| paper | sol_pullback_2h | 4 | 0 / 4 | 0 / 4 |
| paper | trend_donchian | 4 | 3 / 4 | 0 / 4 |
| paper | trend_donchian_ada_4h | 4 | 3 / 4 | 0 / 4 |
| paper | mes_trend_long_1d | 2 | 2 / 2 | 0 / 2 |
| paper | mgc_pullback_1d | 2 | 0 / 2 | 0 / 2 |
| paper | gdx_pullback_1d | 1 | 0 / 1 | 0 / 1 |
| paper | iaum_pullback_1d | 1 | 1 / 1 | 0 / 1 |
| paper | mgc_trend_1h | 1 | 1 / 1 | 0 / 1 |
| paper | qqq_trend_long_1d | 1 | 0 / 1 | 0 / 1 |
| paper | slv_pullback_1d | 1 | 0 / 1 | 0 / 1 |
| paper | spy_pullback_1h | 1 | 1 / 1 | 0 / 1 |

Rerun: `python3 scripts/research/geometry_amend_census.py` (needs `DIAG_READ_TOKEN`; see `.claude/skills/diag-data/SKILL.md`).
