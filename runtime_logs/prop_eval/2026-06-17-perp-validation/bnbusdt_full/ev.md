# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:35:18.776264+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $101 | $-90 | $-180 | $886.7 | 30% | 6.56 | $295 | 0.343 |
| trend_donchian | 1.0 | $16 | $-45 | $-135 | $559.6 | 14% | 3.32 | $150 | 0.104 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $320 | $233.47 | $-270 | $1370.01 | 58% | 6.56 | $295 | 1.084 |
| trend_donchian | 1.0 | $141 | $-45 | $-180 | $851.68 | 36% | 3.32 | $150 | 0.939 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $748 | $666.22 | $-360 | $2157.69 | 82% | 6.56 | $295 | 2.535 |
| trend_donchian | 1.0 | $378 | $341.67 | $-225 | $1319.93 | 64% | 3.32 | $150 | 2.529 |
