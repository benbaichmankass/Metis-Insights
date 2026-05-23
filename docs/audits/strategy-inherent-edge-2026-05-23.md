# Cross-Strategy Inherent-Edge Audit — S-STRAT-IMPROVE-S5 (first read, 2026-05-23)

> **Sprint:** S-STRAT-IMPROVE-S5 (Tier-1 analysis). **No live change.**
> **Question (operator North Star):** which strategies have a durable,
> fee-survivable *inherent* edge — the basis for a 3–5 strategy roster?
> **First answer: ict_scalp and turtle_soup both DO; vwap does not.**

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
