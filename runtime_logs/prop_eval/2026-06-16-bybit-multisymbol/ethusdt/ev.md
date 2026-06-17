# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-16T21:20:24.959983+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $187 | $-45 | $-135 | $979.33 | 40% | 5.12 | $230 | 0.812 |
| trend_donchian | 1.0 | $53 | $-45 | $-90 | $635.9 | 20% | 2.68 | $121 | 0.438 |
| squeeze_breakout_4h | 1.5 | $-3 | $-45 | $-90 | $500.94 | 8% | 1.44 | $65 | -0.039 |
| squeeze_breakout_4h | 1.0 | $-32 | $-45 | $-45 | $-45 | 3% | 1.16 | $52 | -0.607 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-33 | $-45 | $-90 | $356.42 | 6% | 3.04 | $137 | -0.238 |
| trend_donchian | 0.5 | $-43 | $-45 | $-45 | $-45 | 1% | 1.32 | $59 | -0.723 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.01 | $46 | -0.987 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-47 | $-45 | $-90 | $-45 | 2% | 2.44 | $110 | -0.431 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-52 | $-45 | $-90 | $-45 | 0% | 1.74 | $78 | -0.667 |
| fvg_range_15m | 0.5 | $-53 | $-45 | $-90 | $-45 | 0% | 1.8 | $81 | -0.655 |
| fvg_range_15m | 1.0 | $-56 | $-45 | $-90 | $-45 | 0% | 2.38 | $107 | -0.525 |
| fvg_range_15m | 1.5 | $-66 | $-45 | $-135 | $-45 | 0% | 2.72 | $122 | -0.536 |
| turtle_soup | 0.5 | $-89 | $-90 | $-180 | $-45 | 0% | 5.6 | $252 | -0.354 |
| turtle_soup | 1.0 | $-134 | $-135 | $-225 | $-45 | 0% | 9.27 | $417 | -0.322 |
| turtle_soup | 1.5 | $-194 | $-180 | $-315 | $-90 | 0% | 14.17 | $638 | -0.304 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $525 | $494.56 | $-225 | $1525.95 | 74% | 5.12 | $230 | 2.278 |
| trend_donchian | 1.0 | $253 | $-45 | $-135 | $969.36 | 50% | 2.68 | $121 | 2.094 |
| squeeze_breakout_4h | 1.5 | $63 | $-45 | $-90 | $637.28 | 20% | 1.44 | $65 | 0.969 |
| trend_donchian | 0.5 | $4 | $-45 | $-90 | $440.87 | 11% | 1.32 | $59 | 0.068 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-9 | $-90 | $-135 | $535.42 | 14% | 3.04 | $137 | -0.066 |
| squeeze_breakout_4h | 1.0 | $-11 | $-45 | $-90 | $376.61 | 7% | 1.16 | $52 | -0.213 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.01 | $46 | -0.987 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-49 | $-90 | $-135 | $321.25 | 6% | 2.44 | $110 | -0.443 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-60 | $-45 | $-90 | $-45 | 0% | 1.74 | $78 | -0.761 |
| fvg_range_15m | 0.5 | $-64 | $-45 | $-90 | $-45 | 0% | 1.8 | $81 | -0.79 |
| fvg_range_15m | 1.0 | $-81 | $-90 | $-135 | $-45 | 0% | 2.38 | $107 | -0.751 |
| fvg_range_15m | 1.5 | $-87 | $-90 | $-180 | $-45 | 0% | 2.72 | $122 | -0.714 |
| turtle_soup | 0.5 | $-143 | $-135 | $-270 | $-90 | 0% | 5.6 | $252 | -0.569 |
| turtle_soup | 1.0 | $-229 | $-225 | $-360 | $-135 | 0% | 9.27 | $417 | -0.548 |
| turtle_soup | 1.5 | $-339 | $-315 | $-540 | $-180 | 0% | 14.17 | $638 | -0.532 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $1,183 | $1129.98 | $-180 | $2544.16 | 93% | 5.12 | $230 | 5.132 |
| trend_donchian | 1.0 | $640 | $618.67 | $-180 | $1603.21 | 81% | 2.68 | $121 | 5.305 |
| squeeze_breakout_4h | 1.5 | $251 | $-45 | $-135 | $996.16 | 45% | 1.44 | $65 | 3.866 |
| trend_donchian | 0.5 | $151 | $-45 | $-90 | $705.88 | 37% | 1.32 | $59 | 2.539 |
| squeeze_breakout_4h | 1.0 | $92 | $-45 | $-90 | $640.1 | 25% | 1.16 | $52 | 1.758 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $63 | $-90 | $-225 | $785.03 | 31% | 3.04 | $137 | 0.461 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-29 | $-90 | $-180 | $475.65 | 15% | 2.44 | $110 | -0.262 |
| squeeze_breakout_4h | 0.5 | $-35 | $-45 | $-45 | $-45 | 2% | 1.01 | $46 | -0.778 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-74 | $-90 | $-135 | $-45 | 1% | 1.74 | $78 | -0.944 |
| fvg_range_15m | 0.5 | $-81 | $-90 | $-135 | $-45 | 0% | 1.8 | $81 | -1 |
| fvg_range_15m | 1.0 | $-107 | $-90 | $-180 | $-45 | 0% | 2.38 | $107 | -1 |
| fvg_range_15m | 1.5 | $-122 | $-90 | $-225 | $-45 | 0% | 2.72 | $122 | -1 |
| turtle_soup | 0.5 | $-252 | $-225 | $-405 | $-135 | 0% | 5.6 | $252 | -1 |
| turtle_soup | 1.0 | $-417 | $-405 | $-585 | $-225 | 0% | 9.27 | $417 | -0.999 |
| turtle_soup | 1.5 | $-632 | $-630 | $-900 | $-360 | 0% | 14.17 | $638 | -0.991 |
