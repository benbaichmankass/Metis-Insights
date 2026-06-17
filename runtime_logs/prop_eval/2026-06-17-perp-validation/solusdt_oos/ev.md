# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:33:53.047094+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $432 | $406.05 | $-135 | $1323.58 | 59% | 3.73 | $168 | 2.573 |
| trend_donchian | 1.0 | $202 | $-45 | $-90 | $860.38 | 39% | 1.97 | $88 | 2.288 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,037 | $1019.48 | $-90 | $2203.74 | 90% | 3.73 | $168 | 6.172 |
| trend_donchian | 1.0 | $604 | $630.25 | $-90 | $1410.17 | 76% | 1.97 | $88 | 6.833 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $2,228 | $2230.6 | $716.51 | $3752.92 | 99% | 3.73 | $168 | 13.255 |
| trend_donchian | 1.0 | $1,339 | $1345.05 | $330.31 | $2374.55 | 97% | 1.97 | $88 | 15.136 |
