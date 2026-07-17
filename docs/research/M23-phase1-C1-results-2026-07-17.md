# M23 Phase 1 — variant C1 (R-aware target) results + τ sweep (2026-07-17)

**Verdict: the R-aware target WORKS in the direction the exact-R gate predicted — it
reproducibly turns the top slice of the ranking net-POSITIVE (which the `won` target never
did) — but the positive region never exceeds ~11 trades / 3% coverage at any threshold,
far below the ≥40 (≥10%) usable-volume floor. The binding constraint is now unambiguously
LABEL VOLUME, not target framing: 376 real trades at ~26% win / fewer big-R winners only
supports a confident top slice of ~10.** Variant C1 is a validated lever with a
characterized ceiling; no live wiring. Next levers are label-volume, not more target
engineering: C3 (faithfulness relabel, to make the abundant backtest labels transfer a
better R-ranking) and more real labels (time), with C2 (regress R) worth one shot for a
smoother ranking.

## What C1 is

The exact-R EV gate (`M23-phase1-ev-gate-2026-07-17.md`) established that the pooled `won`
(pnl>0) meta-label ranks win-*probability* (win-rate 0.263 → 0.318 under selection) but not
loss-*magnitude* (per-trade net R non-monotone/worse), so it never produces a net-positive
selected subset. Variant C1 changes only the **training target** to `won_r =
1[r_multiple ≥ τ]` — a materially-good-trade detector — so the head learns to rank realized
R. Everything else (features, trainer, live_holdout protocol, real-trade holdout, the EV
gate) is identical; the EV gate still scores real win/loss + realized net R, independent of
the training target. Manifest: `setup-candidates-metalabel-backtest-c1-v1.yaml`
(dataset `v011`); builder param `r_label_threshold`; the R-aware target rides on the
live-row realized-R fix (`c314141`, 359/376 = 95.5% real R).

## Result — τ sweep (trainer #6730 τ=0.5, #6731 full sweep; 376 real live-holdout rows)

Take-all baseline (unchanged across runs): win-rate 0.2633, total R −162.83, **net R −181.63**
(0.05R/trade cost). The R-aware selection's best points:

| τ | live won_r base | widest net-positive point | tip (best net R) | crosses positive? |
|---|---|---|---|---|
| 0.25 | 0.226 | — (t=0.50 n=31 → **−22.60**) | t=0.56 n=4 → −1.30 | **no** (never positive) |
| **0.50** | 0.210 | **t=0.50 n=10 → +2.56** | t=0.52 n=6 → +3.85 | yes, n ≤ 10 |
| **0.75** | 0.178 | **t=0.42 n=11 → +0.85** | t=0.42 n=11 → +0.85 | yes, n ≤ 11 (widest) |
| 1.00 | 0.141 | t=0.50 n=2 → +2.04 | t=0.49 n=2 → +2.04 | yes, n = 2 (noise) |

- **τ=0.50 reproduced exactly** with the fixed valid `v011` version (n=10 @ t=0.50 net
  +2.56; tip n=6 +3.85) — the version-format fix (`v001c1`→`v011`) didn't change the
  science.
- **The lever works and is reproducible:** τ ∈ {0.50, 0.75, 1.00} all cross net-positive at
  the top of the ranking — the R-aware head identifies a handful of genuinely positive-EV
  trades that the `won` head's selection never isolated (that one stayed net-negative at
  every volume, best usable −18.57 at n=44).
- **But the positive region is tiny at every τ:** the widest is τ=0.75's **n=11 (3%
  coverage, +0.85R)** — still far below the ≥40 / ≥10% usable-volume floor. Lowering τ to
  0.25 (more positives to train on) does NOT widen the usable positive region — it goes
  net-*negative* even at its tip (the target gets too close to plain `won`); raising τ to
  1.0 sharpens the tip but shrinks it to n=2.

## Interpretation — the ceiling is label volume

**Establishes:** the target-framing hypothesis was right. Reframing the label from
win/lose to clears-τR moves the selected subset from *always net-negative* (won target) to
*net-positive at the top* (won_r target), reproducibly across τ. The head CAN find
positive-EV trades in this book; the R-aware target is the key that unlocks them.

**Does NOT establish** a usable filter — and localizes exactly why. The net-positive region
tops out at ~11 trades regardless of τ. With 376 real trades and ~26% winners (and fewer
big-R winners still), the model can only *confidently* green-light a top slice of ~10 — the
data-scale floor the M23 thesis predicted. This is not a target-engineering problem
(sweeping τ doesn't widen it) — it is a **label-count** problem. The R-aware lever has
converted the label wall from "the target can't rank R" to "there aren't enough labels to
rank R at usable volume", which is the more honest and more actionable framing.

## Recommendation — no live wiring; label-volume levers next

1. **Do NOT wire C1 live.** n≈11 positive-EV trades is far too thin to trade; the gate
   correctly fails on usable volume. (This stays a Tier-3, operator-gated shadow-soak
   decision even if a future run clears the floor.)
2. **C3 — faithfulness relabel (next Phase-1 lever, Tier-1).** The backtest train pool
   (1,685 rows) is ~4.5× the real book; if the barrier-vs-live ~0.6R faithfulness gap is
   closed (realistic cost/exit re-sim, or the auxiliary-pretrain framing), the head learns
   a better R-ranking from that abundant pool and the positive region should widen. This is
   the highest-leverage remaining Phase-1 move.
3. **C2 — regress net R (one shot, Tier-1).** A regressor on `r_multiple` (vs the τ-
   thresholded classifier) may extract a smoother ranking and a slightly wider positive
   region; worth a single run given C1's positive-but-thin result. Needs a regression
   manifest/metric.
4. **More real labels (time).** The structural fix. The positive region grows with the real
   book; at the current ~3-4 trades/week accrual this is the slow lever, which is exactly
   why the backtest-augmentation (C3) and Phase 2 (external corpus, gated) exist.
5. **Phase 2 (external corpus) stays gated** on ToS + distribution-alignment + operator go.

**One-line for the operator:** the R-aware target (variant C1) is the first thing to turn a
selected subset of the real book net-POSITIVE (+2.56R on the top 10 trades, reproducible
across thresholds) — the win/lose target never did — but the positive region caps at ~11
trades at every threshold, so the wall is now precisely label VOLUME, not the target. Next
is the faithfulness relabel (C3) to make the 4.5×-larger backtest pool transfer a wider
R-ranking; no live wiring (n≈11 is far too thin).

## Artifacts
- Builder + target: `ml/datasets/families/setup_candidates.py` (`r_label_threshold`/`won_r`,
  `431ebe9`); manifest `ml/configs/setup-candidates-metalabel-backtest-c1-v1.yaml`
  (`431ebe9`, version fix `8787f5b`); tests in `tests/ml/test_setup_candidates.py`.
- Runs: trainer-vm-diag #6730 (τ=0.5 first cut), #6731 (τ ∈ {0.25,0.5,0.75,1.0} sweep,
  valid `v011`).
- Design: `docs/research/M23-phase1-variantC-DESIGN-2026-07-17.md`.
- Prior leg: `docs/research/M23-phase1-ev-gate-2026-07-17.md`.
- Follow-up: `MB-20260717-M23-SELECTION-GATE` (C2/C3 + the label-volume ceiling).
