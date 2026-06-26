# Fleet model scorecard — RG3 + RG4 across the shadow regime fleet (2026-06-26)

**What this is.** Every shadow regime head read through *both* replay-pre-gate
stages, in one table, to classify each head keep / watch / fix-skew / kill and —
the operator's actual question — to learn **how to build models that will pass
the gate**.

- **RG3 (Stage-1, clean-candle replay).** Feed clean candles through the *live*
  feature builder and score vs the dataset's own `regime_label`. Does the head
  **discriminate** the realized regime? (necessary, not sufficient)
- **RG4 (Stage-2, logged-live-row replay).** Re-score the **exact feature rows
  the live runtime logged** to `shadow_predictions.jsonl`. A per-stage AUC
  collapse vs Stage-1 is **train/serve skew** — the `btc-regime-yz` failure mode
  (great offline, broken live).

Run: `scripts/ml/fleet_scorecard.sh 8000` on the trainer VM as a `nohup`
background job (the full candle-replay outlives one SSH session). Trainer-vm-diag
relays #4697 (launch) / #4699 (read). `shadow_predictions.jsonl` = 109,641 rows.

## The table

| Model (shadow head) | RG3 offline | RG4 live | Live rows (labeled) | Verdict |
|---|---|---|---|---|
| btc-regime-5m-lgbm-yz-v1 | 0.92 | **0.82** | 8362 (all) | **KEEP — strongest** |
| btc-regime-5m-lgbm-v2 | 0.89 | **0.79** | 11119 (all) | **KEEP** |
| btc-regime-5m-baseline-v1 | 0.82 | 0.73 | 11675 (all) | KEEP |
| mes-regime-5m-lgbm-v2 | 0.80 | 0.77 | 2981 (~204) | KEEP (thin labels) |
| mes-regime-5m-baseline-v1 | 0.68 | 0.75 | 3031 (~254) | KEEP (live > offline) |
| btc-regime-15m-lgbm-yz-v1 | 0.83 | 0.76 | 2118 (all) | KEEP |
| btc-regime-15m-lgbm-v2 | 0.81 | 0.72 | 2152 (all) | KEEP |
| btc-regime-15m-baseline-v1 | 0.75 | 0.69 | 2207 (all) | KEEP |
| btc-regime-1h-lgbm-v2 | 0.78 | 0.61 | 793 (all) | **WATCH — degrades live** |
| btc-regime-1h-lgbm-yz-v1 | 0.79 | 0.60 | 585 (all) | **WATCH** |
| btc-regime-1h-lgbm-funding-svble-v1 | 0.78 | **0.55** | 449 (all) | **WATCH — at floor** |
| **mes-regime-15m-lgbm-v2** | **0.89** | **0.32** 🔴 | 1102 (~77) | **FIX-SKEW — anti-predictive** |
| **mes-regime-15m-baseline-v1** | 0.58 | **0.44** 🔴 | 1152 (~127) | **KILL — weak + anti-predictive** |
| mes-regime-1d-lgbm-v2 | 0.92 | — | 242 (0) | UNSCOREABLE (no labeled live rows) |
| mes-regime-5m-lgbm-yz-v1 | 0.84 | — | 1629 (0) | UNSCOREABLE |
| eth-regime-1h-lgbm-v1 | 0.73 | — | 84 (0) | UNSCOREABLE (too new) |
| eth-regime-1h-lgbm-xasset-v1 | 0.70 | — | 320 (0) | UNSCOREABLE (xasset probe, too new) |

`mf=True/False` in the raw output = whether the logged live record carried the
market-feature block (False → scored 0.5 NO_EDGE).

## Findings

1. **The gate works — and just earned its keep.** `mes-regime-15m-lgbm-v2` is
   0.89 offline → **0.32 live (anti-predictive)**. Promoting on offline AUC would
   have *inverted* its contribution to order decisions. RG3 alone green-lights
   all 17 heads, including this one. RG4 is the differentiator.

2. **The BTC 5m/15m fleet is genuinely healthy** — six heads hold 0.69–0.82
   live, `mf=True`, near-complete labeling (`unlab` 0–6). **These are the real
   promotion-readiness candidates**, not the 1h heads earlier notes assumed were
   the strong ones.

3. **The BTC 1h fleet degrades materially live** (0.78 offline → 0.55–0.61). They
   clear the 0.55 floor but the funding head sits *on* it. Not promotion-ready
   until we understand why 1h skews more than 5m/15m.

4. **The MES fleet can barely be judged.** Most live MES rows are unlabeled
   (`unlab` 1025–2777 of ~1100–3000) vs BTC `unlab≈0` — a **labeling-pipeline gap
   specific to MES** — and the two partly-labeled MES 15m heads *both* invert.

5. **ETH heads (incl. the cross-asset D2a probe) have almost no live data** (84 /
   320 rows, none labeled). Too early; they need soak time before RG4 means
   anything.

## Methodology — building models that pass the gate

Offline AUC is **necessary but nowhere near sufficient**: 4 of 17 heads that pass
RG3 either invert on live rows or can't be judged at all. The build-time funnel
that produces gate-passing heads is, in order:

1. **Live feature-capture parity first.** The `mf=False` advisory rows + the MES
   anti-predictive heads both smell like the live feature builder not reproducing
   the training features. Fix parity *before* training more variants, or we just
   manufacture more 0.89→0.32 heads.
2. **Live labeling coverage.** The MES `unlab` gap means RG4 is blind on half the
   fleet. RG4 is only as good as the labeled live rows feeding it.
3. **Prefer timeframes that hold up live** (BTC 5m/15m); treat 1h/MES as
   parity-work-needed, not promote-ready.
4. **Gate on RG4 + net-of-cost economic edge, never on RG3 AUC.**

## Status

Tier-1 read-only research. The keep / watch / fix-skew / kill column is a
**proposal** — actual stage flips are the operator-gated promotion gate. Logged
as `MB-20260626-001` (ml-review backlog) as the umbrella record; the per-head
promotions and the two parity gaps (MES labeling, advisory `mf=False`) are
follow-ups under it.
