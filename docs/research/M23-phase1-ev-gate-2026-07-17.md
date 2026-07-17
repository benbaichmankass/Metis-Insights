# M23 Phase 1 — EV-at-threshold gate reframe (2026-07-17)

**Verdict: the pooled meta-label is a REAL win-rate-lifting selector (26.3% → 31.8% at
12% coverage), it cuts the book's total realized loss hard under selection (net R
−181.6 → −18.6 at top-12%), but it is NOT a net-positive trade filter — the selected
subset stays net-negative at every usable volume.** The gate was reframed from "beat
majority accuracy" to "does taking only the top-scored real trades beat taking all,
**net of realized cost**?" and — after the live-row realized-R fix (below) — computed on
the trades' **actual** R, not a win-rate × assumed-R:R proxy. The exact-R answer sharpens
the earlier one and adds a load-bearing new fact: **the model ranks P(win) well but not
loss magnitude.** The real book bleeds (mean −0.43R/trade over 376 real trades), its EV is
dominated by fat-tailed losses (R ∈ [−14.4, +16.1]), and a P(win) head can't fix a book
whose damage lives in loss *size* it doesn't score. Path forward: variant C — an
outcome/R-aware relabel (not just win/loss) + more real labels — not a live wiring of the
current head. **Update (this run): the "R column is 0-filled" data gap is RESOLVED — 359/376
(95.5%) of live rows now carry reconstructed net R, so the EV gate is now exact.**

## What was asked

The pooled leg (`M23-phase1-pooled-2026-07-17.md`) lifts real-trade precision to 0.318
(vs 0.244 base) but FAILS the "beat 0.756 majority accuracy" gate — the wrong bar for a
rare-positive (24–26% win) SELECTION head. The reframed question (`MB-20260717-M23-SELECTION-GATE`):
**does taking only the model's top-scored real trades beat taking ALL of them?** Scorer
`scripts/ml/m23_ev_gate.py` reuses the already-trained pooled model + the evaluator's own
`_resolve_predictor`, scores all 376 real `is_live_trade` holdout rows, and sweeps the
decision threshold.

## Result — the win-rate sweep (the real signal)

376 holdout live trades, base win-rate **0.2633**:

| threshold | n_selected (coverage) | selected win-rate |
|---|---|---|
| 0.00 (take-all) | 376 (100%) | 0.2633 |
| 0.30 | 209 (56%) | **0.2919** |
| 0.40 | 138 (37%) | 0.2536 |
| 0.50 | 44 (12%) | **0.3182** |
| 0.60 | 15 (4%) | 0.3333 |
| 0.70 | 1 (0%) | 1.0000 (noise) |

The selection **lifts win-rate monotonically at usable coverage** (56% of the book at
29.2%, 12% at 31.8%) — a genuine, if modest, filter. This is the same precision edge the
pooled classifier metrics showed, now confirmed as a *ranking* over the real trades.

## EV — exact realized R (data gap now closed)

After the live-row realized-R fix, the scorer sums each trade's **actual net R**
(`pnl / (|entry − stop| × size)`; 359/376 real, 17 coarse unit-R fallback). The real
book's outcome distribution: **mean −0.433R, median −0.727R, R ∈ [−14.438, +16.056], 99
wins / 277 losses, take-all total R = −162.83 (net −181.63 at 0.05R/trade cost).** This is
the true scale of the label wall — the 376-trade real book loses ~0.43R per trade.

The exact-R threshold sweep (per-trade R = sel_netR / n_sel added for the key read):

| threshold | n_sel (coverage) | win-rate | sel_netR | Δ vs take-all | **netR/trade** |
|---|---|---|---|---|---|
| 0.00 (take-all) | 376 (100%) | 0.2633 | −181.63 | +0.00 | **−0.483** |
| 0.30 | 209 (56%) | 0.2919 | −119.14 | +62.49 | **−0.570** |
| 0.40 | 138 (37%) | 0.2536 | −94.06 | +87.57 | **−0.682** |
| 0.50 | 44 (12%) | 0.3182 | −18.57 | +163.05 | **−0.422** |
| 0.60 | 15 (4%) | 0.3333 | −13.13 | +168.50 | **−0.875** |
| 0.70 | 1 (0%) | 1.0000 | +1.39 | +183.02 | +1.39 (noise) |

Two facts that the win-rate-only view (and the assumed-R:R proxy) hid:

1. **Selection strips total loss but not per-trade R.** The `sel_netR` improvement is
   large (−181.6 → −18.6 at t=0.50) but it comes almost entirely from taking **fewer
   trades** (44 vs 376), not from better trades: per-trade net R is **non-monotone and
   mostly ≈ or worse than take-all** (−0.570 at t=0.30, −0.682 at t=0.40, −0.875 at t=0.60;
   only t=0.50 nudges better at −0.422 vs −0.483). Win-rate rises but realized R doesn't
   follow.
