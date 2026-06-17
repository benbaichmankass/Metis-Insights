# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T04:51:30.519311+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $290 | $-45 | $-135 | $1529.35 | 39% | 4.01 | $180 | 1.606 |
| trend_donchian | 1.0 | $135 | $-45 | $-90 | $989.47 | 25% | 2.04 | $92 | 1.472 |
| trend_donchian | 0.5 | $-3 | $-45 | $-45 | $442.64 | 8% | 1.14 | $51 | -0.056 |
| squeeze_breakout_4h | 1.5 | $-25 | $-45 | $-45 | $-45 | 5% | 1.39 | $63 | -0.401 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-28 | $-45 | $-45 | $-45 | 3% | 1.6 | $72 | -0.383 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| fvg_range_15m | 1.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.13 | $51 | -0.887 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.01 | $45 | -0.995 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.25 | $56 | -0.798 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $769 | $646.13 | $-180 | $2261.94 | 76% | 4.01 | $180 | 4.265 |
| trend_donchian | 1.0 | $433 | $372.37 | $-90 | $1470.3 | 57% | 2.04 | $92 | 4.713 |
| trend_donchian | 0.5 | $100 | $-45 | $-45 | $694.1 | 24% | 1.14 | $51 | 1.959 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $6 | $-45 | $-90 | $522.96 | 12% | 1.6 | $72 | 0.09 |
| squeeze_breakout_4h | 1.5 | $-20 | $-45 | $-90 | $331.02 | 6% | 1.39 | $63 | -0.327 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-35 | $-45 | $-45 | $-45 | 3% | 1.25 | $56 | -0.628 |
| squeeze_breakout_4h | 1.0 | $-41 | $-45 | $-45 | $-45 | 1% | 1.13 | $51 | -0.818 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| fvg_range_15m | 1.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.01 | $45 | -0.995 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,707 | $1595.78 | $77.08 | $3674.07 | 95% | 4.01 | $180 | 9.47 |
| trend_donchian | 1.0 | $1,018 | $947.98 | $-90 | $2342.16 | 87% | 2.04 | $92 | 11.094 |
| trend_donchian | 0.5 | $377 | $413.88 | $-90 | $1104.74 | 59% | 1.14 | $51 | 7.36 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $144 | $-45 | $-135 | $763.75 | 35% | 1.6 | $72 | 2.001 |
| squeeze_breakout_4h | 1.5 | $23 | $-45 | $-90 | $510.03 | 16% | 1.39 | $63 | 0.375 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $14 | $-45 | $-90 | $479.55 | 14% | 1.25 | $56 | 0.256 |
| squeeze_breakout_4h | 1.0 | $-34 | $-45 | $-90 | $-45 | 3% | 1.13 | $51 | -0.669 |
| fvg_range_15m | 1.5 | $-36 | $-45 | $-45 | $-45 | 1% | 1 | $45 | -0.802 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-44 | $-45 | $-45 | $-45 | 0% | 1.01 | $45 | -0.979 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -0.994 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
