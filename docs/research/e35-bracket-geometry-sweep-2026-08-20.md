# E3.5 — the bracket-geometry sweep: `tp_r` × `atr_stop_mult` × `timeout_bars`

**Date:** 2026-08-20 · **Tier-1, observe-only.** Changing any bracket parameter
in `config/strategies.yaml` is **Tier-3**; this produces the evidence, not the flip.

**Tool:** `scripts/research/e35_bracket_geometry_sweep.py` (44/44 self-test).
Every comparison reuses `m20_fleet_exit_sweep`'s own definitions by import —
`base_args`, `resolve_split`, `run_cell`, `beats` (Path A), `is_path_b_candidate`,
`walkforward`. A new coverage-matrix **dimension**, not a new gate.
**Artifacts:** `runtime_logs/e35_bracket/<leg>/2026-08-20/`.

**POPULATION: 19 legs × 199 grid cells = 3,781 measured cells, plus 133 gate
rows.** Config-exact base, `--tp-cap-pct 0.099`, `--split-mode oos-trades
--split-target-oos 50`, yearly folds 2021–2026, **net of fees**. Candles from
`data.binance.vision`, full span 2021-08-16 → 2026-08-19 (SOL/XRP/ADA/AVAX each
miss 5 days = 0.27%; BTC/ETH none).

⚠️ **This is a SEPARATE finding from
`e35-bracket-is-not-a-decision-2026-08-20.md`** and must not be back-read into
it. That document measured what the bracket *is* today at fixed
`atr_stop_mult`; this one varies it.

---

## 1. The response surface is wide — and that is the warning, not the result

| leg | base net_R | grid min | grid max | **spread** | argmax cell | Δ |
|---|---|---|---|---|---|---|
| `trend_donchian_1h` | −2.81 | −284.72 | 44.62 | **329.34** | `sm2_to96` | +47.43 |
| `htf_pullback_trend_2h` | −9.61 | −153.80 | 12.90 | **166.70** | `tp4_sm3_to96` | +22.51 |
| `trend_donchian_eth` | 26.36 | −50.04 | 79.13 | 129.16 | `sm1.5_to400` | +52.77 |
| `trend_donchian_eth_prop` | 27.07 | −74.44 | 41.50 | 115.94 | `sm1.5` | +14.44 |
| `trend_donchian_sol` | 50.94 | −28.03 | 72.24 | 100.28 | `sm1.5_to96` | +21.30 |
| `trend_donchian` | 20.28 | −43.37 | 47.35 | 90.72 | `sm1.5_to400` | +27.07 |
| `trend_donchian_sol_prop` | 38.10 | −28.03 | 45.35 | 73.38 | `sm1.5_to96` | +7.25 |
| `eth_pullback_2h` | −8.69 | −62.75 | 9.91 | 72.65 | `tp2_sm3.5_to48` | +18.59 |
| `xrp_pullback_2h` | −15.56 | −85.76 | −15.56 | 70.20 | `tp6` | **+0.00** |
| `trend_donchian_eth_4h` | 12.77 | −2.85 | 63.96 | 66.81 | `tp6_sm1.5` | +51.19 |
| `trend_donchian_sol_4h` | 37.92 | 11.53 | 75.94 | 64.41 | `tp4_sm1.5_to24` | +38.02 |
| `trend_donchian_ada_4h` | 33.09 | −13.48 | 43.85 | 57.32 | `sm2` | +10.76 |
| `eth_pullback_prop_2h` | −32.58 | −66.43 | −9.19 | 57.24 | `tp4_sm3_to48` | +23.39 |
| `squeeze_breakout_4h` | 12.36 | −13.21 | 40.78 | 53.99 | `sm1.5_to24` | +28.42 |
| `avax_pullback_2h` | 17.37 | −34.62 | 19.21 | 53.83 | `tp6_to96` | +1.84 |
| `sol_pullback_2h` | 18.11 | −16.45 | 26.09 | 42.54 | `sm2_to96` | +7.98 |
| `ada_pullback_2h` | 14.98 | −17.47 | 24.34 | 41.81 | `tp2.5_sm3.5_to400` | +9.36 |
| `trend_donchian_avax_4h` | 22.23 | 1.88 | 38.48 | 36.60 | `tp3_sm1.5_to24` | +16.25 |
| `trend_donchian_xrp_4h` | 9.24 | −19.42 | 14.46 | 33.88 | `tp3_sm2` | +5.22 |

**Spread across 19 legs: min 33.9 R · median 66.8 R · max 329.3 R.** So the
bracket dimension is emphatically *not* flat — the answer the operator asked to
be ready for ("if net R barely moves, the exit dimension has little to give")
does **not** hold.

**But a wide surface argmaxed over 199 cells is a multiple-comparisons machine,
not a finding.** The argmax is the maximum of 199 draws, and the gate below is
what separates it from noise. `xrp_pullback_2h` is the clean illustration: a
70.20 R spread and an argmax that improves the base by **exactly 0.00 R** —
every one of its 199 cells is at or below base.

---

## 2. The gate: 112 of 133 fail before a fold is run