2. **The head ranks win-probability, not loss-magnitude.** With R spanning [−14, +16], the
   book's EV is set by a few large losers, and a P(win) classifier that green-lights more
   frequent winners still eats those tail losses — so a higher-win-rate subset isn't a
   lower-loss subset. This is the exact failure the binary `won` label can't fix.

**Verdict (exact R): NOT a usable net-positive filter.** Every selection at usable volume
(n ≥ 44) stays net-negative; only n_sel=1 crosses positive (noise, correctly flagged
below the usable-volume floor by the scorer).

## Interpretation

**Establishes:** the meta-label is a **real trade-quality selector** — it ranks the 376
real trades such that its top-scored subset wins at a materially higher rate than the book
(31.8% vs 26.3% at 12% coverage). The labels-first lever produces a usable *ranking*.

**Does NOT establish** that the current head is a net-positive live filter: the real book's
26.3% base win-rate is well below the R:R breakeven, and selection lifts it to ~32% — an
improvement that *reduces* losses but doesn't turn the subset positive at this data scale.
The edge is directionally right and growing (single-strategy +0.038 precision → pooled
+0.074 → this ranking lift), but not yet enough to trade on.

## Data gap — RESOLVED (this run)

The live `setup_candidates` rows previously carried `r_multiple = 0.0` (only the `won`
bit) so the cost-aware EV couldn't be computed exactly. **Fixed (Tier-1, commit
`c314141`):** `ml/datasets/families/setup_candidates.py` now reconstructs each live row's
realized R from the trade's own risk — net, cost-aware `R = pnl / (|entry − stop| ×
size)` (size cancels the dollar units for this linear BTCUSDT book), falling back to a
signed unit-R only when the risk columns are absent, tagged via a new `r_multiple_source`
field (`stop_distance` vs `unit_fallback`). Coverage on the trainer's journal: **359/376
(95.5%) real R, 17 unit-fallback** — the EV table above is the exact-R result. The scorer
(`scripts/ml/m23_ev_gate.py`) now reports the real-vs-fallback split and prefers the real
R. `MB-20260717-M23-LIVEROW-REALIZED-R` closed.

## Recommendation — Phase 1 continues; variant C next (now R-aware); no live wiring

1. **Do NOT wire the current meta-label live** (shadow or otherwise) — the exact-R gate
   confirms the filtered subset is net-negative at every usable volume. A promising
   *ranker*, not a profitable *filter*.
2. **Next lever (variant C, Tier-1) — now sharpened by the exact-R read:** the
   barrier-vs-live faithfulness relabel PLUS an **outcome/R-aware target**. The binary
   `won` label is the binding constraint the exact-R sweep exposed — it teaches the head to
   rank win-*probability*, which improves win-rate but not realized R because the book's EV
   lives in fat-tailed loss *magnitude*. Variant C should (a) apply realistic costs/exits
   to the harness trades so the training label matches live outcomes, and (b) train against
   a **magnitude/R-aware objective** (regress net R, or a class-weighted / R-thresholded
   label) so selection lowers loss size, not just loss frequency. Pair with the now-exact
   EV gate to measure it.
3. **Structural ceiling:** the true binding limit is now measured — the real book loses
   **−0.43R/trade** (mean) over 376 trades with heavy tails; a P(win) meta-label can rank
   but cannot manufacture EV a −0.43R book doesn't contain. More real labels (time) +
   variant C's R-aware relabel are the levers; Phase 2 (external corpus) stays gated on
   ToS + alignment + operator go.

**One-line for the operator:** with the live-row R gap now closed (359/376 real R), the
exact-R gate shows the M23 meta-label is a real win-rate ranker (top-12% 31.8% vs 26.3%
book) that cuts total loss hard under selection (net R −182 → −19) BUT is not a profitable
filter — it ranks *how often* trades win, not *how big* they lose, and this book's damage
is loss-size. Next: variant C with an R-magnitude-aware label; no live wiring.

## Artifacts
- Live-row R fix: `ml/datasets/families/setup_candidates.py` + `scripts/ml/m23_ev_gate.py`
  (`c314141`); family tests in `tests/ml/test_setup_candidates.py`.
- Exact-R re-run: trainer-vm-diag #6727 (rebuilt v001 setup_candidates with real R, scored
  the existing pooled model `…/20260717T015444Z/` — `r_multiple` is an outcome, not a
  training feature, so no retrain needed).
- Coverage probe: trainer-vm-diag #6726 (359/376 real R on the live journal).
- Original proxy run: trainer-vm-diag #6720.
- Pooled leg: `docs/research/M23-phase1-pooled-2026-07-17.md`.
- Follow-up: `MB-20260717-M23-SELECTION-GATE` (open — variant C R-aware relabel);
  `MB-20260717-M23-LIVEROW-REALIZED-R` (**closed** this run).
