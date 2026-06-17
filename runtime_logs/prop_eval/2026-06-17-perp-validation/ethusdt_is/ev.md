# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:35:50.302135+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $140 | $-45 | $-135 | $864.98 | 35% | 4.92 | $221 | 0.633 |
| trend_donchian | 1.0 | $28 | $-45 | $-90 | $537.24 | 17% | 2.82 | $127 | 0.222 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $409 | $366.4 | $-225 | $1338.41 | 66% | 4.92 | $221 | 1.849 |
| trend_donchian | 1.0 | $177 | $-45 | $-135 | $846.14 | 42% | 2.82 | $127 | 1.397 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $949 | $893.28 | $-225 | $2228.93 | 90% | 4.92 | $221 | 4.289 |
| trend_donchian | 1.0 | $474 | $442.65 | $-180 | $1364.43 | 73% | 2.82 | $127 | 3.73 |
