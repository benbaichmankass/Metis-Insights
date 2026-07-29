# Trainer compute-ceiling relief — paid-spot-compute proposal (2026-07-29)

> **Type:** Tier-1 analysis + proposal (rec #3 of `roadmap-toolbox-assessment-2026-07-29.md`).
> Operator steer 2026-07-29: **authorize paid spot compute** — "come back with a
> concrete $/mo + what it buys before anything is provisioned." This doc is that.
> **Provisioning is Tier-2/3** (a new/resized billed VM) — nothing is provisioned
> without an explicit dollar figure + go-ahead.
>
> **Honesty note:** the $/mo figures below are **estimates** from public
> pay-as-you-go rate cards; firm each against the provider's live cost estimator
> at provisioning before committing.

## 1. The constraint, precisely

The trainer is **1 OCPU / 6 GB Ampere** (`158.178.209.121`), and the Always-Free
Ampere pool is **full (4/4 OCPU: trainer 1 + gateway 1 + live 2)** — so a bigger
*free* box would require shrinking the live/gateway boxes (not viable). Two distinct
bottlenecks bite:

- **RAM (6 GB) — the hard wall.** Some manifests can't fit even trained ALONE:
  `btc-regime-5m-lgbm-flow-v1` **wedged the box for 18.7h in D-state, swap-thrashing**
  (2026-07-15/16) and is now **OOM-quarantined** (`BL-20260717-TRAINER-SINGLE-MANIFEST-OOM`).
  Promotion-readiness + drift-retrain sweeps also OOM. This is why the mitigations to
  date are *defensive* (a 5 GB cgroup, `TRAINING_MANIFEST_TIMEOUT_S=1800`, single-manifest
  quarantine, checkpoint/resume) — they contain the OOM, they don't remove it.
- **1 OCPU — the throughput wall.** The ~90-manifest nightly cycle is **fully
  serialized** behind one core + the enforced heavy-job lock. More advisory-head
  throughput (the whole point of the fleet) is gated on this.

**What paid compute does NOT need to fix:** gpu-burst (`runpod` RTX 3090, **$0.22/hr**,
$10/mo cap) is already approved and **barely used (~$0.07 lifetime)** — but it's GPU
and per-experiment; it does nothing for the CPU nightly cycle. Lifting its cap is
cheap but off-target for this constraint.

## 2. Options + concrete $/mo

| # | Option | Est. $/mo | What it buys | Effort |
|---|---|---:|---|---|
| **A** | **Resize the trainer to a paid OCI Ampere PAYG shape — 2 OCPU / 12 GB** | **~$28** | Kills the OOM wall (12 GB fits every current manifest incl. the flow head + sweeps) and doubles cycle throughput. Same tenancy/tooling (`provision-live-vm`/`vm-migration` skill); no new provider. | Med (a VM migration) |
| **B** | **Resize the trainer to 4 OCPU / 24 GB PAYG** | **~$55** | As A, plus real headroom — the ~90-manifest cycle parallelizes across 4 cores; promotion sweeps + drift-retrain stop being special-cased. The durable "stop being one-live-head" fix (rec #3's stated goal). | Med (a VM migration) |
| **C** | **Keep the free trainer; add a small on-demand paid spot VM for the heavy/OOM manifests only** | **~$3–10** | Routes just the OOM-prone manifests (flow head, sweeps) to a short-lived spot box (ARM/x86 spot ~$0.005–0.02/hr × a few hrs/day), results published back to the mirror. Minimal spend; the nightly CPU cycle stays serialized on the free box. | High (new routing + publish-back + a spot-launch/teardown workflow, gpu-burst-style) |
| **D** | **Status-quo + lift gpu-burst usage** | **~$1** | Push GPU-trainable heavy manifests through the existing runpod path within (or slightly above) the $10 cap. Off-target: the LGBM cycle is CPU, so this helps only the few GPU-shaped experiments, not the OOM/serialization. | Low |

Rate basis (estimates): OCI Ampere A1.Flex PAYG ≈ **$0.01/OCPU-hr + $0.0015/GB-hr**
× 730 hr/mo (A: (2×0.01 + 12×0.0015)×730 ≈ $28; B: (4×0.01 + 24×0.0015)×730 ≈ $55).

## 3. Recommendation

**Option A (~$28/mo) is the best value** — it removes the RAM wall entirely (the
concrete, recurring pain: OOM, the 18.7h wedge, the quarantine) and doubles
throughput, on the same tenancy with the existing migration tooling, for the price
of a couple of coffees. **Option B (~$55/mo)** is the choice if you want the fleet to
genuinely parallelize and stop treating heavy jobs as exceptions — the truer fix for
"one live advisory head → many." **Option C** trades money for engineering and keeps
the serialized cycle; worth it only if the monthly bill must stay near zero.

**Pick a monthly figure** (or A/B/C) and I'll: (1) firm the estimate against OCI's
live cost estimator, (2) bring the exact resize/provision plan (a Tier-2/3 VM
migration via the `vm-migration` skill — new billed shape, data-dir + registry carry-
over, cutover, teardown of the free box), and (3) execute on your go-ahead. Nothing
is billed until you approve the figure.

## 4. Cross-links
- Free-runner offload (the $0 complement, already in use for CPU-only stateless jobs)
  is `docs/claude/vm-resource-management.md` — it stays the default for backtests /
  k-folds / the rec #5 regime matrix; this proposal is for the **stateful** nightly
  cycle + OOM manifests that offload can't move.
- Trainer resource protocol + the defensive mitigations: `docs/claude/trainer-resource-protocol.md`,
  `BL-20260717-TRAINER-SINGLE-MANIFEST-OOM`.
