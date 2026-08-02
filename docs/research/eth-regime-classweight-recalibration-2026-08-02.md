# ETH 15m regime head — class-weight recalibration (2026-08-02)

> **Backlog:** MB-20260627-003 (ETH regime head NO_EDGE live, RG4 0.46 → needs
> retrain before promotion) · MB-20260628-REGIME-SOAK-READINESS (ETH half).
> **Tier:** Tier-1 research + a candidate/offline manifest edit. The head is
> shadow-stage (never order-influencing); **advisory promotion stays Tier-3,
> operator-gated.** This document is the evidence, not a promotion.

## The question

`eth-regime-15m-lgbm-v1` is the multi-symbol-A retrain built to fix the ETH
regime head's live NO_EDGE (the 1h head reads near-constant-`volatile`; the 15m
retrain still scores RG4 0.46 ≈ random). MB-20260627-003 flags it "needs retrain
before promotion" but did not say *what to change*. The manifest header itself
names the suspect:

> *"Mirror `btc-regime-15m-lgbm-v2`: ~28× inverse-base-rate weight (BTC 15m
> volatile base rate ~3.6%). **First knob to sweep if ETH's realized 15m base
> rate differs materially.**"*

The head ports BTC's `class_weight {range: 1.0, volatile: 28.0}` **verbatim**.
That 28× is justified in the BTC manifest as ≈ the inverse of BTC's volatile
base rate (`1 / 0.036 ≈ 28`) — without it the booster predicts only the majority
`range` class and `f1_volatile` collapses; *with too much of it*, the booster
over-commits to `volatile` and the output saturates (the same near-constant
signature, from the other direction). So the head's discrimination is only as
good as the match between its class weight and the symbol's **actual** base rate.

## Finding 1 — ETH's realized 15m volatile base rate is ~2× BTC's (decisive)

Measured directly on the datasets the live 15m heads train on
(`datasets-out/market_features/<sym>/15m`, the canonical nightly build), at the
frozen production label `vol_threshold=0.005`, `forward_window_m=5`
(trainer-vm-diag #8341):

| symbol | dataset | n | volatile frac | **implied 1/base-rate** | live head `volatile` weight |
|---|---|---:|---:|---:|---:|
| BTCUSDT | v002 | 175,272 | **4.46%** | ~22× | 28× — calibrated ✓ |
| **ETHUSDT** | v002 | 175,272 | **9.22%** | **~11×** | **28× — ~2.6× too aggressive ✗** |
| SOLUSDT | v002 | 168,168 | 20.66% | ~5× | (SOL heads use their own) |

The forward-vol distribution confirms the mechanism is intrinsic market
structure, not a labeling artifact: ETH's forward 5-bar log-return vol runs
**consistently higher** than BTC's at every quantile (ETH p90 0.00484 / p95
0.00625 vs BTC p90 0.00367 / p95 0.00481), so the shared `0.005` cut sits at
~ETH's p90 but ~BTC's p95 — ETH is simply the more volatile asset, and its
`volatile` class is ~2× more common. The frozen `0.005` threshold is
**deliberately shared fleet-wide** (the `regime_policy.yaml` vol-gate cells key
on it), so the correct response is to match the *class weight* to ETH's own base
rate, **not** to move the threshold (which would desync the gate).

**Implication:** the inverse-base-rate recipe applied to ETH's *own* data gives
`volatile ≈ 1 / 0.0922 ≈ 11×`, not 28×. The ported 28× over-weights the minority
class ~2.6× → the booster over-commits to `volatile` → near-constant output → the
observed live NO_EDGE. This is a **calibration** failure, and it has never been
corrected (every ETH LGBM regime manifest carries the ported 28×; the one
class-weight variant, `eth-regime-15m-hmm-cw-v1`, is a different model family and
a documented negative).

## Finding 2 — in-session class-weight A/B

