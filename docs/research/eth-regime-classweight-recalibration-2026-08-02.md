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

<!-- A/B RESULT TABLE — pending #8342 -->

## Disposition

<!-- pending A/B: recalibrate-in-place (if ~11× lifts f1_volatile / un-saturates)
     vs feature-inadequacy route (if no weight helps) -->

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
