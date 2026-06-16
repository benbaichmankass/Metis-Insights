# Prop-firm evaluation matrix

- **Ruleset:** `breakout`
- **Limits:** profit target 10.0%, daily-loss 3.0%, max-DD 6.0% (static), funded soak 30d
- **Data window:** None → None (2023-01-01 00:00:00+00:00 → 2026-03-01 00:00:00+00:00)
- **Generated:** 2026-06-16T17:17:38.962051+00:00

| Rank | Roster | Eval pass | Days→target | Active days | Off-start DD (rule) | Peak-trough DD | Consistency worst-day | Funded survive | First breach | Net $ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `squeeze_breakout_4h,fvg_range_15m` | ✅ | 673 | 89 | 0.2% | 3.3% | 19.4% | ✅ | — | $504 |
| 2 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | ✅ | 519 | 169 | 0.0% | 9.9% | 12.3% | ✅ | — | $184 |
| 3 | `squeeze_breakout_4h` | ❌ | — | 55 | 0.2% | 2.4% | 19.7% | — | — | $183 |
| 4 | `fvg_range_15m` | ❌ | — | 44 | 0.0% | 2.6% | 28.7% | — | — | $395 |
| 5 | `trend_donchian,fvg_range_15m` | ❌ | 432 | 233 | 7.5% | 6.2% | 17.2% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $905 |
| 6 | `trend_donchian` | ❌ | 428 | 216 | 7.5% | 6.3% | 18.9% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $825 |
| 7 | `trend_donchian,squeeze_breakout_4h,fvg_range_15m` | ❌ | — | 226 | 7.5% | 6.9% | 4.0% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $277 |
| 8 | `trend_donchian,squeeze_breakout_4h` | ❌ | — | 210 | 7.5% | 7.0% | 3.6% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $263 |
| 9 | `trend_donchian,fade_breakout_4h,squeeze_breakout_4h` | ❌ | — | 260 | 7.5% | 7.2% | 5.5% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $152 |
| 10 | `trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | ❌ | — | 271 | 7.5% | 7.2% | 5.2% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $140 |
| 11 | `trend_donchian,fade_breakout_4h` | ❌ | 438 | 276 | 7.5% | 7.7% | 16.5% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $607 |
| 12 | `trend_donchian,fade_breakout_4h,fvg_range_15m` | ❌ | 432 | 287 | 7.5% | 8.3% | 15.5% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $656 |
| 13 | `fade_breakout_4h,fvg_range_15m` | ❌ | — | 156 | 0.0% | 8.7% | 12.8% | — | — | $166 |
| 14 | `fade_breakout_4h,squeeze_breakout_4h` | ❌ | — | 137 | 3.7% | 10.8% | 12.2% | — | — | $-113 |
| 15 | `fade_breakout_4h` | ❌ | — | 119 | 5.8% | 11.1% | 14.2% | — | — | $-232 |
| 16 | `trend_donchian,fvg_range_15m,ict_scalp_5m` | ❌ | — | 366 | 18.3% | 20.7% | 15.5% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-851 |
| 17 | `trend_donchian,ict_scalp_5m` | ❌ | — | 358 | 20.1% | 22.4% | 16.5% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-958 |
| 18 | `trend_donchian,fade_breakout_4h,fvg_range_15m,ict_scalp_5m` | ❌ | — | 400 | 17.9% | 22.5% | 14.0% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-792 |
| 19 | `trend_donchian,fade_breakout_4h,ict_scalp_5m` | ❌ | — | 393 | 19.4% | 22.7% | 14.8% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-883 |
| 20 | `trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` | ❌ | — | 375 | 24.0% | 24.4% | 4.4% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-1,141 |
| 21 | `trend_donchian,fade_breakout_4h,squeeze_breakout_4h,ict_scalp_5m` | ❌ | — | 369 | 25.1% | 25.4% | 4.6% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-1,182 |
| 22 | `trend_donchian,squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` | ❌ | — | 343 | 25.5% | 25.8% | 4.0% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-1,253 |
| 23 | `trend_donchian,squeeze_breakout_4h,ict_scalp_5m` | ❌ | — | 336 | 26.9% | 27.2% | 4.2% | — | max_drawdown @ 2023-01-27 16:00:00+00:00 | $-1,310 |
| 24 | `squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` | ❌ | — | 395 | 71.3% | 71.7% | 6.0% | — | max_drawdown @ 2023-10-03 05:00:00+00:00 | $-3,551 |
| 25 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` | ❌ | — | 437 | 72.0% | 72.8% | 5.1% | — | daily_loss @ 2023-11-07 15:00:00+00:00 | $-3,578 |
| 26 | `squeeze_breakout_4h,ict_scalp_5m` | ❌ | — | 380 | 73.1% | 73.4% | 5.8% | — | max_drawdown @ 2023-10-03 05:00:00+00:00 | $-3,639 |
| 27 | `fade_breakout_4h,squeeze_breakout_4h,ict_scalp_5m` | ❌ | — | 424 | 73.8% | 74.5% | 5.7% | — | daily_loss @ 2023-11-07 15:00:00+00:00 | $-3,666 |
| 28 | `fade_breakout_4h,fvg_range_15m,ict_scalp_5m` | ❌ | — | 461 | 74.3% | 74.5% | 5.2% | — | daily_loss @ 2023-11-07 15:00:00+00:00 | $-3,691 |
| 29 | `fvg_range_15m,ict_scalp_5m` | ❌ | — | 403 | 75.7% | 75.8% | 6.5% | — | max_drawdown @ 2023-09-26 12:00:00+00:00 | $-3,771 |
| 30 | `fade_breakout_4h,ict_scalp_5m` | ❌ | — | 444 | 76.5% | 76.8% | 5.9% | — | daily_loss @ 2023-11-07 15:00:00+00:00 | $-3,805 |
| 31 | `ict_scalp_5m` | ❌ | — | 384 | 77.8% | 77.9% | 5.4% | — | max_drawdown @ 2023-09-26 12:00:00+00:00 | $-3,879 |

