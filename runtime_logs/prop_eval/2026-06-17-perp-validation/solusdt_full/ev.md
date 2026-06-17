# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:33:05.532143+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $302 | $-45 | $-135 | $1563.99 | 40% | 4.46 | $201 | 1.506 |
| trend_donchian | 1.0 | $151 | $-45 | $-90 | $1013.2 | 26% | 2.15 | $97 | 1.559 |
| trend_donchian | 0.5 | $4 | $-45 | $-45 | $453.24 | 9% | 1.17 | $53 | 0.082 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $766 | $629.2 | $-180 | $2390.35 | 71% | 4.46 | $201 | 3.814 |
| trend_donchian | 1.0 | $455 | $381.7 | $-90 | $1552.9 | 57% | 2.15 | $97 | 4.691 |
| trend_donchian | 0.5 | $119 | $-45 | $-90 | $732.87 | 27% | 1.17 | $53 | 2.251 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,670 | $1543.65 | $-178.84 | $3900.26 | 93% | 4.46 | $201 | 8.321 |
| trend_donchian | 1.0 | $1,031 | $939.51 | $-135 | $2521.89 | 85% | 2.15 | $97 | 10.64 |
| trend_donchian | 0.5 | $387 | $401.66 | $-90 | $1168.5 | 58% | 1.17 | $53 | 7.344 |