| verdict | n |
|---|---|
| `is_oos_fail` | **112** |
| `wf_pass` | 17 |
| `wf_fail` | 4 |

⚠️ **THE 17 IS INFLATED — 8 of them are partly inert.** `walkforward` counts a
fold in which the lever changed nothing as a win (`0 >= 0`, `0 <= 0`), which
`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS` records. Reading
`wins_effective` beside `wins`:

| leg | cell | axis | wf | **effective** | inert |
|---|---|---|---|---|---|
| `avax_pullback_2h` | `to96` | timeout | 6/6 | **2/6** | 4 |
| `avax_pullback_2h` | `tp6_to96` | tp+timeout | 6/6 | **2/6** | 4 |
| `trend_donchian_sol_4h` | `to96` | timeout | 6/6 | **3/6** | 3 |
| `trend_donchian_xrp_4h` | `tp2.5` | tp | 6/6 | **3/6** | 3 |
| `sol_pullback_2h` | `to96` | timeout | 6/6 | 4/6 | 2 |
| `trend_donchian_avax_4h` | `tp2` | tp | 4/6 | **2/6** | 2 |
| `eth_pullback_2h` | `tp3` | tp | 5/6 | 4/6 | 1 |
| `trend_donchian_sol_4h` | `to48` | timeout | 5/6 | 4/6 | 1 |
| *(9 others)* | | | | *unchanged* | 0 |

**20 inert fold-wins in total.** Four cells that record `6/6` are `2/6`–`3/6`
effective — the lever did nothing in most folds it was credited for. Every
inflated cell is on the `timeout` or `tp` axis, which is what you would expect:
a timeout at 96 bars on a leg whose trades rarely last that long cannot fire.

**Concentration worth stating:** 8 of the 17 passes are on `eth_pullback_2h` (5)
and `eth_pullback_prop_2h` (3) — and those are the *same underlying leg*
(ETHUSDT 2h pullback; the prop variant differs only in `tp_r` 6.0 and
`trail_mult` 3.5). Treat that as **n = 1 leg, not 2**.

---

## 3. Dispersion — and the E3 falsifier answered for the first time

Per § E4, `split_sensitive: true` is a **refusal**. Run on the strongest
survivors, five OOS targets each.

| leg | cell | dOOS range | `split_sensitive` | pass_fraction |
|---|---|---|---|---|
| `eth_pullback_2h` | **`tp2_sm3.5_to48`** (joint) | +2.294 … +9.779 | **false** | **1.0** |
| `eth_pullback_prop_2h` | **`tp4_sm3_to48`** (joint) | +1.154 … +6.776 | **false** | **1.0** |
| `eth_pullback_2h` | `tp2.5` (best single) | +0.454 … **−0.365** | **true** | 0.4 |
| `squeeze_breakout_4h` | `to24` | — | — | **`refused`** (§ 4) |

`harness_agreement` ok on the first three (max delta 0.0008 R against a 0.001
tolerance).

**This is the first time E3's falsifier has been met.** § E3 requires that *"a
combined cell must beat the best single cell by more than the added degrees of
freedom buy"*, and the 2026-08-20 lever screen failed it (+0.000 R on XRP). Here,
on `eth_pullback_2h`: the joint cell gains **+3.962 R OOS at target 50** against
the best single cell's **+0.397 R**, and — the part that is not just a bigger
number — the joint cell is **`split_sensitive: false` across all five targets
while the single cell flips sign** (+0.454 → −0.365) and fails 3 of 5.

⚠️ **Three reasons this is not yet a Tier-3 proposal**, all of which I would want
answered before quoting it as one:

1. **It is n = 1 leg.** Both surviving cells are ETHUSDT 2h pullback.
2. **The joint cell is the argmax of 199.** Dispersion tests the IS/OOS boundary;
   it does **not** correct for having selected the maximum of a 199-cell search.
   The added degrees of freedom are 3 vs 1, and *how much* that buys has not been
   quantified — a shuffled-label or random-cell control would be the honest way
   to price it, and neither was run.
3. **`eth_pullback_2h`'s base is net-negative** (−8.69 R over full history). Same
   shape as the `rr_floor` result: the cell makes a losing book less bad.

---

## 4. `squeeze_breakout_4h` was REFUSED, and the refusal found a real defect

`m20_split_dispersion` returned `state: refused`, `harness_agreement` max delta
**0.7986 R** against its 0.001 tolerance — exactly the behaviour its docstring
promises rather than emitting a band on a metric that does not reproduce.

Root-caused to one line: **`backtest_squeeze.py:378` accumulates
`max_drawdown_r` over the GROSS R series while `backtest_trend.py:836` and
`backtest_pullback.py` accumulate over the NET one.** Proof on the base run (106
trades): harness reports 6.6176; re-derived over `gross_r` = **6.6176 exactly**,
over `net_r_fee_only` = 6.957, over `net_r` = 7.4162. `net_total_r` and
`total_trades` reconcile (0.0004, 0), so it is isolated to the drawdown.

Two hypotheses were **refuted** first and are recorded so nobody re-tests them:
row ordering (the file is sorted by both `entry_time` and `exit_time`, all three
orderings give 7.4162, 0 overlapping consecutive trades) and an emit-schema
divergence (squeeze and pullback emit byte-identical key sets).

