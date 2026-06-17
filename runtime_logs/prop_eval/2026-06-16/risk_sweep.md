# Prop-firm risk-pct sweep — Breakout 1-Step Classic ($5,000)

- **Generated:** 2026-06-16T15:49:33.472232+00:00
- **Ruleset:** breakout 1-step-classic (10% target / 3% daily / 6% static DD off start)
- **Account:** $5,000  · **clock-tf:** 1h  · **flip-policy:** hold  · **daily-loss halt:** 3%
- **Data:** BTCUSDT 5m 2023-01-01 → 2026-02-28 (multiyear feed, outside repo)
- **Risk levels:** 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0

> **DD = off the STARTING balance** (the Breakout static-6% rule measure), NOT peak-to-trough. Peak-trough shown as a secondary stat. `eval_pass` = equity crossed +10% at some point in-window (clears the one-phase eval); `return%`/`net$` are END-of-window stats, so a low-quality pass can scrape +10% then give it back (see the note).

## Headline: cells that clear +10% with off-start DD < 6%, ranked by days-to-target

| Rank | Roster | risk_pct | Days→target | Off-start DD | Peak-trough DD | Return% (end) | Net $ (end) | Trades | Win% |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.75 | 120 | 0.08% | 22.99% | 8.04% | $402 | 182 | 36.26% |
| 2 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.6 | 164 | 0.07% | 18.83% | 6.75% | $337 | 182 | 36.26% |
| 3 | `fade_breakout_4h,fvg_range_15m` | 0.75 | 219 | 0.0% | 20.39% | 7.15% | $358 | 172 | 37.79% |
| 4 | `squeeze_breakout_4h` | 1.0 | 403 | 0.8% | 7.4% | 12.15% | $607 | 56 | 42.86% |
| 5 | `squeeze_breakout_4h,fvg_range_15m` | 0.75 | 404 | 0.58% | 8.15% | 26.01% | $1,300 | 91 | 43.96% |
| 6 | `squeeze_breakout_4h,fvg_range_15m` | 0.6 | 432 | 0.45% | 6.56% | 20.6% | $1,030 | 91 | 43.96% |
| 7 | `fade_breakout_4h,fvg_range_15m` | 0.6 | 438 | 0.0% | 16.65% | 6.03% | $301 | 172 | 37.79% |
| 8 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.5 | 438 | 0.06% | 15.94% | 5.8% | $290 | 182 | 36.26% |
| 9 | `fade_breakout_4h,fvg_range_15m` | 0.5 | 439 | 0.0% | 14.07% | 5.19% | $260 | 172 | 37.79% |
| 10 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.4 | 439 | 0.05% | 12.95% | 4.78% | $239 | 182 | 36.26% |
| 11 | `fade_breakout_4h,fvg_range_15m` | 0.4 | 512 | 0.0% | 11.42% | 4.29% | $214 | 172 | 37.79% |
| 12 | `squeeze_breakout_4h,fvg_range_15m` | 0.5 | 512 | 0.37% | 5.5% | 17.04% | $852 | 91 | 43.96% |
| 13 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.3 | 519 | 0.03% | 9.87% | 3.69% | $184 | 182 | 36.26% |
| 14 | `fade_breakout_4h,squeeze_breakout_4h` | 0.4 | 519 | 5.02% | 14.22% | -3.07% | $-154 | 149 | 34.23% |
| 15 | `squeeze_breakout_4h` | 0.75 | 540 | 0.58% | 5.7% | 9.13% | $456 | 56 | 42.86% |
| 16 | `fvg_range_15m` | 0.5 | 673 | 0.06% | 4.34% | 13.27% | $664 | 45 | 51.11% |
| 17 | `fvg_range_15m` | 0.6 | 673 | 0.07% | 5.17% | 15.99% | $800 | 45 | 51.11% |
| 18 | `fvg_range_15m` | 0.75 | 673 | 0.09% | 6.39% | 20.11% | $1,005 | 45 | 51.11% |
| 19 | `squeeze_breakout_4h,fvg_range_15m` | 0.3 | 673 | 0.22% | 3.33% | 10.08% | $504 | 91 | 43.96% |
| 20 | `squeeze_breakout_4h,fvg_range_15m` | 0.4 | 673 | 0.29% | 4.42% | 13.54% | $677 | 91 | 43.96% |
| 21 | `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.25 | 679 | 0.03% | 8.29% | 3.12% | $156 | 182 | 36.26% |
| 22 | `fvg_range_15m` | 0.4 | 1051 | 0.04% | 3.5% | 10.57% | $529 | 45 | 51.11% |

## Full sweep (all 70 cells)

| Roster | risk_pct | Off-start DD | Peak-trough DD | Return% | Net $ | Days→target | Eval pass | Funded survive | First breach |
|---|---|---|---|---|---|---|---|---|---|
| `fade_breakout_4h` | 0.1 | 1.92% | 3.84% | -1.54% | $-77 | — | ❌ | ❌ | — |
| `fade_breakout_4h` | 0.15 | 2.88% | 5.71% | -2.31% | $-116 | — | ❌ | ❌ | — |
| `fade_breakout_4h` | 0.2 | 3.84% | 7.55% | -3.09% | $-154 | — | ❌ | ❌ | — |
| `fade_breakout_4h` | 0.25 | 4.8% | 9.35% | -3.87% | $-193 | — | ❌ | ❌ | — |
| `fade_breakout_4h` | 0.3 | 5.75% | 11.12% | -4.65% | $-232 | — | ❌ | ❌ | — |
| `fade_breakout_4h` | 0.4 | 7.66% | 14.57% | -6.22% | $-311 | — | ❌ | ❌ | max_drawdown |
| `fade_breakout_4h` | 0.5 | 9.55% | 17.89% | -7.79% | $-389 | 519 | ❌ | ❌ | max_drawdown |
| `fade_breakout_4h` | 0.6 | 11.44% | 21.09% | -9.37% | $-468 | 497 | ❌ | ❌ | max_drawdown |
| `fade_breakout_4h` | 0.75 | 14.25% | 25.68% | -11.74% | $-587 | 219 | ❌ | ❌ | max_drawdown |
| `fade_breakout_4h` | 1.0 | 18.86% | 32.79% | -15.69% | $-785 | 120 | ❌ | ❌ | daily_loss |
| `fade_breakout_4h,fvg_range_15m` | 0.1 | 0.0% | 2.98% | 1.17% | $59 | — | ❌ | ❌ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.15 | 0.0% | 4.43% | 1.73% | $87 | — | ❌ | ❌ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.2 | 0.0% | 5.87% | 2.28% | $114 | — | ❌ | ❌ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.25 | 0.0% | 7.29% | 2.8% | $140 | — | ❌ | ❌ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.3 | 0.0% | 8.68% | 3.32% | $166 | — | ❌ | ❌ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.4 | 0.0% | 11.42% | 4.29% | $214 | 512 | ✅ | ✅ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.5 | 0.0% | 14.07% | 5.19% | $260 | 439 | ✅ | ✅ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.6 | 0.0% | 16.65% | 6.03% | $301 | 438 | ✅ | ✅ | — |
| `fade_breakout_4h,fvg_range_15m` | 0.75 | 0.0% | 20.39% | 7.15% | $358 | 219 | ✅ | ✅ | — |
| `fade_breakout_4h,fvg_range_15m` | 1.0 | 0.72% | 26.26% | 8.67% | $434 | 120 | ❌ | ❌ | daily_loss |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.1 | 1.22% | 3.75% | -0.72% | $-36 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.15 | 1.84% | 5.57% | -1.09% | $-54 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.2 | 2.47% | 7.36% | -1.47% | $-73 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.25 | 3.1% | 9.12% | -1.86% | $-93 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.3 | 3.73% | 10.85% | -2.25% | $-113 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.4 | 5.02% | 14.22% | -3.07% | $-154 | 519 | ✅ | ✅ | — |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.5 | 6.32% | 17.47% | -3.92% | $-196 | 439 | ❌ | ❌ | max_drawdown |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.6 | 7.65% | 20.6% | -4.81% | $-240 | 164 | ❌ | ❌ | max_drawdown |
| `fade_breakout_4h,squeeze_breakout_4h` | 0.75 | 9.66% | 25.1% | -6.18% | $-309 | 120 | ❌ | ❌ | max_drawdown |
| `fade_breakout_4h,squeeze_breakout_4h` | 1.0 | 13.08% | 32.06% | -8.62% | $-431 | 100 | ❌ | ❌ | daily_loss |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.1 | 0.01% | 3.4% | 1.3% | $65 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.15 | 0.02% | 5.05% | 1.92% | $96 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.2 | 0.02% | 6.69% | 2.53% | $126 | — | ❌ | ❌ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.25 | 0.03% | 8.29% | 3.12% | $156 | 679 | ✅ | ✅ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.3 | 0.03% | 9.87% | 3.69% | $184 | 519 | ✅ | ✅ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.4 | 0.05% | 12.95% | 4.78% | $239 | 439 | ✅ | ✅ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.5 | 0.06% | 15.94% | 5.8% | $290 | 438 | ✅ | ✅ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.6 | 0.07% | 18.83% | 6.75% | $337 | 164 | ✅ | ✅ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 0.75 | 0.08% | 22.99% | 8.04% | $402 | 120 | ✅ | ✅ | — |
| `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` | 1.0 | 0.11% | 29.49% | 9.81% | $490 | 100 | ❌ | ❌ | daily_loss |
| `fvg_range_15m` | 0.1 | 0.01% | 0.9% | 2.61% | $130 | — | ❌ | ❌ | — |
| `fvg_range_15m` | 0.15 | 0.01% | 1.34% | 3.92% | $196 | — | ❌ | ❌ | — |
| `fvg_range_15m` | 0.2 | 0.02% | 1.78% | 5.24% | $262 | — | ❌ | ❌ | — |
| `fvg_range_15m` | 0.25 | 0.03% | 2.21% | 6.56% | $328 | — | ❌ | ❌ | — |
| `fvg_range_15m` | 0.3 | 0.03% | 2.64% | 7.89% | $395 | — | ❌ | ❌ | — |
| `fvg_range_15m` | 0.4 | 0.04% | 3.5% | 10.57% | $529 | 1051 | ✅ | ✅ | — |
| `fvg_range_15m` | 0.5 | 0.06% | 4.34% | 13.27% | $664 | 673 | ✅ | ✅ | — |
| `fvg_range_15m` | 0.6 | 0.07% | 5.17% | 15.99% | $800 | 673 | ✅ | ✅ | — |
| `fvg_range_15m` | 0.75 | 0.09% | 6.39% | 20.11% | $1,005 | 673 | ✅ | ✅ | — |
| `fvg_range_15m` | 1.0 | 0.13% | 8.36% | 27.07% | $1,353 | 673 | ❌ | ❌ | daily_loss |
| `squeeze_breakout_4h` | 0.1 | 0.07% | 0.82% | 1.22% | $61 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.15 | 0.11% | 1.22% | 1.83% | $92 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.2 | 0.14% | 1.61% | 2.44% | $122 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.25 | 0.18% | 2.01% | 3.05% | $153 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.3 | 0.22% | 2.39% | 3.66% | $183 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.4 | 0.29% | 3.16% | 4.88% | $244 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.5 | 0.37% | 3.9% | 6.1% | $305 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.6 | 0.45% | 4.63% | 7.31% | $366 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h` | 0.75 | 0.58% | 5.7% | 9.13% | $456 | 540 | ✅ | ✅ | — |
| `squeeze_breakout_4h` | 1.0 | 0.8% | 7.4% | 12.15% | $607 | 403 | ✅ | ✅ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.1 | 0.07% | 1.12% | 3.31% | $166 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.15 | 0.11% | 1.68% | 4.98% | $249 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.2 | 0.14% | 2.23% | 6.67% | $334 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.25 | 0.18% | 2.78% | 8.37% | $418 | — | ❌ | ❌ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.3 | 0.22% | 3.33% | 10.08% | $504 | 673 | ✅ | ✅ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.4 | 0.29% | 4.42% | 13.54% | $677 | 673 | ✅ | ✅ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.5 | 0.37% | 5.5% | 17.04% | $852 | 512 | ✅ | ✅ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.6 | 0.45% | 6.56% | 20.6% | $1,030 | 432 | ✅ | ✅ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 0.75 | 0.58% | 8.15% | 26.01% | $1,300 | 404 | ✅ | ✅ | — |
| `squeeze_breakout_4h,fvg_range_15m` | 1.0 | 0.8% | 10.74% | 35.24% | $1,762 | 356 | ❌ | ❌ | daily_loss |
