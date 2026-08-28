# Offload inbox — models trained on a runner, on their way to the registry

**This directory is a transport, not a store.** It holds models trained by
`.github/workflows/trainer-offload-train.yml` on a free GitHub runner, committed
here so plain `git` carries them to the trainer VM, where
`scripts/ops/trainer_git_sync.sh` (a 15-minute `git checkout --force -B main
origin/main`) puts them on disk.

```
ml/offload-inbox/
  index.jsonl                       # append-only, one R1-shaped row per drop
  <model_id>/<run_id>/
      model_state.json              # the trained model
      metrics.json
      manifest.json
```

## Nothing here is registered, and nothing here influences a trade

A drop is inert until `scripts/ml/offload/drain_inbox.py` ingests it **on the
trainer** — and that drain ships **unarmed** (`--apply` off,
`deploy/trainer/ict-offload-drain.timer` installed-disabled). When it is armed,
it registers under a **namespaced** id (`<model_id>-offload`) at a **forced
`candidate`** stage, which the shadow factory refuses to load. A runner-trained
model joining the live shadow fleet is **Tier-2** and needs an explicit operator
OK.

## Why the model file is committed rather than fetched

Operator decision, 2026-08-27. Two alternatives were considered and declined:

- **Fetch from the run's GitHub artifact** — artifacts expire at 30–90 days, so
  a registered model would outlive its artifact and become *registered and
  unloadable* on a delay that is harder to notice than an immediate failure.
- **git-lfs** — adds an LFS dependency and a bandwidth quota that can fail a
  fetch in a way plain git cannot, i.e. a new failure mode on the path that
  delivers models to the live shadow fleet.

## Why it does not grow without bound

Publishing is **opt-in per run** (`publish: false` by default). Measured
(trainer-diag #10366, n=5,419): `model_state.json` is p50 **1.34 MB**, max 2.05
MB, and the trainer turns over ~52 runs/day — so committing *every* run would be
~70 MB/day of permanent, unreclaimable history. Only runs someone chooses to
publish land here.

⚠️ **The runner cannot enforce that policy and does not pretend to.** The
registry is trainer-local and untracked (`git ls-files ml/registry-store` → 0
against 96 on-disk entries), so a runner has no way to ask whether a model_id
has an entry. The bound is the opt-in, plus a 25 MB per-artifact refusal in
`emit_bundle.py` that fires *before* the commit, because git history is immutable.

## Pruning

A consumed drop is safe to delete from the working tree — the drain is
idempotent per `(model_id, run_id)` and re-reads `index.jsonl`, so removing a
row's directory means that row simply grades `refused_bad_drop` rather than
re-ingesting. Deleting does **not** reclaim history; that is inherent to the
transport and is why the opt-in exists.
