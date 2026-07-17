# MB-20260701-001 — vol_threshold→base-rate mapping reconcile (2026-07-16)

## Question (gate 1 of the MB-20260701-001 Tier-3 gate chain)

The vt004-pcv first-gate run (`docs/research/MB-20260701-vt004-evidence-2026-07-16.md`)
reported a **14% volatile base rate** for a build parameterized `vol_threshold=0.004`,
which disagreed with the T1.1 track's mapping (`0.004 → 7%`). Before pursuing a denser
operating point for the live BTC-15m vol-gate head, the mapping had to be reconciled:
does a given `vol_threshold` produce a known, reproducible volatile prevalence, and why
did the two tracks disagree?

## Method

- **Code trace** (this repo) of how `dataset.build_params.vol_threshold` reaches the
  volatile label, on both the trainer-VM training path and the gpu-burst pod path.
- **Empirical read** on the trainer VM (issue #6676, `trainer-vm-diag` relay,
  read-only): for every `market_features/BTCUSDT/15m/<version>/` dataset dir — its
  `metadata.json`, its actual `regime_label` prevalence, and the `forward_vol`
  distribution / `frac > {0.005,0.004,0.003}`.

## Finding 1 — the authoritative threshold→base-rate mapping is now pinned

The `forward_vol` distribution is **identical across every version dir** (same
underlying `market_raw` window, 175,272 rows; p50 0.00152, p90 0.00372, p96 0.00526).
The volatile base rate at a threshold is therefore fully determined by that one common
distribution:

| vol_threshold | volatile base rate (frac > threshold) |
|---|---|
| 0.005 | **4.6%** |
| 0.004 | **8.4%** |
| 0.003 | **16.0%** |

This is reproducible on current data and supersedes the drifted per-track figures
(T0.1 sweep 4.6/8.4/11.7; T1.1 TCN 3.6/7.0/14.0 — earlier/narrower windows). **0.004 →
8.4%, not 14%.**

## Finding 2 — the vt004-pcv run trained on 0.003-labeled data (the "0.004" was a no-op)

Per-version actual `regime_label` prevalence (→ the threshold each dir was *really* built at):

| version | volatile % | actually labeled at |
|---|---|---|
| v001 | 50.00% | balanced/median (special) |
| v002 | 4.56% | **0.005** |
| v003 | 16.06% | **0.003** |
| **v004** | **16.06%** | **0.003 — NOT 0.004** |
| v005 | 16.03% | 0.003 |
| v006 | 16.01% | 0.003 |
| v104 | 8.42% | **0.004** |
| v106 | 2.62% | ~0.006 |
| v107 | 1.58% | ~0.007 |
| v513 | 16.06% | 0.003 |
| v514 | 8.42% | **0.004** |
| v515 | 4.60% | 0.005 |
| v520 | 4.60% | 0.005 |
| v903 | 16.04% | 0.003 |
| v905 | 4.60% | 0.005 |
| vfmac003 | 16.04% | 0.003 |

The vt004-pcv manifest (`btc-regime-15m-lgbm-vt004-pcv-v1`) declares `dataset.version: v004`
+ `dataset.build_params.vol_threshold: 0.004`. But **`v004` is a 0.003-labeled dataset**
(16.06% prevalence, matching frac>0.003). The run therefore trained on a 0.003 label; its
result (16% full-set prevalence / 14.05% eval-fold prevalence, f1_volatile **0.438**) is a
**0.003 operating-point measurement** — it lines up with the T1.1 0.003 LightGBM control
(f1_volatile 0.444), not a distinct 0.004 point. **The genuine 0.004 operating point remains
unmeasured under purged CV** (the manifest intended 0.004; the trainer path silently gave 0.003).

## Root causes (two, compounding)

1. **`dataset.build_params` is silently ignored on the trainer-VM training path.**
   `ml/experiments/runner.py::run_experiment` resolves the dataset via
   `manifest.dataset.path_under(root)` = `root/family/symbol/timeframe/version` and reads a
   **pre-built `data.jsonl`**; it never consults `manifest.dataset.build_params`. Only the
   gpu-burst pod path (`scripts/ml/gpu_burst/_remote.py::_market_features_params`) merges
   `build_params` into an on-pod `build-dataset market_features`. So the T1.1 arms (pod) got a
   genuinely 0.004-built dataset; the vt004-pcv run (trainer VM) got whatever the `v004` dir
   already contained. A manifest can declare a `vol_threshold` the training run never applies,
   with **no error and no warning**.

2. **Version strings don't encode the threshold, and metadata.json doesn't record it.**
   The `version` label (`v004`) is opaque — v004/v005/v006/v513/v903/vfmac003 are all 0.003;
   v104/v514 are 0.004; v002/v515/v520/v905 are 0.005. `metadata.json` carries only
   `{version, row_count, notes:""}` — no `build_params`, no `vol_threshold` (`_threshold_fields:[]`).
   So which threshold a dir holds is discoverable only by measuring its label prevalence, and a
   mislabeled dir (a "v004" built at 0.003) goes undetected.

## Consequences / corrections

- The committed vt004 first-gate evidence's **thesis still holds** — the shipped 0.005 head is
  data-starved (f1_volatile ~0.24 at 4.6%) and a denser label separates the classes
  (f1_volatile 0.44 at 16%). But its label is **0.003, not 0.004**; it re-confirms the existing
  0.003 measurement rather than adding a 0.004 point. The vt004 evidence doc + ROADMAP item-5 +
  the ml-review backlog are corrected to say so.
- The genuine 0.004 (v104/v514, 8.4%) and 0.005 (v002/v515/v520, 4.6%) dirs already exist, so the
  operating curve can be pinned under purged CV **without rebuilding features** — train the
  live-faithful mirror against the correctly-identified dirs with class weights matched to each
  dir's *true* base rate (the vt004 manifest's `volatile:11.9` was calibrated for 8.4% but ran on
  16%, a further mismatch to correct).

## Remaining gates before ANY Tier-3 config change (unchanged — live threshold stays 0.005)

1. ~~Reconcile the threshold→base-rate mapping.~~ **DONE (this doc).**
2. Pin the operating curve: live-faithful LightGBM mirror at **genuine** 0.005 (v515), 0.004
   (v104), 0.003 (v513), purged 5-fold CV, class weights matched to 4.6/8.4/16.0%.
3. RG4 live-regime discrimination (`scripts/ml/rg4_targeted.sh`) on a fresh mirror.
4. Vol-gate backtest A/B vs the shipped 0.005 gate (`REGIME_ML_VERDICT_MODE=use`).
5. Operator approval — Tier-3, order-routing-affecting.

## Follow-up logged

The build-path correctness gap (build_params silently ignored by `run_experiment`; version dirs
not encoding the threshold; metadata not recording build params) is logged to the ml-review
backlog as `MB-20260716-BUILDPARAMS-IGNORED` — a manifest declaring `build_params` the trainer
path drops is a silent-misconfiguration footgun that mislabeled this very probe.
