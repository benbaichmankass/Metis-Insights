# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T05:02:52.121795+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $149 | $-45 | $-180 | $912.87 | 36% | 5.58 | $251 | 0.594 |
| trend_donchian | 1.0 | $31 | $-45 | $-90 | $564.57 | 17% | 2.74 | $123 | 0.251 |
| trend_donchian | 0.5 | $-44 | $-45 | $-45 | $-45 | 1% | 1.33 | $60 | -0.734 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -0.998 |
| squeeze_breakout_4h | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.08 | $49 | -0.925 |
| squeeze_breakout_4h | 1.5 | $-47 | $-45 | $-45 | $-45 | 0% | 1.27 | $57 | -0.818 |
| fvg_range_15m | 0.5 | $-47 | $-45 | $-90 | $-45 | 0% | 1.57 | $71 | -0.669 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-49 | $-45 | $-90 | $-45 | 0% | 1.74 | $78 | -0.623 |
| fvg_range_15m | 1.0 | $-50 | $-45 | $-90 | $-45 | 0% | 2.43 | $109 | -0.46 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-54 | $-45 | $-90 | $-45 | 0% | 2.71 | $122 | -0.444 |
| fvg_range_15m | 1.5 | $-64 | $-45 | $-90 | $-45 | 0% | 3.48 | $157 | -0.406 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-70 | $-90 | $-90 | $-45 | 0% | 3.91 | $176 | -0.398 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $472 | $430.83 | $-225 | $1477.42 | 71% | 5.58 | $251 | 1.88 |
| trend_donchian | 1.0 | $217 | $-45 | $-135 | $919.02 | 45% | 2.74 | $123 | 1.763 |
| trend_donchian | 0.5 | $-6 | $-45 | $-90 | $416.55 | 10% | 1.33 | $60 | -0.093 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -0.998 |
| squeeze_breakout_4h | 1.0 | $-46 | $-45 | $-45 | $-45 | 0% | 1.08 | $49 | -0.943 |
| squeeze_breakout_4h | 1.5 | $-50 | $-45 | $-90 | $-45 | 0% | 1.27 | $57 | -0.873 |
| fvg_range_15m | 0.5 | $-52 | $-45 | $-90 | $-45 | 0% | 1.57 | $71 | -0.73 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-54 | $-45 | $-90 | $-45 | 0% | 1.74 | $78 | -0.686 |
| fvg_range_15m | 1.0 | $-70 | $-90 | $-90 | $-45 | 0% | 2.43 | $109 | -0.639 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-77 | $-90 | $-135 | $-45 | 0% | 2.71 | $122 | -0.635 |
| fvg_range_15m | 1.5 | $-96 | $-90 | $-135 | $-45 | 0% | 3.48 | $157 | -0.616 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-101 | $-90 | $-180 | $-45 | 1% | 3.91 | $176 | -0.575 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,101 | $1033.21 | $-225 | $2495.46 | 92% | 5.58 | $251 | 4.384 |
| trend_donchian | 1.0 | $579 | $555.04 | $-180 | $1503.05 | 79% | 2.74 | $123 | 4.697 |
| trend_donchian | 0.5 | $124 | $-45 | $-90 | $680.1 | 33% | 1.33 | $60 | 2.079 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h | 1.0 | $-49 | $-45 | $-90 | $-45 | 0% | 1.08 | $49 | -1 |
| squeeze_breakout_4h | 1.5 | $-55 | $-45 | $-90 | $-45 | 1% | 1.27 | $57 | -0.954 |
| fvg_range_15m | 0.5 | $-71 | $-90 | $-90 | $-45 | 0% | 1.57 | $71 | -1 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-78 | $-90 | $-135 | $-45 | 0% | 1.74 | $78 | -1 |
| fvg_range_15m | 1.0 | $-109 | $-90 | $-180 | $-45 | 0% | 2.43 | $109 | -1 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-121 | $-135 | $-180 | $-45 | 0% | 2.71 | $122 | -0.993 |
| fvg_range_15m | 1.5 | $-152 | $-135 | $-225 | $-90 | 2% | 3.48 | $157 | -0.969 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-156 | $-180 | $-270 | $-10.97 | 5% | 3.91 | $176 | -0.886 |
