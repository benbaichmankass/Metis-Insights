# Prop-firm evaluation matrix

- **Ruleset:** `breakout`
- **Limits:** profit target 10.0%, daily-loss 3.0%, max-DD 6.0% (static), funded soak 30d
- **Data window:** None → None (2023-01-01 00:00:00+00:00 → 2026-03-01 00:00:00+00:00)
- **Generated:** 2026-06-16T15:02:48.027894+00:00

| Rank | Roster | Eval pass | Days→target | Active days | Worst-DD | Consistency worst-day | Funded survive | First breach | Net $ |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `squeeze_breakout_4h,fvg_range_15m` | ✅ | 673 | 89 | 3.3% | 19.4% | ✅ | — | $2,520 |
| 2 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | ✅ | 519 | 169 | 9.9% | 12.3% | ✅ | — | $922 |
| 3 | `squeeze_breakout_4h` | ❌ | — | 55 | 2.4% | 19.7% | — | — | $915 |
| 4 | `fvg_range_15m` | ❌ | — | 44 | 2.6% | 28.7% | — | — | $1,974 |
| 5 | `trend_donchian,fvg_range_15m` | ❌ | 432 | 233 | 6.2% | 17.2% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $4,525 |
| 6 | `trend_donchian` | ❌ | 428 | 216 | 6.3% | 18.9% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $4,125 |
| 7 | `trend_donchian,squeeze_breakout_4h,fvg_range_15m` | ❌ | — | 226 | 6.9% | 4.0% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $1,387 |
| 8 | `trend_donchian,squeeze_breakout_4h` | ❌ | — | 210 | 7.0% | 3.6% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $1,317 |
| 9 | `trend_donchian,fade_breakout_4h,squeeze_breakout_4h` | ❌ | — | 260 | 7.2% | 5.5% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $759 |
| 10 | `trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | ❌ | — | 271 | 7.2% | 5.2% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $702 |
| 11 | `trend_donchian,fade_breakout_4h` | ❌ | 438 | 276 | 7.7% | 16.5% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $3,033 |
| 12 | `trend_donchian,fade_breakout_4h,fvg_range_15m` | ❌ | 432 | 287 | 8.3% | 15.5% | — | max_drawdown @ 2023-01-26 00:00:00+00:00 | $3,280 |
| 13 | `fade_breakout_4h,fvg_range_15m` | ❌ | — | 156 | 8.7% | 12.8% | — | — | $829 |
| 14 | `fade_breakout_4h,squeeze_breakout_4h` | ❌ | — | 137 | 10.8% | 12.2% | — | — | $-563 |
| 15 | `fade_breakout_4h` | ❌ | — | 119 | 11.1% | 14.2% | — | — | $-1,162 |

*15 combos evaluated. Headlines below.*

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
