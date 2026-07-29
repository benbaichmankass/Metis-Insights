# Trainer compute-ceiling relief — free-runner offload first, paid deferred (2026-07-29)

> **Type:** Tier-1 analysis + implementation (rec #3 of `roadmap-toolbox-assessment-2026-07-29.md`).
> **Corrected 2026-07-29** after an operator challenge: the original draft led
> with paid compute; that over-reached. **A free GitHub-hosted runner already has
> more RAM and cores than the trainer**, so the fix is a $0 offload, not a budget
> raise. Paid compute is a deferred fallback, not the recommendation.

## 1. The constraint, precisely

The trainer is **1 OCPU / 6 GB Ampere**, and the Always-Free Ampere pool is **full
(4/4 OCPU)** — no bigger *free* box is possible without shrinking live/gateway. Two
bottlenecks bite:

- **RAM (6 GB) — the hard wall.** `btc-regime-5m-lgbm-flow-v1` can't fit even trained
  ALONE — it **wedged the box 18.7h in D-state, swap-thrashing** (2026-07-15/16) and
  is now **OOM-quarantined** (`BL-20260717-TRAINER-SINGLE-MANIFEST-OOM`).
  Promotion-readiness + drift-retrain sweeps OOM too. The mitigations to date are
  *defensive* (5 GB cgroup, `TRAINING_MANIFEST_TIMEOUT_S=1800`, single-manifest
  quarantine, checkpoint/resume) — they contain the OOM, they don't remove it.
- **1 OCPU — the throughput wall.** The ~90-manifest nightly cycle is fully
  serialized behind one core + the enforced heavy-job lock.

## 2. Why the existing tooling already covers most of this

**The key fact:** a standard GitHub-hosted Linux runner is **4 vCPU / 16 GB RAM,
$0, parallel** — **~2.7× the RAM and 4× the cores of the trainer.** So the manifest
that OOMs at 6 GB **very likely just trains on a free runner.** The gap is not
compute availability — it is **plumbing**: the nightly pipeline is architected to run
*on* the trainer VM (its registry, dataset cache, mirror). Moving a heavy manifest
off it needs a **fetch → build → train → publish-back** workflow — which already
exists in *pattern* (`research-{panel,exit-head,symbol-p0}-build.yml` run
training-shaped jobs on runners today).

Two tools we already have, and their real fit:
- **Free GitHub runners** — the right tool for the OOM manifests (16 GB fits them, $0).
  Feasibility verified: the crypto fetch dodges the Bybit-geoblock on US runners via
  **Binance-vision** (`scripts/ops/fetch_backtest_candles.py`, already proven in the
  research-build workflows), and `ml build-dataset market_raw` accepts a plain **`csv`
  adapter** — so `fetch → build_raw(csv) → build_features → ml train` runs entirely
  off-VM.
- **runpod GPU-burst** (RTX 3090 @ $0.22/hr, $10/mo cap, ~$0.07 lifetime) — for
  *GPU-shaped* experiments (TCN/deep nets). The OOM manifests are **LightGBM (CPU)**;
  paying for a GPU to fix a RAM problem is the wrong tool. Keep it as-is; **don't lift
  its cap for this.**

## 3. Recommendation — free-runner offload (no budget raise)

1. **Ship a `trainer-offload-train` workflow** (sibling of the research-build cluster):
   given a manifest, on a 16 GB runner, fetch its candles from Binance-vision → build
   `market_raw`(csv)+`market_features` → `ml train` → upload the trained model +
   report metrics. **This recovers the ability to train + eval the OOM manifests the
   6 GB box literally cannot** — at **$0**. (Implemented alongside this doc.)
2. **Keep runpod for genuine GPU experiments.** No cap change.
3. **Defer paid compute.** Revisit *only* if a manifest OOMs even at **16 GB**, or if
   the *whole* ~90-manifest nightly cycle's serialized runtime becomes a real pain
   (a throughput convenience, not a fix for anything breaking).

## 4. Honest coverage of the offload (v1 vs the tail)

- **v1 covers:** any manifest whose dataset is **public-fetchable** — the crypto
  regime + funding heads (Binance-vision candles + the public Bybit funding/OI REST),
  and the OOM-prone `flow`/sweep jobs run for their **eval metrics** (the thing the
  trainer can't produce today).
- **Two honest tail items (v2, documented, not hand-waved):**
  1. **`btc-regime-5m-lgbm-flow-v1` needs the `market_microstructure` capture** —
     forward-captured on the trainer, **not public**. The offload must pull that
     side-stream from the trainer (mirror-publish it, or fetch as an artifact) before
     it can build the flow head's full-history shard. Until then the offload trains it
     on public data only (flow cols 0 pre-capture — the same windowed-eval caveat as
     the rec #4 A/B).
  2. **Register-back into the live registry.** v1 uploads the trained model + metrics
     as a run artifact; wiring the result back into the trainer's `ml/registry-store`
     + mirror (so it joins the live shadow fleet) is the v2 slice — the research-build
     cluster's `comms/` PAT-auto-merge publish is the pattern to mirror.

## 5. Cost line

- **Free-runner offload: $0.** GitHub-hosted runners are free + unmetered on this
  public repo.
- **Paid fallback (deferred):** if ever needed, an OCI Ampere PAYG resize to 2 OCPU /
  12 GB is ~$28/mo (estimate, firm against OCI's cost estimator) — but **only** if the
  16 GB runner proves insufficient.

## 6. Cross-links
- `docs/claude/vm-resource-management.md` (route stateless CPU work to free runners),
  `research-exit-head-build.yml` (the proven template), `BL-20260717-TRAINER-SINGLE-MANIFEST-OOM`.
