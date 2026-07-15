# Scalping through chop — multi-TF range-bounce capital-efficiency study

**Date:** 2026-07-15 · **Author:** Claude research session
(`claude/scalping-chop-strategies-b9n05u`) · **Tier:** 1 (research; no live wiring)
· **PR:** #6479 · **Data:** trainer VM 3yr 5m (BTC `btc_5m.parquet`, ETH
`ethusdt_5m.csv`), 2023-01-01 → 2026-02-28, net-of-fee 7.5 bps round-trip
· **Trainer run:** issue #6480 (`actions/runs/29411456818`).

## Question

Can a strategy **geared to scalp through chop** — reading range/chop **boundaries
on a higher timeframe** and catching **bounces on a faster one** — be net-positive,
and is it **capital-efficient**? "Efficient" here is deliberately not "big wins":
it is **PnL per unit of trade-time** (`net_R / position-day`, the same metric the
exit-refinement gate uses, `scripts/ml/train_exit_head.py::agg`) and, because a
chop strategy idles between ranges, **return per CALENDAR day** — which pays for
the idle time and lets us ask honestly whether it beats **holding a longer
position** (buy-and-hold) or **sitting on cash**.

## What was built (the reusable tooling)

- **`scripts/backtest_chop_scalp.py`** — a multi-timeframe range-bounce scalp
  harness. HTF (`--htf-rule`, e.g. 15m/1h) defines the range boundaries + ADX
  chop gate + touch confirmation; the LTF (5m here) catches a boundary
  wick-rejection (optionally FVG-confirmed), targeting the opposite boundary /
  midline / fixed-R. HTF features attach to LTF bars by a **backward
  `merge_asof`** (lookahead-safe — an LTF entry only ever sees an HTF bar that
  already closed). `_summarize` reports the capital-efficiency block
  (`net_r_per_pos_day`, mean hold, roundtrippers%).
- **`scripts/research/chop_scalp_study.py`** — the study orchestrator used here:
  runs a chop_scalp config grid + the incumbent `fvg_range` (live params) +
  buy-hold + cash + a chop-tape characterization on one feed, scores every
  strategy from a uniform per-trade emit, and does the walk-forward IS/OOS split.
- **`scripts/backtest_fvg_range.py`** — additive `hold_bars` + `mfe_r` on
  `--emit-trades` (no behaviour change) so the incumbent is scored on the same
  axis.
- **`tests/test_backtest_chop_scalp.py`** — 14 tests (cap-efficiency math,
  merge lookahead-safety, chop gate, determinism).

Baselines are risk-normalised at 1% equity/trade, so `net_R` maps to a
`total_return_%`, `ret/cal-day%` = return over the whole window ÷ calendar days,
`exposure%` = fraction of calendar time capital was deployed.

## The tape

Across both symbols and windows, **~30–38% of the 5m tape is chop** (HTF ADX < 20),
median range width ~2–3% (15m) / ~4–7% (1h). So the opportunity is real and
sizeable — chop is a third of the tape. The question is whether an edge is
extractable there net-of-fee.

## Results (headline)

### BTCUSDT — the only symbol where any range play works, and only the incumbent

| window | strategy | trades | win% | net_R | **net_R/pos-day** | ret/cal-day% |
|---|--:|--:|--:|--:|--:|
| FULL | fvg_range/15m/far (incumbent) | 45 | 55.6 | **+24.1** | **+2.10** | +0.021 |
| FULL | chop_scalp/15m/far/wick | 194 | 21.1 | **−74.3** | **−6.53** | −0.064 |
| FULL | chop_scalp/1h/far/wick | 82 | 26.8 | −22.7 | −4.06 | −0.020 |
| FULL | chop_scalp/1h/far/fvg | 9 | 44.4 | +3.6 | +2.98 | +0.003 |
| **OOS** | fvg_range/15m/far (incumbent) | 26 | 57.7 | **+12.7** | **+1.70** | **+0.030** |
| **OOS** | chop_scalp/1h/far/fvg | 8 | 37.5 | −3.5 | −3.32 | −0.008 |
| **OOS** | chop_scalp/15m/far/wick | 92 | 17.4 | −54.9 | −10.78 | −0.129 |
| FULL | buy_hold | — | — | — | — | **+0.264** |
| **OOS** | buy_hold | — | — | — | — | **−0.067** |
| any | cash | 0 | — | 0 | 0 | 0 |

### ETHUSDT — nothing works, incumbent included

