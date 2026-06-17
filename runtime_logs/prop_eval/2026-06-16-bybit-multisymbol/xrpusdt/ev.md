# Prop-firm Monte-Carlo — COST-AWARE EV sweep

_Generated 2026-06-17T05:14:02.558076+00:00_

> **Objective: expected $ netted per horizon, NET of fees, re-buying a fresh account on each breach.** This credits a strategy that burns an account fast but banks more than its fee in payouts first. Ranks by EV ($), not by survival.

**Economics:** account fee $45, re-buy $45, profit split 80%, withdrawal = BANK-ASAP (all equity above start + $0 buffer; first payout day 14, then every 7d, $50 min).

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold). 3000 paths, block_len 8, seed 1234.

> Same realised-only caveat as the survival sheet: a per-trade bootstrap has no intraday equity swing, so breaches (and thus fee churn) are UNDER-counted → EV here is, if anything, optimistic.

## 3-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $58 | $-90 | $-180 | $737.87 | 25% | 5.66 | $255 | 0.226 |
| trend_donchian | 1.0 | $-6 | $-45 | $-90 | $461.93 | 11% | 2.86 | $129 | -0.047 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.25 | $56 | -0.797 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -0.996 |
| squeeze_breakout_4h | 1.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.04 | $47 | -0.957 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.01 | $46 | -0.988 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.17 | $53 | -0.852 |
| trend_donchian | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.5 | $68 | -0.666 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $-46 | $-45 | $-90 | $-45 | 2% | 1.79 | $80 | -0.568 |
| fvg_range_15m | 1.5 | $-49 | $-45 | $-90 | $-45 | 0% | 1.86 | $84 | -0.583 |

## 6-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $230 | $50.88 | $-225 | $1105.46 | 51% | 5.66 | $255 | 0.904 |
| trend_donchian | 1.0 | $83 | $-45 | $-135 | $705.16 | 28% | 2.86 | $129 | 0.645 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $38 | $-45 | $-90 | $543.21 | 20% | 1.79 | $80 | 0.467 |
| squeeze_breakout_4h | 1.5 | $21 | $-45 | $-45 | $502.47 | 13% | 1.04 | $47 | 0.437 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $-36 | $-45 | $-90 | $-45 | 3% | 1.17 | $53 | -0.688 |
| trend_donchian | 0.5 | $-37 | $-45 | $-90 | $-45 | 4% | 1.5 | $68 | -0.549 |
| squeeze_breakout_4h | 1.0 | $-37 | $-45 | $-45 | $-45 | 2% | 1 | $45 | -0.824 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1.01 | $46 | -0.989 |
| fvg_range_15m | 1.0 | $-45 | $-45 | $-45 | $-45 | 0% | 1.25 | $56 | -0.798 |
| fvg_range_15m | 1.5 | $-48 | $-45 | $-90 | $-45 | 3% | 1.86 | $84 | -0.569 |

## 12-month horizon

| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | accts | fees $ | ROI/fees |
|---|---|---|---|---|---|---|---|---|---|
| trend_donchian | 1.5 | $576 | $495.07 | $-315 | $1848.81 | 76% | 5.66 | $255 | 2.263 |
| trend_donchian | 1.0 | $266 | $224.42 | $-225 | $1093.78 | 55% | 2.86 | $129 | 2.071 |
| squeeze_breakout_4h,fvg_range_15m | 1.5 | $255 | $250.6 | $-135 | $862.22 | 55% | 1.79 | $80 | 3.172 |
| squeeze_breakout_4h | 1.5 | $231 | $-45 | $-45 | $845.37 | 44% | 1.04 | $47 | 4.904 |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | $74 | $-45 | $-90 | $555.61 | 25% | 1.17 | $53 | 1.398 |
| squeeze_breakout_4h | 1.0 | $63 | $-45 | $-45 | $553.63 | 21% | 1 | $45 | 1.402 |
| trend_donchian | 0.5 | $3 | $-45 | $-135 | $478.65 | 14% | 1.5 | $68 | 0.045 |
| fvg_range_15m | 1.5 | $-43 | $-90 | $-135 | $308.47 | 10% | 1.86 | $84 | -0.508 |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | $-44 | $-45 | $-45 | $-45 | 0% | 1.01 | $46 | -0.956 |
| squeeze_breakout_4h | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -0.99 |
| fvg_range_15m | 0.5 | $-45 | $-45 | $-45 | $-45 | 0% | 1 | $45 | -1 |
| fvg_range_15m | 1.0 | $-52 | $-45 | $-90 | $-45 | 1% | 1.25 | $56 | -0.915 |
