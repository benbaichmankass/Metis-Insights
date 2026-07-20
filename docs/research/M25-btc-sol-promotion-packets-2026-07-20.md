# M25 — BTC/SOL vol-head Tier-3 promotion packets (DRAFT, 2026-07-20)

**Status: APPROVED (operator, 2026-07-20 ~10:05Z chat: Tier-2 deploy + BOTH Tier-3 promotions — "the promotions are approved too, just ping me when you do it"). Execution is sequenced on serving-fidelity certification; no further approval gate.** Under the
M25 gate reframe (operator-approved 2026-07-19: edge is proven OFFLINE by the
purged walk-forward `oos_edge` gate; the live soak proves serving MECHANICS),
both candidates pass every required gate except `live_parity`, whose current
failure was traced to a **serving-stack skew** (MB-20260720-LIVE-SERVING-PARITY-SKEW:
live scores match NO trainer artifact — all 16 retained runs give the same
~1.2e-2 residual; suspect = unpinned lightgbm version divergence, pin
`lightgbm==4.6.0` staged). **The moment the pin deploys (Tier-2, operator OK
pending) and the scoped gate-check re-runs green, these packets are
decision-ready as written.** Nothing here waits on calendar soak time.

## Packet 1 — BTC vol-gate head swap (Tier-3)

**Proposal:** promote `btc-regime-15m-lgbm-fc-pcv-v1` `shadow → advisory` and
demote `btc-regime-15m-lgbm-v2` `advisory → shadow` in the same action. The
fc-pcv head becomes the per-symbol advisory head that drives the LIVE BTC
vol-gate decision (`REGIME_ML_VERDICT_MODE=use`, per-symbol resolution).

**Evidence (gate-check, reframed gates — relays #7050/#7064):**

| Gate | Status | Value |
|---|---|---|
| oos_edge (required) | PASS | +0.277 macro_f1 over 5 purged WF-CV folds (0.604 vs baseline 0.327) |
| labels_accruing (required) | PASS | 0.98 (1267/1292 live rows labeled) |
| live_parity (required) | **pending re-cert** | fails on the version-skew instrument issue; re-run post-pin |
| shadow_soak | PASS | 16.8d |
| drift_clean | PASS | KS 0.089 / PSI 0.062 |
| non_degenerate | PASS | imbalance-aware alt (precision_lift 5.05) |
| live_regime_discrimination (advisory) | PASS | RG4 live AUC **0.627** over the soak |

**Why swap (not just promote):** the incumbent `btc-regime-15m-lgbm-v2` is
drift-flagged (MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE); fc-pcv's live RG4
discrimination (0.627) is the stronger live read.

**Honest caveats:**
1. **Frozen training data** (MB-20260720-FCPCV-RETRAIN-NOOP): the candidate is
   trained on data ending ~Jul 1 (pinned v520 dataset; 16 nightly "retrains"
   were byte-identical no-ops). Counterpoint: the 16.8d live track record was
   therefore accrued by ONE unchanging artifact — cleaner soak evidence than a
   nightly-moving model. Operator choice at approval time:
   **(a) promote the frozen artifact as-is** (it owns the track record;
   recommended — the reframe judges mechanics live + edge offline, both of
   which this artifact has), or **(b) refresh v520 → retrain → re-run
   mechanics gates** (days, and the retrained artifact has no live rows yet).
2. The incumbent v2 trains on fresh v002 nightly — the swap trades data
   recency for measured live discrimination + drift-cleanliness.

**Rollback:** demote fc-pcv → shadow + re-promote v2 → advisory (one
`promote-stage` action each), or `REGIME_ML_VERDICT_MODE=shadow` (env flip,
no redeploy) to fall back to the frozen vol_detector label entirely.

## Packet 2 — SOL advisory head (Tier-3)

**Proposal:** promote `sol-regime-15m-lgbm-fc-pcv-v1` `shadow → advisory`;
demote `sol-regime-15m-lgbm-v1` `shadow → candidate` (retire from the soak).

**Evidence:** oos_edge +0.245 (0.550 vs 0.305, 5 purged folds) PASS ·
labels_accruing 0.90 PASS · soak 13.8d · drift clean (KS 0.099/PSI 0.023) ·
non_degenerate 0.396 PASS · advisory RG4 live AUC 0.545–0.565 (marginal,
advisory-only under the reframe) · live_parity pending the same re-cert.

**sol-v1 demote rationale:** live RG4 reads anti-predictive-to-coin-flip
(0.44–0.52 across reads) after 22.0d of soak — it does not discriminate the
live regime; keeping it soaking spends compute for no decision value.

**Effect scope:** SOL has NO authored `trend_vol` OFF-cells yet, so this
promotion changes no real-money outcome by itself — it is the prerequisite
(per MB-20260628-VOLGATE-GOLIVE) for authoring SOL cells as a follow-up
Tier-3. Same frozen-dataset caveat as BTC (v530 pinned, ~Jul 6).

## Execution sequence (once operator approves)

1. Merge PR #7082 (parity scoping + lightgbm pin + skip guard) — blocked only
   on GitHub's Actions event outage recovering.
2. Tier-2 (operator OK'd separately): install pinned lightgbm on the LIVE
   venv + trainer venv, restart `ict-trader-live`.
3. Re-run scoped `gate-check` on both candidates from the trainer — expect
   `live_parity` PASS on artifact-fresh rows.
4. On green: operator says "approved" → `promote-stage` actions per the
   packets → first-decision health check (verify the swapped BTC head's first
   live vol verdicts + `regime_ml_vol_shadow` agreement rows) → ping results.
