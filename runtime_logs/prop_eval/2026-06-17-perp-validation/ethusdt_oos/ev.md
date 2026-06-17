# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:36:04.298306+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $372 | $337.09 | $-90 | $1192.3 | 56% | 2.9 | $130 | 2.856 |
| trend_donchian | 1.0 | $153 | $-45 | $-90 | $782.42 | 33% | 1.64 | $74 | 2.072 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $933 | $917.35 | $-90 | $1934.18 | 89% | 2.9 | $130 | 7.154 |
| trend_donchian | 1.0 | $536 | $564 | $-90 | $1243.69 | 74% | 1.64 | $74 | 7.278 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,994 | $1969.13 | $656.44 | $3391.22 | 100% | 2.9 | $130 | 15.294 |
| trend_donchian | 1.0 | $1,205 | $1195.31 | $308.76 | $2160.39 | 97% | 1.64 | $74 | 16.361 |
