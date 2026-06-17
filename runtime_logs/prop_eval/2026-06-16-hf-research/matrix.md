# Prop-firm evaluation matrix

- **Ruleset:** `breakout`
- **Limits:** profit target 10.0%, daily-loss 3.0%, max-DD 6.0% (static), funded soak 30d
- **Data window:** 2025-02-01 → 2026-02-01 (2025-02-01 00:00:00+00:00 → 2026-02-01 00:00:00+00:00)
- **Generated:** 2026-06-16T19:46:38.765838+00:00

| Rank | Roster | Eval pass | Days→target | Active days | Off-start DD (rule) | Peak-trough DD | Consistency worst-day | Funded survive | First breach | Net $ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `hf_vwap_revert` | ❌ | — | 0 | 0.0% | 0.0% | — | — | — | $0 |
| 2 | `hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h` | ❌ | — | 27 | 3.5% | 4.8% | 31.2% | — | — | $9 |
| 3 | `fvg_range_15m,squeeze_breakout_4h` | ❌ | — | 27 | 3.5% | 4.8% | 31.2% | — | — | $9 |
| 4 | `hf_displacement_cont` | ❌ | — | 54 | 13.5% | 15.6% | 8.7% | — | max_drawdown @ 2025-11-17 13:00:00+00:00 | $-677 |
| 5 | `hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h` | ❌ | — | 69 | 14.4% | 16.8% | 8.8% | — | max_drawdown @ 2025-10-27 14:00:00+00:00 | $-719 |

> **DD columns:** *Off-start DD (rule)* is measured off the **starting balance** — this is the measure the static-DD pass/fail verdict is based on. *Peak-trough DD* is the engine's secondary peak-to-trough stat; it can exceed the limit on a passing combo when the deep swing happened while the account was in profit (the off-start drop stayed under the floor).

*5 combos evaluated. Headlines below.*

1. `hf_vwap_revert` — EVAL NOT REACHED (target not hit)
2. `hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h` — EVAL NOT REACHED (target not hit)
3. `fvg_range_15m,squeeze_breakout_4h` — EVAL NOT REACHED (target not hit)
4. `hf_displacement_cont` — EVAL FAIL (max_drawdown)
5. `hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h` — EVAL FAIL (max_drawdown)
