# Cross-Strategy Inherent-Edge Audit — S-STRAT-IMPROVE-S5 (first read, 2026-05-23)

> **Sprint:** S-STRAT-IMPROVE-S5 (Tier-1 analysis). **No live change.**
> **Question (operator North Star):** which strategies have a durable,
> fee-survivable *inherent* edge — the basis for a 3–5 strategy roster?
> **Final answer (after regime confirmation): only ict_scalp has a
> DURABLE gross edge (all 3 years). turtle_soup's edge did NOT hold —
> its 12-month positive was a regime artifact. vwap has none.**

## ⚠️ Regime confirmation (2023 / 2024 / 2025) — supersedes the 12mo read

The first read below used only recent 12 months. Per the operator's
no-overfit directive, both strategies were re-run on full-year 2023 and
2024 slices. Net-of-fee R by year:

| Strategy | 2023 | 2024 | 2025 | Gross-edge durable? |
|---|---|---|---|---|
| **ict_scalp** | gross +31.2 / **net +2.1** | gross +29.2 / net −18.9 | gross +45.6 / **net +4.2** | **YES** — gross positive all 3 yrs (+29..+46/yr) |
| **turtle_soup** | gross −6.0 / net −15.0 | gross −13.7 / net −25.4 | gross +11.4 / net +1.3 | **NO** — gross negative in 2023 & 2024 |

**Revised verdict:**
- **ict_scalp = durable keeper.** Positive *gross* edge every year; the
  negative 2024 *net* is a fee/over-trading problem (256 trades, 48R
  fees), not an edge problem. Fee-efficiency tuning (fewer/wider trades)
  is the lever to make a regime-robust gross edge solidly net-positive.
- **turtle_soup = not a durable keeper (as the bare setup).** The +11.4R
  in 2025 was a single-window artifact; gross was negative in 2023+2024.
  *Caveat:* this is the simplified single-TP1 harness — turtle_soup's
  live TP2/partial/ATR-trail/BE management might rescue it, so the action
  is "investigate exits / rework the setup," not "condemn."
- **vwap = no edge** (S4-B).
- **Roster implication:** only 1 of 3 current strategies has confirmed
  durable edge → the **creative new-strategy workstream is central**, not
  optional, to fill the 3–5 slot roster.

---

## ict_scalp exit-variation grid (S6, 2026-05-23)

Variation sweep (tp_at_r ∈ {1.0,1.5,2.0,2.5,3.0} × break-even ∈
{off,0.5R,1.0R}; one entry pass → many cheap exit sims, net-of-fee) per
year-slice. Best variation by net R:

| Year | entries | best variation | gross R | net R | runner-up read |
|---|---|---|---|---|---|
| 2023 | 156 | tp=3.0 / no-BE | +43.8 | **+14.8** | tp=1.5 only +1.5 |
| 2025 (12mo) | 203 | tp=1.5 / no-BE | +46.6 | **+5.5** | tp=3.0 lower |
| 2024 | 254 | tp=2.5 / no-BE | +35.5 | **−12.5** | every variation net-negative |

**Findings:**
1. **Break-even consistently hurts** — `be=None` tops every year; BE
   stops winners out on retracement. Drop BE for ict_scalp. (Robust
   cross-year.)
2. **Exit target is a serious lever but regime-dependent (cliff, not
   plateau)** — best tp = 3.0 (2023) vs 1.5 (2025). No single fixed tp
   is robustly optimal → argues for a **regime-adaptive exit**, not a
   static retune. A fixed tp is a compromise (overfitting recent regime
   if chosen from one year).
3. **2024 net-negative at every exit variation despite +35R gross** —
   254 entries × fees. An **over-trading / selectivity** problem the
   exit cannot fix → the next lever is cutting low-quality entries
   (entry-threshold sweep + **model-as-gate**).

**Implication:** exit dimension characterized (no BE; adaptive tp). The
decisive remaining lever is entry **selectivity**, best tested via the
model-in-the-loop gate (does a setup-quality/win-prob model lift net
OOS across all 3 years, especially 2024?). Evidence: trainer issue #1798.

## ict_scalp entry-selectivity grid (S6, 2026-05-23) — first robust improvement

`displacement_atr_mult` sweep, full pass each, net-of-fee, all 3 years:

| disp | 2023 net | 2025 net | 2024 net |
|---|---|---|---|
| 1.3 (live) | +2.1 | +4.2 | −18.9 |
| **1.6** | **+7.9** | **+9.6** | −16.5 |
| 2.0 | +1.4 | +0.6 | −9.0 |
| 2.5 | −0.3 | −1.4 | −3.6 |
| 3.0 | −1.4 | +0.2 | −2.3 |

