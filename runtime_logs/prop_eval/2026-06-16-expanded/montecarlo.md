# Prop-firm Monte-Carlo — survival + speed sweep

_Generated 2026-06-16T17:15:22.795542+00:00_

**Ruleset:** `breakout` / `1-step-classic` — account $5,000, target 10%, daily-loss 3%, static DD 6%.

**Method:** block-bootstrap (5000 paths, block_len 8, seed 1234) of each combo's real per-trade ledger, walked as a fresh $5,000 account compounded at each `risk_pct`. Engine base risk_pct 0.5; ledger rescaled to each cell's risk via sizing-independent R-multiples.

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold, reentry suppress).

> **Honesty caveat (daily-loss is OPTIMISTIC):** a per-trade bootstrap has no intraday open-position equity swing, so the daily-loss (and static-DD) checks see only REALISED closed-trade P&L per synthetic day. Breakout's real daily-loss fires on intraday equity incl. open positions — so P(breach by daily_loss) here UNDER-counts. Backtest ≠ funded reality (slippage/fills/funding).

| combo | risk | P(pass) | days→pass (med / p5–p95) | P(breach) | by cause | P(surv 3mo) | P(surv 6mo) | P(surv 12mo) | end ret (med) |
|---|---|---|---|---|---|---|---|---|---|---|
| ict_scalp_5m | 0.3 | 0% | — | 100% | daily 7%, static 93% | 30.58% | 2.76% | 0.02% | -6.3% |
| ict_scalp_5m | 0.5 | 0% | 31.2 / 28.9–38.9 | 100% | daily 21%, static 79% | 9.7% | 0.58% | 0% | -6.3% |
| ict_scalp_5m | 0.6 | 0% | 46.1 / 24.6–109 | 100% | daily 28%, static 72% | 6.24% | 0.24% | 0% | -6.4% |
| ict_scalp_5m | 0.75 | 1% | 31.7 / 6.8–81.3 | 100% | daily 22%, static 78% | 3.96% | 0.18% | 0% | -6.5% |
| ict_scalp_5m | 1.0 | 1% | 25.8 / 3.7–68.8 | 100% | daily 28%, static 72% | 1.84% | 0.12% | 0% | -6.5% |
| ict_scalp_5m,squeeze_breakout_4h,fvg_range_15m | 0.3 | 0% | 89.5 / 89.5–89.5 | 100% | daily 9%, static 91% | 43.52% | 10.18% | 0.44% | -6.3% |
| ict_scalp_5m,squeeze_breakout_4h,fvg_range_15m | 0.5 | 1% | 54.1 / 16.8–133 | 100% | daily 24%, static 76% | 16.96% | 1.88% | 0.06% | -6.3% |
| ict_scalp_5m,squeeze_breakout_4h,fvg_range_15m | 0.6 | 2% | 24.1 / 0.6–116 | 100% | daily 31%, static 69% | 10.72% | 1.08% | 0.02% | -6.3% |
| ict_scalp_5m,squeeze_breakout_4h,fvg_range_15m | 0.75 | 3% | 20.6 / 0.6–100.4 | 100% | daily 26%, static 74% | 7.44% | 0.6% | 0% | -6.4% |
| ict_scalp_5m,squeeze_breakout_4h,fvg_range_15m | 1.0 | 4% | 18.3 / 0.6–77.8 | 100% | daily 35%, static 65% | 3.56% | 0.2% | 0% | -6.3% |
| ict_scalp_5m,squeeze_breakout_4h | 0.3 | 0% | 74.2 / 74.2–74.2 | 100% | daily 8%, static 92% | 40.06% | 7% | 0.18% | -6.3% |
| ict_scalp_5m,squeeze_breakout_4h | 0.5 | 0% | 72.1 / 19.2–132.8 | 100% | daily 22%, static 78% | 14.52% | 1.16% | 0.04% | -6.3% |
| ict_scalp_5m,squeeze_breakout_4h | 0.6 | 1% | 49.9 / 12.9–118.4 | 100% | daily 30%, static 70% | 9.32% | 0.58% | 0% | -6.3% |
| ict_scalp_5m,squeeze_breakout_4h | 0.75 | 1% | 21.9 / 6.8–91.9 | 100% | daily 24%, static 76% | 6.22% | 0.3% | 0% | -6.4% |
| ict_scalp_5m,squeeze_breakout_4h | 1.0 | 3% | 20.7 / 3.7–73.5 | 100% | daily 31%, static 69% | 3% | 0.08% | 0% | -6.4% |
| ict_scalp_5m,fvg_range_15m | 0.3 | 0% | — | 100% | daily 8%, static 92% | 35.36% | 5.36% | 0.2% | -6.3% |
| ict_scalp_5m,fvg_range_15m | 0.5 | 0% | 49.2 / 24.2–158.1 | 100% | daily 24%, static 76% | 11.3% | 1.04% | 0.02% | -6.3% |
| ict_scalp_5m,fvg_range_15m | 0.6 | 1% | 32 / 0.6–137.3 | 100% | daily 30%, static 70% | 7.5% | 0.6% | 0.02% | -6.3% |
| ict_scalp_5m,fvg_range_15m | 0.75 | 2% | 13.1 / 0.6–74.3 | 100% | daily 25%, static 75% | 5.02% | 0.42% | 0% | -6.4% |
| ict_scalp_5m,fvg_range_15m | 1.0 | 4% | 19.1 / 3.2–60.8 | 100% | daily 31%, static 69% | 2.26% | 0.16% | 0% | -6.5% |
| ict_scalp_5m,fvg_range_15m,squeeze_breakout_4h,fade_breakout_4h | 0.3 | 0% | 68.7 / 28.3–179.5 | 100% | daily 4%, static 96% | 43.02% | 10.42% | 0.64% | -6.3% |
| ict_scalp_5m,fvg_range_15m,squeeze_breakout_4h,fade_breakout_4h | 0.5 | 2% | 50.1 / 17.6–156.4 | 100% | daily 20%, static 80% | 16.42% | 2.3% | 0.12% | -6.3% |
| ict_scalp_5m,fvg_range_15m,squeeze_breakout_4h,fade_breakout_4h | 0.6 | 3% | 30 / 0.6–128.1 | 100% | daily 27%, static 73% | 10.38% | 1.38% | 0.02% | -6.3% |
| ict_scalp_5m,fvg_range_15m,squeeze_breakout_4h,fade_breakout_4h | 0.75 | 5% | 21.4 / 0.6–112 | 100% | daily 25%, static 75% | 7% | 0.86% | 0.02% | -6.3% |
| ict_scalp_5m,fvg_range_15m,squeeze_breakout_4h,fade_breakout_4h | 1.0 | 7% | 19 / 2.7–72.5 | 100% | daily 37%, static 63% | 3.34% | 0.22% | 0% | -6.3% |
| turtle_soup | 0.3 | 0% | — | 100% | daily 13%, static 87% | 77.22% | 49.02% | 13.32% | -6.3% |
| turtle_soup | 0.5 | 0% | 288.2 / 288.2–288.2 | 100% | daily 38%, static 62% | 50.66% | 20.74% | 3.04% | -6.2% |
| turtle_soup | 0.6 | 0% | 160.5 / 98–239.3 | 100% | daily 40%, static 60% | 44.6% | 15.72% | 1.8% | -6.2% |
| turtle_soup | 0.75 | 0% | 139.4 / 63.9–264.7 | 100% | daily 35%, static 65% | 37.04% | 11.62% | 1.1% | -6.3% |
| turtle_soup | 1.0 | 1% | 95.8 / 44–235.4 | 100% | daily 43%, static 57% | 26.04% | 6.34% | 0.46% | -6.3% |
| ict_scalp_5m,turtle_soup | 0.3 | 0% | — | 100% | daily 10%, static 90% | 14.18% | 0.56% | 0% | -6.3% |
| ict_scalp_5m,turtle_soup | 0.5 | 0% | 27.2 / 27.2–27.2 | 100% | daily 25%, static 75% | 2.68% | 0.02% | 0% | -6.3% |
| ict_scalp_5m,turtle_soup | 0.6 | 0% | 15.6 / 12.5–19.6 | 100% | daily 31%, static 69% | 1.36% | 0% | 0% | -6.3% |
| ict_scalp_5m,turtle_soup | 0.75 | 0% | 13.2 / 7.2–65.5 | 100% | daily 31%, static 69% | 0.78% | 0% | 0% | -6.4% |
| ict_scalp_5m,turtle_soup | 1.0 | 1% | 17 / 3.7–46.3 | 100% | daily 32%, static 68% | 0.3% | 0% | 0% | -6.5% |
| squeeze_breakout_4h,fvg_range_15m | 0.3 | 23% | 472.7 / 199.5–687.2 | 0% | static 0% | 100% | 100% | 100% | 5.5% |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | 55% | 375.7 / 109.6–635.8 | 4% | static 4% | 100% | 99.94% | 97.6% | 9.2% |
| squeeze_breakout_4h,fvg_range_15m | 0.6 | 64% | 327.2 / 41.2–614.8 | 7% | static 7% | 100% | 99.46% | 95.2% | 11.0% |
| squeeze_breakout_4h,fvg_range_15m | 0.75 | 71% | 275.4 / 41.6–595.8 | 13% | static 13% | 99.86% | 96.3% | 90.22% | 13.5% |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | 72% | 204.1 / 41.6–536 | 22% | static 22% | 99.54% | 88.4% | 81.48% | 16.7% |
| fvg_range_15m | 0.3 | 46% | 641.8 / 332.6–902 | 0% | static 0% | 100% | 100% | 100% | 8.7% |
| fvg_range_15m | 0.5 | 81% | 464.1 / 170.6–835.1 | 1% | static 1% | 100% | 100% | 99.56% | 14.8% |
| fvg_range_15m | 0.6 | 87% | 394 / 108.2–799.5 | 2% | static 2% | 100% | 99.58% | 98.84% | 17.8% |
| fvg_range_15m | 0.75 | 90% | 314.8 / 45.6–743.4 | 4% | static 4% | 100% | 98.52% | 97% | 22.4% |
| fvg_range_15m | 1.0 | 90% | 248.6 / 45.6–646.1 | 8% | static 8% | 100% | 96.18% | 93.66% | 30.0% |