_(trainer-vm-diag #8342 — `ml train --no-register` on the identical ETH v002
dataset, `class_weight(volatile)` swept, per-class metrics from the manifest's
own `time_aware_holdout` evaluator.)_

| `volatile` weight | f1_volatile | precision_volatile | recall_volatile | macro_f1 | weighted_f1 |
|---:|---:|---:|---:|---:|---:|
| **28** (current) | 0.2784 | **0.1707** | 0.7539 | 0.5426 | 0.7655 |
| 15 | 0.3126 | 0.2051 | 0.6567 | 0.5889 | 0.8221 |
| **11** (≈ 1/base-rate) | **0.3312** | 0.2288 | 0.5993 | 0.6106 | 0.8465 |
| 8 | 0.3384 | 0.2515 | 0.5170 | 0.6244 | 0.8658 |

**The over-commitment reverses exactly as the base-rate diagnosis predicts.** The
28× head has precision_volatile **0.171** at recall **0.754** — it predicts
`volatile` on ~4.4× more bars than are truly volatile (0.754/0.171 ≈ base-rate
inflation), the near-constant-output failure mode. Lowering the weight toward
ETH's own inverse base rate rebalances precision↑/recall↓ and lifts f1_volatile,
macro_f1, and weighted_f1 **monotonically**. The 28× recipe is confirmed the
worst of the four arms on every aggregate metric.

## Disposition — recalibrate in place to **11×**

Editing `eth-regime-15m-lgbm-v1.yaml` `class_weight volatile: 28.0 → 11.0` in
place (the model_id and everything else unchanged). Rationale for **11×** over
the marginally-higher-f1 8× arm:

1. **Principled + fleet-consistent:** 11× ≈ `1 / 0.0922`, the same
   inverse-base-rate recipe BTC's head uses (28× ≈ `1 / 0.036`). ETH now gets
   the recipe applied to *ETH's* data instead of BTC's.
2. **f1 is at its plateau:** 0.3312 (11×) vs 0.3384 (8×) is a ~2% difference,
   inside noise; the curve has flattened by 11×.
3. **Recall matters for a gate:** the vol-gate fires when `P(volatile) > 0.5`
   for an OFF-cell — under-firing (missing a volatile episode, letting a
   money-losing cell trade) is the costlier error, so the higher recall_volatile
   at 11× (0.599 vs 8×'s 0.517) is the safer operating point.

**This is the calibration fix, not a proven live-NO_EDGE resolution.** The A/B is
**offline holdout**; the live RG4 0.46 is a live-row skew read. Recalibration is
the *necessary precondition* for a fair RG4 re-read (a miscalibrated weight makes
the live read meaningless), and the mechanism it corrects — output saturation —
is the same mechanism behind a near-constant-output live NO_EDGE. But whether the
recalibrated head clears **RG4 ≥ 0.55** can only be measured after it soaks under
the corrected weight. The nightly cycle retrains `eth-regime-15m-lgbm-v1` at 11×;
its fresh RG4 read is the post-soak Tier-3 promotion gate (MB-20260627-003 stays
open, retargeted from "needs retrain" to "recalibrated → awaiting fresh RG4
soak"). No promotion is enacted here.

## Method notes

- Base rate + forward-vol quantiles: `value_counts(regime_label)` +
  `forward_log_return_vol` percentiles over each built dataset's `data.jsonl`,
  read straight off the trainer (no re-derivation of the label — the built
  dataset already carries `regime_label`, defined by
  `market_features._label_regime`: `volatile` iff `forward_vol > vol_threshold`).
- The local `data/ohlcv/*.csv` fixtures are synthetic (300 rows, single close
  value) and were **not** used — base rates are from the real trainer datasets.
- RG4 (live-row skew AUC) requires a live shadow soak and cannot be read
  in-session on a fresh retrain; `f1_volatile` (holdout) is the in-session
  discrimination proxy, RG4 confirmation is the post-soak Tier-3 gate.
