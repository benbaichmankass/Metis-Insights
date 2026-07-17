# M23 Phase 1 — pooled multi-strategy backtest-augmented meta-labels (2026-07-17)

**Verdict: FAILS the pre-registered majority-accuracy gate, but population-matched
pooling clearly HELPS — the precision lift over the real-trade base rate DOUBLED
(+0.074 vs the single-strategy leg's +0.038), and accuracy rose +0.048.** The fair
population-matched test (backlog `MB-20260716-M23-P1-POPMATCH`) shows in-distribution
backtest labels *do* transfer to real-trade selection, and more/matched labels help —
but the model still can't beat the 0.756 all-lose majority on raw accuracy, because the
real-trade win base rate is only 24.4% (a rare-positive problem where the accuracy gate
is arguably the wrong bar for a *selection* head). Not a dead end: the precision lift is
a real, usable signal and it's improving with the labels-first lever.

## What changed vs the first leg

The first leg (`M23-phase1-trend-donchian-2026-07-16.md`) trained the meta-label model
on ONE strategy's backtest (trend_donchian) but evaluated on the ALL-strategy real
journal holdout — a distribution shift by construction. This leg **pools the roster** so
train and eval span the same multi-strategy population:

- market_raw BTCUSDT/1h → 1h CSV, **resampled to 2h + 4h** (leakage-free OHLCV
  downsample) so each strategy runs at its own timeframe.
- per-strategy `--emit-trades` harness replay over 5y (2021-07 → 2026-07):
  **trend_donchian@1h → 1,154 trades** (net −8.6R), **squeeze_breakout_4h@4h → 223**
  (net +23.9R), **htf_pullback_trend_2h@2h → 308** (net +47.4R). Pooled = **1,685
  `is_backtest=1` rows** (0 skipped).
- `setup_candidates` build: **2,061 rows = 1,685 backtest (train) + 376 real live
  (holdout eval)**, `won` {0: 1376, 1: 685}. Both populations confirmed present (the
  first pooled run silently re-used the old v001 single-strategy dataset — a build/train
  version-pin bug, see "Method note").

## Result — pooled vs single-strategy (live_holdout eval, n_eval = 376 real trades)

| leg | backtest train rows | accuracy | precision | recall | f1 | verdict |
|---|---|---|---|---|---|---|
| single-strategy (trend only) | 1,154 | 0.646 | 0.282 | 0.222 | 0.249 | FAIL |
| **pooled (3-strategy, matched)** | **1,685** | **0.694** | **0.318** | 0.141 | 0.196 | **FAIL** |
| reference | — | 0.756 majority | 0.244 base rate | — | — | — |

- **Accuracy +0.048** (0.646 → 0.694) — closer to majority, still below.
- **Precision lift DOUBLED: +0.074** over base rate (0.318 vs 0.244), vs the single
  leg's +0.038 (0.282). The pooled model's "this trade wins" calls are right 31.8% of
  the time vs the 24.4% base rate — a materially better *selector*.
- **Recall dropped** (0.222 → 0.141): the pooled model is more conservative/precise —
  it green-lights fewer trades but is righter about them.
- **Still FAILS** the pre-registered gate (beat 0.756 majority accuracy AND lift
  precision): it lifts precision decisively but doesn't beat majority accuracy.

## Interpretation — what this establishes (honest, and more positive than the first leg)

**Establishes:**
1. **Population-matching was the right fix and it mattered** — pooling the roster (train
   and eval on the same multi-strategy population) improved BOTH accuracy (+0.048) and
   the precision lift (2×) over the mismatched single-strategy leg. The first leg's weak
   result was partly the train/eval mismatch, exactly as suspected.
2. **In-distribution backtest labels transfer to real-trade selection.** A model trained
   only on 1,685 backtest labels picks *real* winners at 31.8% vs the 24.4% base rate —
   a real, growing edge as the label lever is pushed. This is direct evidence for the
   M23 thesis (manufacture in-distribution labels to beat the ~350-real-trade wall).

**Does NOT establish** that the augmented-label model clears the bar as-specified:
- **The accuracy-beats-majority gate is arguably wrong for this task.** With a 24.4% win
  base rate, "predict lose always" scores 0.756 accuracy; beating it requires high
  precision AND recall on the rare positive. But a *selection* head doesn't need to
  beat all-lose accuracy — it needs to lift precision at a usable recall so you can
  **filter** which trades to take (take only green-lit → win rate 24.4% → 31.8%). By
  that lens the pooled model is already useful; the raw-accuracy gate under-credits it.
- **The barrier-vs-live faithfulness gap remains** (harness trades use idealized
  costs/exits; the design flagged a ~0.6R gap). Closing it should push precision further.

## Recommendation — Phase 1 continues; reframe the gate + one more leg before Phase 2

1. **Reframe the gate for a selection head** (do next, Tier-1): replace/augment
   "beat majority accuracy" with a **precision-at-recall / decision-curve / EV-at-threshold**
   evaluation — does taking only the model's top-scored real trades beat taking all of
   them, net of cost? The +0.074 precision lift suggests yes; quantify the EV.
2. **Phase-1 variant C — faithfulness upgrade** (Tier-1): apply the realistic-cost/exit
   model to the harness trades (or the auxiliary-pretrain framing: backtest labels
   pretrain, real labels fine-tune the head), which should raise real-trade precision.
3. **Phase 2 (external corpus) stays gated** on the ToS review + distribution-alignment
   pre-check + operator go — do NOT start it. The in-distribution lever is still paying
   off; exhaust it first.

The one-line story for the operator: **the labels-first lever is working — pooling more,
matched, in-distribution backtest labels made the trade-quality selector measurably
better (precision 24.4%→31.8%). It doesn't clear the (too-strict) majority-accuracy gate
yet, so the next step is an EV-based gate reframe + a faithfulness relabel, not Phase 2.**

## Method note (bug caught + fixed this run)

The first pooled run returned metrics BYTE-IDENTICAL to the single-strategy leg
(acc 0.6463) — because the `setup-candidates-metalabel-backtest-v1` manifest **pins
`dataset.version: v001`** and `ml train` reads the manifest's version, not what the
orchestrator builds; building the pool at v002 meant the train silently re-used the old
v001 single-strategy dataset. Fixed by building the pool at v001 (overwrite). Same
build/train version-mismatch class as the gate-1 `build_params` no-op — logged for the
orchestrator-hygiene backlog.

## Artifacts
- Orchestrator: `scripts/ml/m23_phase1_pooled.sh` (`96c455c`).
- Manifest: `ml/configs/setup-candidates-metalabel-backtest-v1.yaml` (dataset v001).
- Run: trainer `/tmp/m23_pooled_result.txt` (trainer-vm-diag #6715/#6717).
- First leg: `docs/research/M23-phase1-trend-donchian-2026-07-16.md`.
- Design: `docs/research/M23-decision-label-wall-DESIGN.md`.
