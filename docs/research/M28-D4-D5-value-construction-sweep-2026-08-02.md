# M28 D4/D5 — value-construction sweep through the P4 gate (2026-08-02)

**Verdict: a clean NULL. No construction clears `edge_vs_baseline > 0` net-of-cost OOS.**
The C4 conditioned-lifecycle gate (`thesis_c4_run.py`) is therefore **not** re-run —
its precondition (a construction that clears the P4 gate) is unmet.

## What ran

The M28-P4 value gate ran once (2026-07-27) on the **level** construction (S1-former:
trailing-percentile `cheap_score`) and returned a clean NULL — `edge_vs_baseline =
−0.0047`, calibration ≈ 0 ([`M28-P4-value-gate-run-2026-07-27.md`](M28-P4-value-gate-run-2026-07-27.md)).
This sweep (`scripts/macro/value_construction_sweep.py`) iterates the unexplored
construction dimensions on the **same** committed value inputs
(`comms/macro/valuation_snapshots_backfill.jsonl`, 10,125 rows) and grades every variant
through the **UNCHANGED** P4 lifecycle gate — net-of-cost + calibration + beat-baseline.
D4 (composite) and D5 (horizon sweep on the `change` cell) were the outstanding pieces.

Run (trainer-VM relay #8360): fetch ~21yr daily ETF closes (SPY/TLT/GLD/SLV/IEF,
5,096–5,428 closes each via the off-VM `fetch_macro_candles.py`) → grade at
`--rebalance-every 30 --horizon-days 30 --fee-frac 0.001`; D5 horizon-sweep
`7,14,30,60,90,180`.

## Result — every construction is sub-all-long net-of-cost

Gate: a construction CLEARS iff `edge_vs_baseline > 0` net-of-cost **AND**
`calibration_rank > 0`, OOS. Baseline to beat (S1-former `level`): `−0.0047`.

| construction | n | win | mean_net | calib | **edge_vs_base** |
|---|---:|---:|---:|---:|---:|
| level_x_turning | 574 | 0.5052 | +0.0011 | +0.0049 | **−0.0017** |
| change | 837 | 0.5161 | +0.0028 | +0.0212 | **−0.0031** |
| detrend | 918 | 0.5087 | +0.0029 | +0.0091 | **−0.0033** |
| composite_eq (D4) | 674 | 0.5223 | +0.0015 | +0.0547 | **−0.0034** |
| change_x_calm_vol (D2reg) | 413 | 0.5036 | +0.0010 | +0.0126 | **−0.0043** |
| baseline (committed) | 1104 | 0.4973 | +0.0018 | −0.0040 | **−0.0047** |
| composite_ic (D4) | 686 | 0.5219 | +0.0006 | +0.0090 | **−0.0050** |
| change_x_uptrend (D2reg) | 481 | 0.4969 | −0.0000 | +0.0526 | **−0.0057** |
| xsec | 776 | 0.4936 | −0.0002 | −0.0343 | **−0.0060** |
| accel | 804 | 0.5062 | −0.0001 | +0.0108 | **−0.0066** |
| level | 1028 | 0.4805 | −0.0003 | −0.0184 | **−0.0068** |
| level_x_price_turning | 610 | 0.4623 | −0.0007 | −0.0279 | **−0.0097** |

**D5 — horizon × cost sweep on the `change` cell** (fee_frac 0.001):

| horizon | n | win | mean_net | calib | **edge_vs_base** |
|---|---:|---:|---:|---:|---:|
| change@7d | 837 | 0.4827 | −0.0001 | −0.0328 | **−0.0025** |
| change@14d | 837 | 0.5006 | +0.0010 | +0.0004 | **−0.0025** |
| change@30d | 837 | 0.5161 | +0.0028 | +0.0212 | **−0.0031** |
| change@60d | 837 | 0.5149 | +0.0032 | −0.0115 | **−0.0101** |
| change@90d | 837 | 0.5257 | +0.0041 | +0.0109 | **−0.0146** |
| change@180d | 837 | 0.5173 | +0.0044 | +0.0638 | **−0.0340** |

## Reading it

- **No construction clears.** Every D4 composite (equal- and IC-weighted), every D2
  regime-conditioning of the `change` cell (calm-vol, up-trend), and every D5 horizon
  (7d–180d) has `edge_vs_baseline < 0` — i.e. loses to naive all-long net-of-cost. The
  space of cheap, defensible constructions on these value inputs is now **exhausted at
  this gate**, all NULL.
- **The `change` cell is the least-bad and does calibrate.** `change` (−0.0031) and the
  D4 composites (−0.0034 equal) beat the S1-former baseline's edge (−0.0047), and their
  **calibration is genuinely positive** — `composite_eq` +0.0547, `change@180d` +0.0638
  (conviction weakly predicts return). That reproduces the "positive-calibrating but
  sub-all-long" character the item flagged. It is a *lead*, not a deployable standalone
  signal — the same disposition the vix_term M5 re-examination reached
  ([`M5-vix-term-corrected-cost-reexamination-2026-08-02.md`](M5-vix-term-corrected-cost-reexamination-2026-08-02.md), honest-negative "validated lead, not deployable standalone").
- **Why the edge stays negative — and why longer horizons make it worse, not better.**
  The gate is `edge_vs_baseline`, and the baseline is naive all-long over a 21-yr ETF
  bull market — a very high bar. Holding the `change` conviction longer (D5) *raises*
  mean_net and (at 180d) calibration, but the all-long baseline captures even more of
  that same drift for free, so `edge_vs_baseline` **worsens monotonically** past 30d
  (−0.0025 → −0.0340). The value read's conviction orders returns weakly, but it cannot
  out-earn simply being long these assets.

## Disposition

- **Park the M28 value sleeve as a deployable standalone.** Both the unconditioned
  level (P4, 2026-07-27) and now the full D1–D4 + D2-conditioned + D5-horizon sweep are
  clean NULLs on `edge_vs_baseline`. This is the exit criterion the methodology names —
  *a construction clears the gate OOS **or** the space is exhausted*; the value-input
  construction space is exhausted at this gate.
- **The positive calibration is banked as a lead, not shipped.** `change`/composite
  calibrate positively; if the value read is ever used it belongs as a *conditioning
  input to another signal* (the c_reg/conviction-contribution role), never as its own
  cheap-long/rich-short book — exactly because it can't beat all-long alone.
- **C4 gate NOT re-run** (precondition unmet). Reproducible in minutes via relay #8360's
  one command; scorecard committed at `comms/macro/value_construction_scorecard.json`.

Recorded in the M28 research ledger ([`M28-signal-research-ledger.md`](M28-signal-research-ledger.md)).