| window | strategy | trades | win% | net_R | **net_R/pos-day** | ret/cal-day% |
|---|--:|--:|--:|--:|--:|
| FULL | fvg_range/15m/far (incumbent) | 22 | 27.3 | **−7.3** | **−1.60** | −0.006 |
| FULL | chop_scalp/15m/far/wick | 188 | 15.4 | −101.6 | −10.13 | −0.088 |
| OOS | chop_scalp/15m/far/wick | 98 | 12.2 | −65.5 | −13.46 | −0.154 |
| OOS | buy_hold | — | — | — | — | −0.097 |

(FVG-confirmed chop_scalp produced **0 trades on ETH** — the range+FVG+touch stack
is too strict for ETH's structure. Full tables in the trainer-run comment / the
committed study JSON.)

## Findings

1. **The faster multi-TF chop-scalp at tradeable frequency is decisively
   net-negative** — every "wick" variant (80–200 trades) loses on **both** BTC and
   ETH, **in-sample and out-of-sample**, net-of-fee, with `net_R/pos-day` of **−4
   to −15** and win rates of **12–27%**. It does not beat cash, let alone buy-hold.
   The structural reason: entering a 5m bounce but targeting a **full HTF-range
   reversion** with a stop just past the boundary is poor R:R — the far target is
   rarely reached, the tight stop is hit often, and 5m round-trip fees (the
   `fee_r` drag was already visible in the sandbox smoke test) tax the churn. The
   `mid`-target variant lifts win% slightly but stays net-negative.

2. **The few positive chop_scalp cells are statistical noise.** The only positive
   cells (FVG-confirmed) fire **1–9 times over 3 years** and **collapse out of
   sample** (BTC 1h/far/fvg: +7.05R on **1** IS trade → −3.46R on 8 OOS trades).
   Not a tradeable, durable edge.

3. **The incumbent `fvg_range` (15m, already live on BTC) is the only
   capital-efficient range play** — +24.1R full / **+1.70 net_R/pos-day OOS** /
   56–58% win — and it is a **genuine but modest diversifier**: in the BTC **OOS
   window where buy-hold went negative** (−0.067%/cal-day), fvg_range **beat both
   buy-hold and cash** (+0.030%/cal-day). That is exactly where a range strategy
   should earn — non-trending regimes — and it confirms the decision to keep
   fvg_range live and NOT chase a faster cousin. On ETH even fvg_range is
   negative, matching its own docs (low-frequency, BTC-specific, recent-regime
   edge).

4. **Capital-efficiency verdict.** During BTC's bull window buy-hold's
   +0.26%/cal-day dwarfs any range strategy — a chop strategy is a **diversifier,
   not a primary engine**. Its value is regime-specific (flat/down tape), small,
   and — critically — **already captured by the existing 15m fvg_range**. Going
   faster/multi-TF did not add capital efficiency; it subtracted it via fee-drag
   churn.

## Recommendation

**Do not wire a faster multi-TF chop-scalper.** This is a clean, data-backed
**negative result**: the "just scalp the chop faster on a multi-TF read" idea is
net-negative at frequency across both symbols and both walk-forward halves, and
the current 15m `fvg_range` remains the right — and only — capital-efficient range
tool. **No Tier-3 strategy proposal accompanies this report.**

If range-scalping is revisited later, the evidence points the burden of proof at:
(a) a **tighter, higher-win target** (partial-TP at fractional-range / `tp1r`
rather than the full far boundary) so R:R stops depending on a full reversion;
(b) **1m LTF** data (finer than the 5m used here) to test whether a genuinely
faster bounce entry changes the fee math; and (c) an **ETH-specific** structure —
the BTC range logic transfers to neither ETH nor a faster clock. Given the
consistency of the negative here, that is a high bar, not a quick tune.

## Reproduce

```bash
# on the trainer VM (has the multi-year 5m data + a pandas venv):
python scripts/research/chop_scalp_study.py \
  --data /home/ubuntu/ict-trader-data/btc_5m.parquet --symbol BTCUSDT \
  --ltf 5m --htf-rules 15m,1h --oos-split 2025-01-01 --md /tmp/chop_btc.md
python scripts/research/chop_scalp_study.py \
  --data /home/ubuntu/ict-trader-data/ethusdt_5m.csv --symbol ETHUSDT \
  --ltf 5m --htf-rules 15m,1h --oos-split 2025-01-01 --md /tmp/chop_eth.md
```
