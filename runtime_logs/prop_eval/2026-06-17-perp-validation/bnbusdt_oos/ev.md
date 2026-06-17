# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:36:42.217935+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $58 | $-90 | $-180 | $662.71 | 30% | 6 | $270 | 0.215 |
| trend_donchian | 1.0 | $-26 | $-45 | $-90 | $350.97 | 7% | 2.73 | $123 | -0.212 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $269 | $238.5 | $-270 | $1064.72 | 61% | 6 | $270 | 0.997 |
| trend_donchian | 1.0 | $88 | $-45 | $-135 | $676.07 | 30% | 2.73 | $123 | 0.716 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $667 | $614.5 | $-315 | $1755.37 | 84% | 6 | $270 | 2.472 |
| trend_donchian | 1.0 | $314 | $316.34 | $-180 | $1063.94 | 62% | 2.73 | $123 | 2.562 |
