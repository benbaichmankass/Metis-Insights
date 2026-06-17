# Prop-firm Monte-Carlo — survival + speed sweep

_Generated 2026-06-16T19:46:36.812888+00:00_

**Ruleset:** `breakout` / `1-step-classic` — account $5,000, target 10%, daily-loss 3%, static DD 6%.

**Method:** block-bootstrap (5000 paths, block_len 8, seed 1234) of each combo's real per-trade ledger, walked as a fresh $5,000 account compounded at each `risk_pct`. Engine base risk_pct 0.5; ledger rescaled to each cell's risk via sizing-independent R-multiples.

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold, reentry suppress).

> **Honesty caveat (daily-loss is OPTIMISTIC):** a per-trade bootstrap has no intraday open-position equity swing, so the daily-loss (and static-DD) checks see only REALISED closed-trade P&L per synthetic day. Breakout's real daily-loss fires on intraday equity incl. open positions — so P(breach by daily_loss) here UNDER-counts. Backtest ≠ funded reality (slippage/fills/funding).

| combo | risk | P(pass) | days→pass (med / p5–p95) | P(breach) | by cause | P(surv 3mo) | P(surv 6mo) | P(surv 12mo) | end ret (med) |
|---|---|---|---|---|---|---|---|---|---|---|
| hf_displacement_cont | 0.3 | 0% | — | 100% | static 100% | 95.78% | 73.76% | 24.04% | -6.2% |
| hf_displacement_cont | 0.5 | 0% | — | 100% | daily 0%, static 100% | 79.08% | 41.3% | 7.02% | -6.3% |
| hf_displacement_cont | 0.6 | 0% | — | 100% | static 100% | 69.06% | 31.68% | 4.42% | -6.4% |
| hf_displacement_cont | 0.75 | 0% | — | 100% | daily 16%, static 84% | 58.92% | 22.2% | 2.62% | -6.4% |
| hf_displacement_cont | 1.0 | 0% | 141.6 / 97.3–311.4 | 100% | daily 5%, static 95% | 45.9% | 14.12% | 1.64% | -6.6% |
| hf_vwap_revert | 0.3 | 0% | — | 0% | — | 0% | 0% | 0% | — |
| hf_vwap_revert | 0.5 | 0% | — | 0% | — | 0% | 0% | 0% | — |
| hf_vwap_revert | 0.6 | 0% | — | 0% | — | 0% | 0% | 0% | — |
| hf_vwap_revert | 0.75 | 0% | — | 0% | — | 0% | 0% | 0% | — |
| hf_vwap_revert | 1.0 | 0% | — | 0% | — | 0% | 0% | 0% | — |
| hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h | 0.3 | 0% | — | 100% | static 100% | 94.42% | 67.52% | 19.68% | -6.2% |
| hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h | 0.5 | 0% | — | 100% | daily 0%, static 100% | 71.74% | 34.68% | 5.44% | -6.3% |
| hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h | 0.6 | 0% | — | 100% | daily 0%, static 100% | 61.02% | 25.86% | 3.14% | -6.4% |
| hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h | 0.75 | 0% | 233.3 / 123.5–312.1 | 100% | daily 19%, static 81% | 49.36% | 16.96% | 1.5% | -6.3% |
| hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h | 1.0 | 0% | 118.1 / 84.3–323.7 | 100% | daily 8%, static 92% | 37.98% | 10.5% | 0.78% | -6.6% |
| hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h | 0.3 | 0% | — | 0% | static 0% | 100% | 100% | 100% | 0.2% |
| hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h | 0.5 | 4% | 504.8 / 319–642.4 | 11% | static 11% | 100% | 100% | 97.26% | 0.2% |
| hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h | 0.6 | 8% | 476 / 266–629.3 | 19% | static 19% | 100% | 100% | 91.64% | 0.1% |
| hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h | 0.75 | 16% | 404.6 / 184.5–611.1 | 31% | static 31% | 100% | 96.88% | 82.1% | -0.4% |
| hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h | 1.0 | 26% | 316.4 / 122.4–595.5 | 47% | daily 0%, static 47% | 100% | 85.86% | 65.6% | -3.6% |
| fvg_range_15m,squeeze_breakout_4h | 0.3 | 0% | — | 0% | static 0% | 100% | 100% | 100% | 0.2% |
| fvg_range_15m,squeeze_breakout_4h | 0.5 | 4% | 504.8 / 319–642.4 | 11% | static 11% | 100% | 100% | 97.26% | 0.2% |
| fvg_range_15m,squeeze_breakout_4h | 0.6 | 8% | 476 / 266–629.3 | 19% | static 19% | 100% | 100% | 91.64% | 0.1% |
| fvg_range_15m,squeeze_breakout_4h | 0.75 | 16% | 404.6 / 184.5–611.1 | 31% | static 31% | 100% | 96.88% | 82.1% | -0.4% |
| fvg_range_15m,squeeze_breakout_4h | 1.0 | 26% | 316.4 / 122.4–595.5 | 47% | daily 0%, static 47% | 100% | 85.86% | 65.6% | -3.6% |
