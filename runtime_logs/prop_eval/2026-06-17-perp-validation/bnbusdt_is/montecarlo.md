# Prop-firm Monte-Carlo — survival + speed sweep

_Generated 2026-06-17T07:36:28.950782+00:00_

**Ruleset:** `breakout` / `1-step-classic` — account $5,000, target 10%, daily-loss 3%, static DD 6%.

**Method:** block-bootstrap (3000 paths, block_len 8, seed 1234) of each combo's real per-trade ledger, walked as a fresh $5,000 account compounded at each `risk_pct`. Engine base risk_pct 0.5; ledger rescaled to each cell's risk via sizing-independent R-multiples.

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold, reentry suppress).

> **Honesty caveat (daily-loss is OPTIMISTIC):** a per-trade bootstrap has no intraday open-position equity swing, so the daily-loss (and static-DD) checks see only REALISED closed-trade P&L per synthetic day. Breakout's real daily-loss fires on intraday equity incl. open positions — so P(breach by daily_loss) here UNDER-counts. Backtest ≠ funded reality (slippage/fills/funding).

| combo | risk | P(pass) | days→pass (med / p5–p95) | P(breach) | by cause | P(surv 3mo) | P(surv 6mo) | P(surv 12mo) | end ret (med) |
|---|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.0 | 25% | 59.2 / 3.4–230.6 | 86% | daily 0%, static 86% | 51.17% | 33.2% | 19.97% | -6.4% |
| trend_donchian | 1.5 | 18% | 33.2 / 3.4–112.6 | 100% | daily 38%, static 62% | 20.63% | 7.97% | 1.33% | -6.2% |
