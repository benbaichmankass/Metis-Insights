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

---

## Addendum (2026-07-15) — two operator-requested follow-ups

Both reinforce the main finding: on these instruments, **shrinking the clock is
dominated by fee-drag + microstructure noise**, so faster is worse.

### A. Small-TF scan of the EXISTING strategy roster (BTC 3yr, net-of-fee)

"Do any of our existing strategies work — or show potential — on smaller
timeframes?" Ran every existing research harness at **5m and 15m** (smaller than
their native 1h–4h; `fvg_range`'s native is 15m) on the trainer BTC 5m parquet
(2023-01 → 2026-02, **332,624** 5m bars), net-of-fee 7.5 bps (trainer run #6483).
Default (bar-based, so they scale with TF) structural params; `fvg_range` at its
live params.

| strategy (native TF) | 5m net_R | 15m net_R |
|---|--:|--:|
| squeeze (2h) | −1,035 | −292 |
| fade (2h) | **−7,226** (fee_r 7,564) | −1,385 (fee_r 1,212) |
| trend (1h) | −2,996 | −359 |
| pullback (2h) | −996 | −168 |
| **fvg_range (15m, live params)** | −22.8 | **+24.1** |

Every trend/breakout/fade/pullback strategy is **catastrophically net-negative at
both 5m and 15m**, monotonically worse as the clock shrinks (5m ≫ 15m losses),
with fees dominating the mean-reversion-style `fade` (gross **+338R** → net
**−7,226R** at 5m). The **only** profitable cell is `fvg_range` at its **native
15m** (+24.1R, exp +0.535, 64% of months positive — matching the main study);
the same strategy dropped to 5m goes negative (−22.8R). `ict_scalp` (native 5m,
gross-only) timed out and is already demoted/negative — not re-run.

**Conclusion (Q2): no existing strategy shows small-TF potential.** The roster's
edges live at 1h–4h (trend/squeeze/fade/pullback) and 15m (`fvg_range`); faster
clocks destroy the edge via noise + fee drag — the same mechanism that sinks the
chop-scalp. There is no hidden fast strategy in the current roster worth further
research. (Caveat: this scans default/live params scaled to a faster clock, not a
per-TF re-tune — a genuinely fast strategy would need its own design, and the
fee-drag evidence sets a high bar for that.)

**Tooling fix surfaced here:** `backtest_{squeeze,fade,trend,pullback}.py`
crashed on pandas-3 `--resample 5m/15m` (`'m'` is no longer a pandas offset
alias). Fixed in the same PR as this addendum (the normalization already present
in `fvg_range`/`chop_scalp`), so future small-TF scans of these strategies just work.

### B. 1m chop-scalp rule-out (BTC, net-of-fee)

"Look into 1m to confidently rule it out." Fetched BTC **1m, trailing 365 days**
(2025-07 → 2026-07, 526,317 bars — a downtrend year) and ran the representative
`chop_scalp` configs **including the tp1r steelman** — the tighter, higher-win
target that directly addresses the far-boundary R:R critique — net-of-fee 7.5 bps
(trainer run #6484):

| config | trades | net_R | net_R/pos-day | fee_R | months+ |
|---|--:|--:|--:|--:|--:|
| 1m / HTF 15m / far | 195 | −185.8 | −70.2 | 180.5 | 0% |
| 1m / HTF 15m / tp1r=1.0 (steelman) | 222 | −207.1 | −181.2 | 201.8 | 7.7% |
| 1m / HTF 1h / far | 61 | −51.6 | −51.4 | 55.4 | 11% |
| 1m / HTF 1h / tp1r=1.0 (steelman) | 82 | −59.3 | −105.4 | 65.6 | 11% |

**1m is decisively ruled out.** Every config is strongly net-negative, and in
every case **`fee_R` ≈ the entire loss** — the gross edge is ~breakeven and 1m
round-trip fees sink it. The **tp1r steelman is _worse_, not better**: fixing the
R:R by taking a tighter target just makes it trade more often → more fee drag
(net_R/pos-day −181 vs −70 for far). net_R/pos-day of −51 to −181 is catastrophic
capital efficiency, 0–11% of months positive.

### Overall — the book is closed

A faster / multi-TF chop-scalp is net-negative at **5m and 1m**, and **no existing
strategy has a small-TF edge** — the roster's edges are inherently 15m–4h. The
finer the clock, the more fee-dominated and the worse the result, in every test
run. **`fvg_range_15m` (already live) remains the right and only capital-efficient
range tool. No Tier-3 change; no new strategy.**

