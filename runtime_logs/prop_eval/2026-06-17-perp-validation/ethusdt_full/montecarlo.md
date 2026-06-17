# Prop-firm Monte-Carlo — survival + speed sweep

_Generated 2026-06-17T07:34:41.530990+00:00_

**Ruleset:** `breakout` / `1-step-classic` — account $5,000, target 10%, daily-loss 3%, static DD 6%.

**Method:** block-bootstrap (3000 paths, block_len 8, seed 1234) of each combo's real per-trade ledger, walked as a fresh $5,000 account compounded at each `risk_pct`. Engine base risk_pct 0.5; ledger rescaled to each cell's risk via sizing-independent R-multiples.

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold, reentry suppress).

> **Honesty caveat (daily-loss is OPTIMISTIC):** a per-trade bootstrap has no intraday open-position equity swing, so the daily-loss (and static-DD) checks see only REALISED closed-trade P&L per synthetic day. Breakout's real daily-loss fires on intraday equity incl. open positions — so P(breach by daily_loss) here UNDER-counts. Backtest ≠ funded reality (slippage/fills/funding).

| combo | risk | P(pass) | days→pass (med / p5–p95) | P(breach) | by cause | P(surv 3mo) | P(surv 6mo) | P(surv 12mo) | end ret (med) |
|---|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.0 | 54% | 105.9 / 31.9–330.7 | 50% | daily 0%, static 50% | 73.87% | 61.87% | 53.2% | -5.9% |
| trend_donchian | 1.5 | 44% | 52.2 / 11–157.9 | 87% | daily 40%, static 46% | 50.13% | 35.2% | 21.53% | -4.8% |
