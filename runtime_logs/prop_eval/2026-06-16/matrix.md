# Prop-firm evaluation matrix

- **Ruleset:** `breakout`
- **Limits:** profit target 10.0%, daily-loss 3.0%, max-DD 6.0% (static), funded soak 30d
- **Data window:** None → None (2023-01-01 00:00:00+00:00 → 2026-03-01 00:00:00+00:00)
- **Generated:** 2026-06-16T15:28:51.159399+00:00

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

> **DD columns:** *Off-start DD (rule)* is measured off the **starting balance** — this is the measure the static-DD pass/fail verdict is based on. *Peak-trough DD* is the engine's secondary peak-to-trough stat; it can exceed the limit on a passing combo when the deep swing happened while the account was in profit (the off-start drop stayed under the floor).

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
