# BTC vol-regime head robustness — offline-vs-live diagnosis (2026-07-18)

**Question (operator, B-robustness thread):** the live BTC vol-gate advisory head
`btc-regime-15m-lgbm-v2` fails live regime-discrimination (RG4 AUC 0.530 < 0.55).
*"What do we need to do to make this model more robust so we don't run into the
degradation issues?"* — i.e. is this **distribution shift** (offline strong, live
weak → fix with regime-shift-resistant features + live-parity validation) or a
**signal/label problem** (offline also weak → the label itself is unlearnable
live)?

**Short answer: distribution-shift family, and the robust replacement already
exists in shadow.** A same-symbol, same-timeframe variant
(`btc-regime-15m-lgbm-fc-pcv-v1`) already **passes** live regime-discrimination
at **0.614** with clean drift. So the live signal is real; the live head's
feature/validation recipe is what fails to transfer. The remediation is to
**mature + promote the fc-pcv variant** (the drift-remediation-runbook happy
path), not to invent new features from scratch.

Companion: [`docs/runbooks/model-drift-remediation.md`](../runbooks/model-drift-remediation.md)
· backlog `MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE` · readiness pass
[`M25-readiness-assessment-2026-07-18.md`](./M25-readiness-assessment-2026-07-18.md).

## Evidence

`python -m ml gate-check <id>` on the trainer mirror, 2026-07-18 (issue #6842).
"live RG4" = `live_regime_discrimination` AUC (required gate, floor 0.55).

| Head | Stage | TF | offline min per-class F1 (prec_lift) | eval n | cross-run std(macro_f1) | **live RG4 AUC** | drift KS | RG4 gate |
|---|---|---|---|---|---|---|---|---|
| `btc-regime-15m-lgbm-v2` | **advisory (LIVE)** | 15m | 0.212 (4.87) | 35 054 | 0.0014 | **0.530** | 0.173 ✅ | ❌ FAIL |
| `btc-regime-15m-lgbm-yz-v1` | shadow | 15m | 0.242 (5.67) | 35 054 | 0.0018 | **0.463** | 0.290 ❌ | ❌ FAIL |
| `btc-regime-15m-lgbm-fc-pcv-v1` | shadow | 15m | 0.254 (5.05) | 87 636 | 0.0000 | **0.614** | 0.072 ✅ | ✅ **PASS** |
| `btc-regime-1h-lgbm-v2` | shadow | 1h | 0.481 | 8 760 | 0.0023 | **0.546** | 0.114 ✅ | ❌ FAIL (near) |
| `btc-regime-5m-lgbm-v2` | shadow | 5m | 0.117 (20.29) | 105 173 | 0.0027 | **0.610** | 0.180 ✅ | ✅ **PASS** |

All five pass `non_degenerate` (via the imbalance-aware alt), `sample_sufficiency`,
`cross_run_stability`, and `shadow_soak`. `oos_edge` / `beats_baseline` are
`insufficient_data` for **all** of them (no purged-WF-CV OOS edge computed — that
needs `gate-check --datasets-root` run on the trainer). So the **live RG4 AUC is
the discriminating gate** across this fleet.

## Interpretation

**1. It is NOT a dead label — the live signal exists.** Two BTC vol-regime heads
clear the live bar decisively on the same live data: **fc-pcv 0.614** and
**5m-v2 0.610**. A regime axis that were genuinely unlearnable live could not
produce a 0.61 live AUC. So this is not the "no signal live / relabel or give up"
branch.

**2. It IS a feature-set + validation-methodology (distribution-shift) problem.**
The live head (`v2`) and the passing head (`fc-pcv`) are the **same symbol and
timeframe (BTC 15m)**; they differ in the feature recipe (`fc` = decision-time
quantile-forecast features from `forecast_live`) and validation (purged CV,
`-pcv`). Same target, same live data, opposite live outcome (0.530 vs 0.614). The
`v2` recipe overfits to the train distribution and does not transfer; the fc +
purged-CV recipe transfers. That is textbook distribution-shift, and the lever the
operator named — *regime-shift-resistant features + live-parity validation* — is
exactly what fc-pcv embodies.

**3. Offline headline metrics do NOT predict live discrimination here** — a
caution for the whole fleet. fc-pcv's offline `min per-class F1` (0.254) is barely
above the live-FAILING v2 (0.212), yet its live AUC is far better (0.614 vs
0.530). The 5m head has the *worst* offline min-F1 (0.117) but PASSES live (0.610).
Ranking candidates on the offline macro/F1 floor would have picked the wrong head.
**The live RG4 AUC is the metric that matters** — which is exactly why the gate
carries it as the required live signal and the offline floors are only
non-degeneracy guards.

**4. Dead ends and partials (ruled out / deferred):**
- `yz-v1` (Yang-Zhang vol features): live AUC **0.463 — below random** + drift
  FAIL. **Ruled OUT.** The YZ feature block does not help live discrimination.
- `1h-v2`: strongest offline (min-F1 0.481) but live **0.546**, a near-miss under
  0.55. The coarser timeframe buys offline stability but doesn't clear live alone.
  Ensemble candidate, not a solo 15m replacement.
- `5m-v2`: live **0.610** PASS, but a 5m vol label is noisier / higher-churn as a
  gate verdict. Useful as an ensemble member / cross-check, not the primary 15m
  replacement.

## Proposed experiment (autonomous to shadow; promotion is Tier-3)

**Primary — mature `fc-pcv` into a promotable replacement for `v2`** (the
drift-remediation "fix as a new version → operator promotes replacement → retire
old" path):
1. Run `python -m ml gate-check btc-regime-15m-lgbm-fc-pcv-v1 --datasets-root
   <path>` **on the trainer** (heavy — in the cycle or backgrounded, never inline
   in a diag relay) to populate `oos_edge` — the one missing required gate.
2. Continue its soak to ≥ the live v2's window (14.9d → ≥ 20.9d) and confirm live
   RG4 holds ≥ 0.55 + drift stays clean across it.
3. **Confirm live-feature parity for the `fc` inputs** — the head's forecast
   features must be computed on the live vol-verdict scoring path
   (`ml_vol_regime_for_symbol`), not only in the training dataset, or the live
   score won't match the eval. This is the load-bearing pre-promotion check.
4. If all required gates clear → prepare the **Tier-3 promote packet**
   (`fc-pcv → advisory`, then demote `v2 → shadow`). Claude prepares the evidence;
   the operator authorizes the live switch.

**Secondary — robustness hardening / ensemble (shadow/eval only):**
- Build ONE new variant = the fc-pcv recipe **+** the vol_bucket audit fix (#6841)
  **+** recency sample-weighting (`sample_weight.half_life_days`), and evaluate a
  **2-head ensemble vol-verdict** (fc-pcv + 5m-v2, both live-PASS) as a more
  regime-shift-robust combined label. Ensembling the vol verdict changes
  `ml_vol_regime_for_symbol` (order-routing) → **Tier-3 to route**; stays
  shadow/eval until the operator promotes.
- **Generalize the win:** adopt purged-CV + fc features as the standing template
  for the ETH/SOL 15m heads, whose vol-gate go-live is already blocked on live
  discrimination (`MB-20260628-VOLGATE-GOLIVE`).

## Meanwhile — the live head stays up

`v2` is fail-permissive if ever demoted (the gate reverts to the frozen
`vol_detector` label and strands no signal) and the daily retrain keeps its drift
clean (KS 0.173 ≤ 0.2). It is not actively harmful, so per the drift-remediation
runbook it **stays live** while the fc-pcv replacement is matured — a demote is
the interim safety net, never the endpoint, and it is not warranted here.