Squeeze's own Path-A comparisons remain apples-to-apples (both arms gross), but
**any table putting squeeze's `max_drawdown_r` beside a trend or pullback leg's
is comparing gross to net**, and gross is optimistic by construction (10.8%
here). Filed as `BL-20260820-SQUEEZE-MAXDD-IS-GROSS-WHILE-EVERY-SIBLING-IS-NET`
— the **fourth** squeeze/sibling divergence found today.

---

## 5. What this establishes

- **The bracket dimension is not flat** (median 66.8 R of spread), so E3.5's
  step-1 diagnostic returns a real axis rather than a null.
- **It is also mostly overfitting**: 112 of 133 gate rows die before a fold runs,
  20 of the surviving fold-wins are inert, and the one leg with a 70 R spread
  improves by 0.00 R.
- **One shape survives everything tested so far** — a *joint* (tp, stop, timeout)
  cell on ETH 2h pullback, `split_sensitive: false` at `pass_fraction` 1.0, where
  the best single cell on the same leg is `split_sensitive: true` at 0.4. That is
  the first evidence for E3's combine-don't-isolate premise.
- **Nothing here is shippable.** n = 1 leg, argmax-of-199 unpriced, base
  net-negative. The next step is a second, independent leg — not a Tier-3 packet.

---

## 6. Where the verdict landed (added at session close)

The coverage matrix `docs/research/exit-refinement-coverage.json` now carries
**`bracket_geometry`** as a ninth column — a **dimension**, not a ninth lever.
The eight columns beside it are all post-entry overrides on a bracket fixed at
entry; this one grades that bracket itself.

Statuses were **derived from the per-leg `gate` blocks in
`runtime_logs/e35_bracket/<leg>/2026-08-20/report.json`**, not transcribed from
the tables above, so the matrix and the artifacts cannot disagree:

| status | rows | what it means here |
|---|---:|---|
| `honest_negative` | 13 | gated, no cell cleared — the shipped triple stands **by measurement** |
| `passed_unshipped` | 2 | Path A, wf 6/6 effective, clean dispersion — **Tier-3, and not ship-ready (§ below)** |
| `pending` | 12 | 2 gate-passing but dispersion **unmeasured**; 9 never run (crypto, free lane covers them); 1 bundled row |
| `blocked` | 25 | 24 have **no candle substrate on the free lane** (equity/ETF/futures); 1 is the squeeze **refusal** |

**State the population:** 133 of 3,781 surface cells were carried into the gate
(7 per leg — the per-axis optima plus the joint argmax), across 19 legs. The
matrix has 52 rows, and each un-measured row says which of the reasons applies
rather than sharing one bucket.

⚠️ **A MATRIX ROW IS NOT ALWAYS ONE LEG, so rows and legs do not reconcile and
must not be quoted as if they did** (corrected 2026-08-20 by arithmetic, after
this line first read *"52 rows, so the column describes 19 legs measured, 33
not"* — which silently equated the two). **19 LEGS were measured; only 18 ROWS
carry a measured status.** The nineteenth, `trend_donchian_1h`, sits inside the
aggregate row `shadow fleet (turtle_soup, fade_breakout_4h, vwap,
trend_donchian_1h, *_prop)`, which stays `pending` because the rest of its legs
were not swept. So a count taken from row statuses UNDER-reports measured legs
by one (`BL-20260820-COVERAGE-MATRIX-CONFLATES-ROWS-AND-LEGS`).

**The three non-closure reasons are deliberately distinct**, because collapsing
them would report a missing feed as a negative result:

- **No feed** (24 rows) — the fetch pass sourced `data.binance.vision`, which is
  crypto-only. Nothing was measured on these legs; that is not evidence the
  shipped triple is right there.
- **No dispersion band** (2 rows — `sol_pullback_2h`/`to96`,
  `trend_donchian_sol_4h`/`to48`) — the cells pass the gate, and the band was
  never computed. Recording them as passes is precisely what
  `BL-20260820-TP-LEVEL-IS-THE-ONE-EXIT-PARAMETER-NEVER-SWEPT`'s own criteria
  forbid, and the one single-axis cell that *was* checked
  (`eth_pullback_2h`/`tp2.5`) came back **`split_sensitive`**.
- **Refused** (1 row — `squeeze_breakout_4h`/`to24`) — passes the gate,
  dispersion refused it on `harness_agreement`. Root cause
  `BL-20260820-SQUEEZE-MAXDD-IS-GROSS-WHILE-EVERY-SIBLING-IS-NET`, filed not
  fixed. A refusal is a refusal, not a caveat.

⚠️ **The two `passed_unshipped` cells are not ship-ready, and the matrix cell
says so.** Each is the **argmax of 199** surface cells and the
multiple-comparisons cost is **unpriced** — no shuffled-label or random-cell
control was run. Both sit on **ETHUSDT 2h**, so n=2 legs on one symbol is not
two independent confirmations. Pricing that argmax is a precondition of putting
either to the operator; any resulting `config/strategies.yaml` change is Tier-3
regardless.
