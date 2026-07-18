# Model drift remediation — runbook

**When the daily promotion-readiness report proposes a DEMOTE for a live
(`advisory`) model — or any model flags drift — do NOT reflexively demote.**
Understand → fix → verify. A demote is an *interim safety net while the fix
trains*, never the endpoint. (Operator directive, 2026-07-18: "have some sort of
process for how we fix models once they drift instead of just demoting them —
what are we gonna do to fix it and make sure it gets back to where it needs to
be?")

Companion skill: [`.claude/skills/drift-remediation`](../../.claude/skills/drift-remediation/SKILL.md).
Composes with `model-training`, `ml-review`, `diag-data`.

## The core distinction — transient drift vs persistent capability failure

Every trainable manifest is **retrained daily** on fresh data (the training
cycle runs every `ml/configs/*.yaml`). That changes what "drift" means:

- **Transient drift** — a score-distribution drift (KS/PSI) reading on a *single
  daily snapshot*. Because the model is retrained the next morning on fresh
  data, a borderline "significant" verdict very often self-corrects to
  "moderate"/passing on the next cycle. **A drift flag that clears on the next
  day's gate-check is transient → no action.**
- **Persistent capability failure** — a required gate that stays failed *across
  retrains*. The signature one is **`live_regime_discrimination`** (RG4 live AUC
  < 0.55 — the head barely beats random on live data). **Retraining the same
  manifest does NOT fix this** (it's retrained daily and stays failed). This is
  a feature / label / data problem, not a staging problem — and **not** a demote.

The whole point of the runbook: **don't demote a live head on a stale one-day
drift snapshot, and don't expect a retrain to fix a capability gap.**

## Workflow

### 1. Detect
The daily report (`runtime_logs/trainer_mirror/promotion_readiness/<date>/SUMMARY.md`,
from `python -m ml stage-guard`) proposes promote / demote / hold per model. A
**demote proposal for an `advisory` (live) model** is the trigger.

### 2. Triage — pull the CURRENT gates, not yesterday's snapshot
```
python -m ml gate-check <model_id>
python -m ml shadow-drift --model-id <model_id>
```
- **Only drift is failing AND it now passes** — `gate-check` `drift_clean` PASS
  (KS ≤ 0.2, PSI ≤ 0.25) / `shadow-drift` verdict `moderate`|`no_change` →
  **TRANSIENT. No action.** The daily retrain already handled it. Record it and
  stop. Do **not** demote on the stale report snapshot.
- **A required capability gate stays FAILED** (`live_regime_discrimination`,
  `non_degenerate`, `oos_edge`, `cross_run_stability`, `sample_sufficiency`) →
  **PERSISTENT.** Go to Diagnose.

### 3. Diagnose the persistent failure
- **`live_regime_discrimination` fail (AUC ≈ 0.5)** — the head doesn't separate
  regimes live. Check, in order:
  - **Dataset audit** for dead features / degenerate labels —
    `manifest_audit_flagged` rows in `runtime_logs/training_cycle.jsonl`, detail
    in the dataset-audit log. A dead (constant / all-NaN) feature or a degenerate
    (near-constant, or extreme-imbalance-with-no-signal) label starves
    discrimination.
  - **The label definition + base rate** in the manifest — e.g. a 96.4% range /
    3.6% volatile label needs `class_weight`; if the minority signal is too rare
    or mislabeled, *no* model discriminates it (a data problem, not a model one).
  - **Regime-mix shift** — the live regime distribution may have moved off the
    training distribution; recency weighting (`sample_weight.half_life_days`)
    helps but cannot invent signal that isn't in the features.
- **`oos_edge` insufficient_data** — the purged-WF-CV OOS edge wasn't computed.
  Run `gate-check --datasets-root <path>` **on the trainer, in the cycle or
  backgrounded — NOT inline in a diag relay** (it is heavy and drops the SSH
  relay: `client_loop: send disconnect: Broken pipe`).

### 4. Remediate — fix, don't demote
- **Data fix** — repair the dead feature / relabel / rebuild the dataset family, retrain.
- **Feature / label fix** — add discriminating features (cross-asset, funding,
  microstructure), fix the label. **This is forward ML research and ships as a
  NEW manifest version, never an in-place edit of the live one.**
- Train the fix under a **new `model_id` / version** so the **live `advisory`
  artifact is never touched**. It lands at `candidate` / `shadow` (observe-only).

### 5. Verify + promote the validated replacement (operator Tier-3)
- The new version must clear **all required gates** (esp.
  `live_regime_discrimination` ≥ 0.55 + `drift_clean`) across ≥ the soak window.
- `python -m ml gate-check <new_id>` → the go/no-go evidence packet.
- If go: **the operator** runs
  `python -m ml promote-stage <new_id> --new-stage advisory --by <op> --reason <…>`,
  then demotes the old one. Claude prepares the packet and evidence; the operator
  authorizes — `shadow → advisory` is the live switch (Tier-3).

### 6. Interim safety — only if the live head is actively harmful NOW
If a live `advisory` head is doing real damage and **no validated replacement is
ready**, the stopgap is to demote it `advisory → shadow`
(`promote-stage <id> --new-stage shadow …`). This is **fail-permissive** — the
gate reverts to the frozen fallback label and **strands no signal**. It is a
*stopgap while the fix trains*, never the endpoint, and still operator-gated
(Tier-3, order-routing). Prefer leaving a not-actively-harmful head live (the
daily retrain keeps drift in check) while the real fix is built.

## Decision tree (one line)

> demote proposed → `gate-check` now → **drift-only & passing?** → *transient, no
> action* · **capability gate failed?** → *diagnose (audit → label → regime) →
> fix as a new version → gate → operator promotes replacement → retire old* ·
> **live head harmful & no replacement?** → *interim demote (fail-permissive),
> then build the fix*.

## Worked example — `btc-regime-15m-lgbm-v2` (2026-07-18)

The live BTC vol-gate advisory head; every BTC regime cell resolves its ML vol
label into the real-money gate.

- **Report (2026-07-17)** proposed **demote** — drift verdict "significant".
- **Triage (2026-07-18 `gate-check` + `shadow-drift`):** `drift_clean` **PASS**
  (KS 0.191 ≤ 0.2), shadow-drift `moderate` / PSI `no_change`. The 2026-07-18
  daily retrain pulled the drift back under threshold → the drift was
  **TRANSIENT. Demote NOT warranted.**
- **Persistent fail:** `live_regime_discrimination` AUC **0.530** (< 0.55) — the
  head barely beats random live, and it **stays** there across daily retrains, so
  retraining alone does not fix it.
- **Diagnosed root cause (`dataset_audit.jsonl`, `runtime_logs/trainer/`):** the
  **`vol_bucket` feature is 100 % NaN — a dead feature** (`nan_fraction 1.0`,
  quarantined) across the 15m regime manifests. `vol_bucket` is the **primary
  categorical feature** in this *volatility-regime* head's manifest — so the head
  trains with its main vol signal **entirely absent**, which plainly starves live
  regime discrimination. The label is healthy (range/volatile balance fine). This
  is a **dataset-build bug** (`vol_bucket` not populated in the `market_features`
  BTCUSDT 15m v002 build), **not** a model-architecture problem.
- **Verdict:** neither demote nor naive-retrain. The fix is a **Tier-1
  data-pipeline repair** — restore `vol_bucket` population in the 15m
  `market_features` build → retrain → re-check `live_regime_discrimination`. If
  the vol signal comes alive and the AUC clears 0.55, the retrained version is
  the promotion replacement (operator Tier-3). Tracked in
  `MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE`. The daily retrain keeps drift in check
  meanwhile, so the live head stays up (not actively harmful; fail-permissive if
  ever demoted). **This is the model-fixing answer to "what do we do instead of
  demoting" — find the dead feature, fix the data, and the capability comes back.**
