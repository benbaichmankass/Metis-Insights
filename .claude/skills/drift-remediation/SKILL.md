---
name: drift-remediation
description: The standing process for FIXING a drifted / degrading ML model instead of reflexively demoting it. Use when the daily promotion-readiness report proposes a DEMOTE for a live (advisory) model, when shadow-drift or gate-check flags drift, or when the operator asks "how do we fix this model / get it back to where it needs to be" rather than just turning it off. Distinguishes transient drift (self-corrects on the daily retrain → no action) from persistent capability failure (needs a feature/label fix shipped as a new version → operator promotes the validated replacement). Demote is interim safety only, never the endpoint. Composes with model-training, ml-review, diag-data. NOT for promoting a model past shadow (that's the Tier-3 operator gate) and NOT for routine training (model-training).
---

# /drift-remediation — fix a drifted model, don't just demote it

Full runbook: [`docs/runbooks/model-drift-remediation.md`](../../docs/runbooks/model-drift-remediation.md).
This skill is the invokable entry; the runbook carries the detail + the worked
example. **Promotion/demotion of a live head is Tier-3 (operator-gated)** — this
skill prepares evidence and builds the fix; the operator authorizes the live
switch.

## The one rule

A demote proposal from the daily report is a **trigger to diagnose, not an
instruction to demote.** Trainable manifests are retrained daily, so drift often
self-heals; and a capability gap never heals from a retrain. So:

1. **Triage on the CURRENT gates, not yesterday's snapshot.**
   ```
   python -m ml gate-check <model_id>
   python -m ml shadow-drift --model-id <model_id>
   ```
   - Drift-only **and** `drift_clean` now PASSES (KS ≤ 0.2, PSI ≤ 0.25) →
     **transient, NO ACTION** (the daily retrain handled it; do not demote on the
     stale report snapshot).
   - A required capability gate stays FAILED (`live_regime_discrimination`,
     `non_degenerate`, `oos_edge`, `cross_run_stability`, `sample_sufficiency`) →
     **persistent → diagnose.**

2. **Diagnose the persistent failure** (usually `live_regime_discrimination`
   AUC ≈ 0.5): dataset audit for dead features / degenerate label
   (`manifest_audit_flagged` in `training_cycle.jsonl`) → the label base rate →
   regime-mix shift. (`oos_edge` needs `gate-check --datasets-root` run in the
   cycle/backgrounded — heavy, drops an inline relay.)

3. **Remediate as a NEW version** — data / feature / label fix trained under a
   new `model_id`, so the **live advisory artifact is never touched**; it lands
   `candidate`/`shadow`.

4. **Verify + hand off** — the new version must clear all required gates across
   the soak window; `gate-check <new_id>` → the go/no-go packet → **the operator**
   `promote-stage <new_id> --new-stage advisory`, then retire the old one.

5. **Interim demote — only if the live head is actively harmful now and no
   replacement is ready.** `promote-stage <id> --new-stage shadow` is
   fail-permissive (reverts to the frozen label, strands nothing). A stopgap
   while the fix trains, never the endpoint. Still Tier-3.

## Report

Record the triage verdict (transient vs persistent), the diagnosed root cause,
and the remediation version + its gate status in the ml-review backlog. Never
silently demote or silently promote — both are Tier-3 operator calls.