**Finding: disp 1.3 → 1.6 is a robust cross-year net improvement (plateau,
not cliff).** Best in BOTH good years (2023 +2.1→+7.9; 2025 +4.2→+9.6 ≈
2–4× net; net/trade ~+0.015→~+0.08), by shedding ~40% of trades (fees)
while keeping the edge. Does NOT rescue 2024 (off-regime → complement's
job). **First concrete, evidence-backed, regime-robust improvement** —
candidate Tier-3 change (1.3→1.6) pending exit-interaction + MES + operator
approval. Caveat: displacement is a *quality* knob only in good regimes;
in the 2024 chop, tightening removes gross-positive trades (no rescue).
Evidence: trainer #1804/#1807/#1809.

## First read (12 months only — see regime confirmation above)


## Method

Run on the trainer VM (uncapped) against the cached **qashdev BTCUSDT 5m
parquet** (`/home/ubuntu/ict-trader-data/btc_5m.parquet`, 332,624 bars,
Jan 2023→Feb 2026). Net-of-fee harnesses (S4): `backtest_ict_scalp.py`
(5m) and `backtest_turtle_soup.py` (5m→15m resample). Fee 7.5 bps
round-trip. **This read is the most recent 12 months** (2025-03-01 →
2026-02-28) — see Caveats; the full 3-year run is too slow on the
trainer's single core with the current per-bar harness (filed as an
optimization follow-up).

## Results (12 months, net-of-fee)

| Strategy | TF | Trades | WR | Gross R | Fee R | **Net R** | Net exp/trade | Fee/trade |
|---|---|---|---|---|---|---|---|---|
| **ict_scalp_5m** | 5m | 204 | 53.4% | **+45.6** | 41.4 | **+4.2** | +0.020 | ~0.20R |
| **turtle_soup** | 15m | 60 | 61.7% | **+11.4** | 10.1 | **+1.3** | +0.022 | ~0.17R |
| vwap (S4-B, ref) | 5m | ~110/win | ~25% | ~0 to neg | huge | **deeply neg** | negative | ~0.45R |

ict_scalp: avg_win +0.99R, avg_loss −0.66R, outcomes {tp 52, sl 49,
timeout 103}, max_dd 9.2R. turtle_soup: avg_win +0.93R, outcomes {tp 33,
sl 23, timeout 4}, max_dd 5.0R; gross long +3.2 / short +8.3, **net long
−1.5 / net short +2.9**.

## Findings

1. **Two strategies have genuine gross edge.** ict_scalp +45.6R and
   turtle_soup +11.4R over 12 months — both with positive per-trade
   expectancy (+0.22 / +0.19 R) and WR > 50%. This is categorically
   different from vwap (gross ~0 over a year).
2. **Both survive fees — barely.** Net +4.2R (ict) and +1.3R (turtle).
   Fees eat ~90% of gross, so net expectancy is thin (~+0.02R/trade).
3. **Fee-efficiency is why they survive and vwap doesn't.** vwap's tight
   0.3σ stop → ~0.45R fee/trade; ict_scalp/turtle_soup use wider,
   structure-based stops → ~0.17–0.20R fee/trade. Same lesson as S4-B,
   now on strategies that *have* gross edge to protect.
4. **Roster implication:** ict_scalp + turtle_soup are **keepers** worth
   improving (selectivity + fee-efficiency to lift the thin net); vwap is
   the **retire/rework** candidate. Two of the 3–5 North-Star slots have
   credible occupants.

## Caveats (intellectual honesty)

- **Single 12-month window (recent).** Not the full 3-year regime
  diversity. Must confirm on 2023 (bear) + 2024 slices before any roster
  decision — a strategy that only works in 2025's regime is not a keeper.
- **Simplified single-TP harness.** ict_scalp here is single TP@1.5R (no
  break-even); turtle_soup is single TP1 (no TP2/partial/ATR-trail). The
  live strategies have richer exits, so these net figures are a
  *conservative floor* — live exits may do better (or worse). The harness
  isolates *setup edge*, which is the right first question.
- **Fee model = 7.5 bps** (Bybit linear). MES/CME economics differ.
- **Net is thin.** +0.02R/trade leaves little margin; the gross edge is
  the durable signal, the net is the fee-tax on it.

## Next (S5 continued → S6)

1. **Regime confirmation** — re-run ict_scalp + turtle_soup on 2023 and
   2024 12-month slices (and optimize the harness so the full 3-year run
   is feasible). Keepers must hold across regimes.
2. **Fee-efficiency tuning** — for the two edged strategies, sweep
   selectivity + stop width net-of-fee to convert strong gross edge into
   a meaningful net edge (the tooling exists; the edge now exists too).
3. **MES** — repeat on MES (CME/IB fees + tick off `instruments.yaml`).
4. **Model-in-the-loop** — registry models as entry gate + decider input
   on the two edged strategies.
5. → roster + decider recommendation (S6, Tier-3 to ship).

All Tier-1. Retiring vwap / promoting tuned ict_scalp+turtle_soup configs
is Tier-3 and stops at the operator-approval gate.
