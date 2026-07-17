# M23 Phase 1 — first leg (trend_donchian backtest-augmented meta-labels): findings (2026-07-16)

**Verdict: HONEST NEGATIVE on the pre-registered gate, with a validated pipeline
and a faint positive precision signal.** The first M23 Phase-1 leg — train a
trade-quality meta-label model on **backtest-augmented** labels (trend_donchian's
own harness replay over 5y of 1h history) and evaluate on the **real-trade**
`live_holdout` — ran end-to-end cleanly and returned: accuracy **0.646** (below the
0.756 majority baseline) with precision **0.282** vs the 0.244 real-trade base rate
(a small +0.038 lift). The gate requires **both** (beat majority AND lift
precision); it got the precision lift but failed the accuracy gate → **FAIL**. The
augmented labels did **not** manufacture a decisively better meta-label model than
real-labels-only on this leg. This is the expected-difficulty result the M23 design
called out (barrier-vs-live faithfulness); the value banked is the **validated
end-to-end pipeline** and a concrete, better-scoped next iteration.

## What M23 Phase 1 asked

Can we break the **decision-label wall** (~350 real trades — the single binding
constraint the 2026-07-16 convergence review found across T0.1–T1.3) by
manufacturing **in-distribution** labels from our own strategy history? The Phase-1
mechanism: triple-barrier + meta-labeling over years of the strategies' own
backtest trades (10–100× the live count, with *our* costs/exits), then require the
augmented-label model to beat a real-labels-only / majority baseline on purged-CV +
a real-money holdout. This is the labels-first pillar of the master-AI north star.
Seeds: `MB-20260530-001`, `MB-20260705-META-LABEL-WALL`, `MB-20260629-ALLOC-COSTCAP`.

## Method (all Tier-1 / offline / trainer-side)

Orchestrator `scripts/ml/m23_phase1_experiment.sh` (committed `b4dd80e`; verified
correct against source), run on the trainer VM 2026-07-16:

1. `market_raw BTCUSDT/1h/v002` → OHLCV CSV (43,825 bars, **2021-07-16 →
   2026-07-15**, ~5y).
2. `scripts/backtest_trend.py` (trend_donchian, donchian=20 / atr_stop=2.5 /
   trail=3.0) over the deep 1h history → **1,154 harness trades** (win 35.9%, net
   −8.11R, expectancy −0.007R; by_year 2022/2023 positive, 2024/2025 negative).
3. `record_harness_trades.py` → **1,154 `is_backtest=1` rows** (0 skipped) in a
   TEMP db (never the money journal; seeded with the journal's `trades` schema).
4. `ml.datasets build setup_candidates v001` — **backtest-train** (the 1,154 harness
   rows, `event_source=backtest`) **+ real-eval** (the live journal, `live_holdout`).
5. `ml train setup-candidates-metalabel-backtest-v1` (target `won`, `live_holdout`
   split — trains on backtest+synthetic, evaluates on held-out **real** trades).
6. Gate: accuracy vs the 0.756 majority baseline + precision lift off the 0.244
   real-trade base rate.

## Result — the gate FAILS (honest negative)

`live_holdout` eval metrics (n_eval = **376** real-trade rows):

| metric | value | reference | read |
|---|---|---|---|
| accuracy | **0.646** | 0.756 majority | **below** — worse than always-predict-"lose" |
| precision | **0.282** | 0.244 base rate | **+0.038 lift** — green-lit trades win slightly more often |
| recall | 0.222 | — | low — catches ~1 in 5 real winners |
| f1 | 0.249 | — | low |
| brier | 0.221 | — | — |

Pre-registered gate = **beat majority AND lift precision**. Precision lifts; accuracy
does not → **VERDICT = FAIL** (`m23_phase1_done: true`, ran to completion — a
falsified hypothesis, not a broken run).

## Interpretation — what this does and does NOT establish

**Establishes:**
- **The M23 Phase-1 machinery works end-to-end** — deep-history harness replay →
  `is_backtest=1` recording → `setup_candidates` backtest-train+real-eval →
  meta-label train → gate. That pipeline is the reusable Phase-1 deliverable; the
  1,154 backtest labels are a real ~3× multiple over trend_donchian's live count.
- **A faint positive transfer exists** — a model trained *only* on backtest labels
  does slightly better than base rate at picking **real** winners (precision
  0.282 > 0.244). Backtest labels are not pure noise for the real task; the signal
  is just weak and sub-gate at this scale.

**Does NOT establish** that backtest-augmented labels beat real-labels-only for
trading decisions. Two honest bounds:
1. **Accuracy below majority** is the decisive miss: the model over-predicts the
   minority "won" class (recall 0.22, but its false-positives drag accuracy under
   the 0.756 all-lose baseline). On a pure accuracy gate this fails cleanly.
2. **Train/eval population mismatch (the prime suspect, and the next iteration).**
   This leg trains on **one strategy's** backtest trades (trend_donchian) but the
   `live_holdout` real-eval pulls the **whole journal** (all strategies, n=376).
   That is a distribution shift *by construction* — a trend_donchian-backtest model
   is being graded on all-strategy real trades. The M23 design's barrier-vs-live
   faithfulness caveat (~0.6R gap) compounds it. A within-strategy train+eval (or a
   pooled multi-strategy backtest-train matched to the multi-strategy real-eval) is
   the fair test and the obvious next leg.

This is the **same label-wall texture** as the T0.1–T1.3 negatives: a plausible
lever returns a weak, sub-gate signal because the *decision label* is thin and
noisy — except here the lever is aimed **directly at the label** (the right target),
and the miss is about *faithfulness/matching*, not about the idea being wrong.

## Decision / next iteration (Phase 1 continues; not closed)

- **Record the honest negative** for the trend_donchian first leg — augmented labels
  do not clear the gate as-run. Do **not** wire backtest labels into any live head.
- **Next leg (the fair test):** rebuild `setup_candidates` so **train and eval are
  the same population** — either (a) within-strategy (trend_donchian backtest-train →
  trend_donchian real holdout; needs the per-strategy real count to support a
  holdout), or (b) pooled multi-strategy backtest-train (replay squeeze / htf_pullback
  / the other legs too) matched to the all-strategy real holdout. (b) is the higher-n,
  more-in-distribution test and the recommended next step.
- **Faithfulness upgrade (design's own caveat):** apply the realistic-cost/exit model
  to the harness trades (or treat backtest labels as an *auxiliary* pretraining
  target with the real labels as the fine-tune head) so the ~0.6R barrier-vs-live gap
  doesn't poison the label.
- **Phase 2 (external corpus) stays gated** on the ToS review + the
  distribution-alignment pre-check (unchanged).

## Status vs the gate chain

Phase-1 **first leg RAN → honest negative**; the pipeline is validated and the fair
within/pooled-population re-run is the next Tier-1/offline leg. No live head touched.

## Artifacts
- Orchestrator: `scripts/ml/m23_phase1_experiment.sh` (`b4dd80e`).
- Manifest: `ml/configs/setup-candidates-metalabel-backtest-v1.yaml`.
- Run: trainer `/tmp/m23_phase1_result.txt` (via trainer-vm-diag #6703).
- Design: `docs/research/M23-decision-label-wall-DESIGN.md`.
- Convergence context: `docs/research/reads-everything-convergence-status-2026-07-16.md`.
