# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T05:25:07.253306+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $40 | $-90 | $-180 | $652.62 | 26% | 5.62 | $253 | 0.159 |
| fvg_range_15m | 1.5 | $-22 | $-45 | $-90 | $303.77 | 8% | 1.77 | $80 | -0.27 |
| trend_donchian | 1.0 | $-26 | $-45 | $-135 | $353.05 | 8% | 2.83 | $127 | -0.205 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-39 | $-45 | $-90 | $237.11 | 6% | 2.46 | $111 | -0.349 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -0.996 |
| fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.21 | $55 | -0.826 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.02 | $46 | -0.978 |
| squeeze_breakout_4h | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.37 | $62 | -0.731 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.14 | $51 | -0.878 |
| trend_donchian | 0.5 | $-47 | $-45 | $-90 | $-45 | 0% | 1.43 | $65 | -0.734 |
| squeeze_breakout_4h | 1.5 | $-49 | $-45 | $-90 | $-45 | 0% | 1.76 | $79 | -0.621 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-49 | $-45 | $-90 | $-45 | 0% | 1.66 | $75 | -0.661 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $219 | $128.77 | $-270 | $992.74 | 55% | 5.62 | $253 | 0.868 |
| trend_donchian | 1.0 | $76 | $-45 | $-135 | $641.14 | 30% | 2.83 | $127 | 0.596 |
| fvg_range_15m | 1.5 | $-25 | $-45 | $-90 | $258.77 | 8% | 1.77 | $80 | -0.311 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-26 | $-90 | $-135 | $366.06 | 12% | 2.46 | $111 | -0.239 |
| trend_donchian | 0.5 | $-44 | $-45 | $-90 | $-45 | 2% | 1.43 | $65 | -0.677 |
| fvg_range_15m | 1.0 | $-44 | $-45 | $-45 | $-45 | 0% | 1.21 | $55 | -0.802 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -0.996 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.02 | $46 | -0.978 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-46 | $-45 | $-45 | $-45 | 0% | 1.14 | $51 | -0.897 |
| squeeze_breakout_4h | 1.0 | $-49 | $-45 | $-90 | $-45 | 0% | 1.37 | $62 | -0.789 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-54 | $-45 | $-90 | $-45 | 1% | 1.66 | $75 | -0.729 |
| squeeze_breakout_4h | 1.5 | $-61 | $-45 | $-90 | $-45 | 0% | 1.76 | $79 | -0.772 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $587 | $525.26 | $-315 | $1731.54 | 80% | 5.62 | $253 | 2.322 |
| trend_donchian | 1.0 | $283 | $266.37 | $-225 | $1062.21 | 61% | 2.83 | $127 | 2.223 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $46 | $-90 | $-180 | $617.24 | 30% | 2.46 | $111 | 0.417 |
| fvg_range_15m | 1.5 | $24 | $-45 | $-135 | $436.93 | 23% | 1.77 | $80 | 0.307 |
| trend_donchian | 0.5 | $1 | $-45 | $-135 | $460.45 | 13% | 1.43 | $65 | 0.022 |
| squeeze_breakout_4h | 1.5 | $-37 | $-90 | $-135 | $373.68 | 8% | 1.76 | $79 | -0.471 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-41 | $-90 | $-135 | $359.08 | 7% | 1.66 | $75 | -0.544 |
| fvg_range_15m | 1.0 | $-43 | $-45 | $-90 | $-45 | 3% | 1.21 | $55 | -0.793 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h | 0.5 | $-46 | $-45 | $-45 | $-45 | 0% | 1.02 | $46 | -1 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-51 | $-45 | $-90 | $-45 | 0% | 1.14 | $51 | -0.995 |
| squeeze_breakout_4h | 1.0 | $-54 | $-45 | $-90 | $-45 | 2% | 1.37 | $62 | -0.882 |
