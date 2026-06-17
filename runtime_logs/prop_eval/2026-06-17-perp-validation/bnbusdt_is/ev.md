# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T07:36:28.950782+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $97 | $-90 | $-180 | $936.97 | 26% | 7.02 | $316 | 0.306 |
| trend_donchian | 1.0 | $32 | $-45 | $-90 | $630.26 | 17% | 3.79 | $171 | 0.186 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $309 | $187.7 | $-270 | $1442.05 | 53% | 7.02 | $316 | 0.979 |
| trend_donchian | 1.0 | $153 | $-90 | $-180 | $894.06 | 38% | 3.79 | $171 | 0.898 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $714 | $608.24 | $-405 | $2225.34 | 78% | 7.02 | $316 | 2.259 |
| trend_donchian | 1.0 | $382 | $345.76 | $-225 | $1363.32 | 65% | 3.79 | $171 | 2.239 |
