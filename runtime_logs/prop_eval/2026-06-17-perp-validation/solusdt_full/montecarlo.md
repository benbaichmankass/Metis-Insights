# Prop-firm Monte-Carlo — survival + speed sweep

_Generated 2026-06-17T07:33:05.532143+00:00_

**Ruleset:** `breakout` / `1-step-classic` — account $5,000, target 10%, daily-loss 3%, static DD 6%.

**Method:** block-bootstrap (3000 paths, block_len 8, seed 1234) of each combo's real per-trade ledger, walked as a fresh $5,000 account compounded at each `risk_pct`. Engine base risk_pct 0.5; ledger rescaled to each cell's risk via sizing-independent R-multiples.

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold, reentry suppress).

> **Honesty caveat (daily-loss is OPTIMISTIC):** a per-trade bootstrap has no intraday open-position equity swing, so the daily-loss (and static-DD) checks see only REALISED closed-trade P&L per synthetic day. Breakout's real daily-loss fires on intraday equity incl. open positions — so P(breach by daily_loss) here UNDER-counts. Backtest ≠ funded reality (slippage/fills/funding).

| combo | risk | P(pass) | days→pass (med / p5–p95) | P(breach) | by cause | P(surv 3mo) | P(surv 6mo) | P(surv 12mo) | end ret (med) |
|---|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 0.5 | 73% | 221.5 / 34.7–516.9 | 14% | static 14% | 98.43% | 94.1% | 89.03% | 15.9% |
| trend_donchian | 1.0 | 65% | 96.9 / 13.5–346.1 | 38% | daily 1%, static 37% | 81.67% | 71.7% | 64.9% | 20.9% |
| trend_donchian | 1.5 | 48% | 44.9 / 7.4–168 | 96% | daily 65%, static 30% | 51% | 31.3% | 12.93% | 0.8% |