> **DD columns:** *Off-start DD (rule)* is measured off the **starting balance** — this is the measure the static-DD pass/fail verdict is based on. *Peak-trough DD* is the engine's secondary peak-to-trough stat; it can exceed the limit on a passing combo when the deep swing happened while the account was in profit (the off-start drop stayed under the floor).

*31 combos evaluated. Headlines below.*

1. `squeeze_breakout_4h,fvg_range_15m` — EVAL PASS / FUNDED SURVIVE
2. `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` — EVAL PASS / FUNDED SURVIVE
3. `squeeze_breakout_4h` — EVAL NOT REACHED (target not hit)
4. `fvg_range_15m` — EVAL NOT REACHED (target not hit)
5. `trend_donchian,fvg_range_15m` — EVAL FAIL (max_drawdown)
6. `trend_donchian` — EVAL FAIL (max_drawdown)
7. `trend_donchian,squeeze_breakout_4h,fvg_range_15m` — EVAL FAIL (max_drawdown)
8. `trend_donchian,squeeze_breakout_4h` — EVAL FAIL (max_drawdown)
9. `trend_donchian,fade_breakout_4h,squeeze_breakout_4h` — EVAL FAIL (max_drawdown)
10. `trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` — EVAL FAIL (max_drawdown)
11. `trend_donchian,fade_breakout_4h` — EVAL FAIL (max_drawdown)
12. `trend_donchian,fade_breakout_4h,fvg_range_15m` — EVAL FAIL (max_drawdown)
13. `fade_breakout_4h,fvg_range_15m` — EVAL NOT REACHED (target not hit)
14. `fade_breakout_4h,squeeze_breakout_4h` — EVAL NOT REACHED (target not hit)
15. `fade_breakout_4h` — EVAL NOT REACHED (target not hit)
16. `trend_donchian,fvg_range_15m,ict_scalp_5m` — EVAL FAIL (max_drawdown)
17. `trend_donchian,ict_scalp_5m` — EVAL FAIL (max_drawdown)
18. `trend_donchian,fade_breakout_4h,fvg_range_15m,ict_scalp_5m` — EVAL FAIL (max_drawdown)
19. `trend_donchian,fade_breakout_4h,ict_scalp_5m` — EVAL FAIL (max_drawdown)
20. `trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` — EVAL FAIL (max_drawdown)
21. `trend_donchian,fade_breakout_4h,squeeze_breakout_4h,ict_scalp_5m` — EVAL FAIL (max_drawdown)
22. `trend_donchian,squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` — EVAL FAIL (max_drawdown)
23. `trend_donchian,squeeze_breakout_4h,ict_scalp_5m` — EVAL FAIL (max_drawdown)
24. `squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` — EVAL FAIL (max_drawdown)
25. `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m,ict_scalp_5m` — EVAL FAIL (daily_loss)
26. `squeeze_breakout_4h,ict_scalp_5m` — EVAL FAIL (max_drawdown)
27. `fade_breakout_4h,squeeze_breakout_4h,ict_scalp_5m` — EVAL FAIL (daily_loss)
28. `fade_breakout_4h,fvg_range_15m,ict_scalp_5m` — EVAL FAIL (daily_loss)
29. `fvg_range_15m,ict_scalp_5m` — EVAL FAIL (max_drawdown)
30. `fade_breakout_4h,ict_scalp_5m` — EVAL FAIL (daily_loss)
31. `ict_scalp_5m` — EVAL FAIL (max_drawdown)
