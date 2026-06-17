# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:34:41.530990+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $199 | $-45 | $-135 | $1000.87 | 40% | 4.39 | $198 | 1.008 |
| trend_donchian | 1.0 | $62 | $-45 | $-90 | $644.97 | 21% | 2.38 | $107 | 0.574 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $546 | $513.76 | $-180 | $1557.05 | 74% | 4.39 | $198 | 2.764 |
| trend_donchian | 1.0 | $261 | $-45 | $-135 | $985.57 | 49% | 2.38 | $107 | 2.431 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,239 | $1210.87 | $-135 | $2555.92 | 94% | 4.39 | $198 | 6.271 |
| trend_donchian | 1.0 | $673 | $659.93 | $-135 | $1569.76 | 82% | 2.38 | $107 | 6.27 |
