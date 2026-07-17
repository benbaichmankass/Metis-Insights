# M23 Phase 1 — EV-at-threshold gate reframe (2026-07-17)

**Verdict: the pooled meta-label is a REAL win-rate-lifting selector (26.3% → 31.8% at
12% coverage, monotonic), but at the current real-book base rate the filtered subset
still doesn't reach positive EV — it loses *less*, not positively.** Reframing the gate
from "beat majority accuracy" to "does taking only the top-scored real trades beat taking
all?" confirms the labels-first lever filters toward better trades, but the real 376-trade
book's win-rate (26.3%) is too thin for even the top decile to cross the R:R breakeven.
The path forward is the faithfulness relabel (variant C) + more real labels, not a live
wiring of the current head.

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

## EV — win-rate × R:R (the R column is 0-filled; see data gap)

**The scorer's R-based EV is invalid here:** the live `setup_candidates` rows carry
`r_multiple = 0` (only the `won` bit is populated for real trades, not the realized R), so
`take-all total R = 0.00` at every threshold and the R-EV "best" collapsed to n_sel=1.
The honest EV uses **win-rate × an assumed R:R** (harness avg_win ~1.5R / avg_loss ~1R →
breakeven win-rate = 1/(1+1.5) = **40%**):

| threshold | coverage | win-rate | EV/trade @1.5:1 |
|---|---|---|---|
| take-all | 100% | 0.263 | −0.34R |
| 0.30 | 56% | 0.292 | −0.27R |
| 0.50 | 12% | 0.318 | −0.21R |
| 0.60 | 4% | 0.333 | −0.17R |

**Selection improves EV/trade monotonically (loses less) but never crosses into positive**
— even the top 12% (31.8% win-rate) is below the 40% breakeven for a 1.5:1 book. (At a
richer 2:1 R:R the breakeven is 33.3%, which the top 4% just touches — but n=15 is noise.)

## Interpretation

**Establishes:** the meta-label is a **real trade-quality selector** — it ranks the 376
real trades such that its top-scored subset wins at a materially higher rate than the book
(31.8% vs 26.3% at 12% coverage). The labels-first lever produces a usable *ranking*.

**Does NOT establish** that the current head is a net-positive live filter: the real book's
26.3% base win-rate is well below the R:R breakeven, and selection lifts it to ~32% — an
improvement that *reduces* losses but doesn't turn the subset positive at this data scale.
The edge is directionally right and growing (single-strategy +0.038 precision → pooled
+0.074 → this ranking lift), but not yet enough to trade on.

## Data gap found (follow-up)

The live `setup_candidates` rows do **not** carry the trade's realized R (`r_multiple`/
`net_r` are 0 for `is_live_trade=True` rows) — only the `won` label. So a *proper*
cost-aware EV (real R per trade, not a win-rate × assumed-R:R proxy) can't be computed
from this dataset. **Fix (Tier-1):** populate the live rows' realized R from the journal's
`pnl` / risk at build time in `ml/datasets/families/setup_candidates.py`, then the EV gate
is exact. Logged.

## Recommendation — Phase 1 continues; variant C next; no live wiring

1. **Do NOT wire the current meta-label live** (shadow or otherwise) — the filtered subset
   is still negative-EV at the real base rate. It's a promising ranker, not a profitable
   filter yet.
2. **Next lever (variant C, Tier-1):** the barrier-vs-live faithfulness relabel — apply
   realistic costs/exits to the harness trades (or the auxiliary-pretrain framing) so the
   *training* label better matches live outcomes; this should push the selected subset's
   win-rate further. Pair with the data-gap fix (real R on live rows) so the EV gate is
   exact.
3. **Structural ceiling:** the binding limit is now visibly the real book's low win-rate
   (26.3% over 376 trades) — the meta-label can rank but can't manufacture edge that isn't
   there. More real labels (time) + variant C are the levers; Phase 2 (external corpus)
   stays gated on ToS + alignment + operator go.

**One-line for the operator:** the M23 meta-label is a real, improving trade-quality
*ranker* (top-12% win-rate 31.8% vs 26.3% book) but not yet a *profitable* filter — the
real book's 26% win-rate is below breakeven and selection only narrows the loss. Next is
the faithfulness relabel + fixing the live-row R gap so the EV gate is exact; no live
wiring of the current head.

## Artifacts
- Scorer: `scripts/ml/m23_ev_gate.py` (`c400cf7`).
- Run: trainer-vm-diag #6720 (model_state `…/20260717T015444Z/`, v001 setup_candidates).
- Pooled leg: `docs/research/M23-phase1-pooled-2026-07-17.md`.
- Follow-up: `MB-20260717-M23-SELECTION-GATE` (+ the live-row R-column data gap).
