# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:33:40.115967+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $213 | $-45 | $-180 | $1400.12 | 29% | 4.66 | $210 | 1.016 |
| trend_donchian | 1.0 | $114 | $-45 | $-90 | $926.29 | 21% | 2.32 | $104 | 1.089 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $563 | $334.15 | $-225 | $2184.62 | 56% | 4.66 | $210 | 2.684 |
| trend_donchian | 1.0 | $339 | $-45 | $-135 | $1450.44 | 45% | 2.32 | $104 | 3.243 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,256 | $1095.66 | $-270 | $3555.82 | 83% | 4.66 | $210 | 5.985 |
| trend_donchian | 1.0 | $781 | $702.52 | $-135 | $2278.77 | 74% | 2.32 | $104 | 7.483 |
