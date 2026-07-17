# Sprint Log: S-ML-VOLGATE-RECONCILE-2026-07-16

## Date Range
2026-07-16 (ML-forward continuation; picks up the `claude/ml-vol-regime-probe-21az61`
thread from S-ML-FORWARD-T13-RANKER-2026-07-16).

## Objective
Execute **gate 1** of the MB-20260701-001 Tier-3 gate chain: reconcile the
`vol_threshold`→base-rate mapping so the BTC-15m vol-regime head's operating
threshold has a known, reproducible volatile prevalence — the blocker the vt004
first-gate evidence flagged (0.004→7% in T1.1 vs 0.004→14% in the vt004-pcv run).
All Tier-1 (research + docs + backlog); nothing touches routing or config.

## Tier
Tier 1 (research reads + docs + backlog/roadmap records). No `config/`, `src/`, or
order-path writes. The live vol-gate threshold stays 0.005; any change remains an
operator-gated Tier-3 proposal downstream.

## Starting Context
MB-20260701-001 was left at POSITIVE FIRST-GATE with an explicit unresolved caveat:
the vt004-pcv "0.004" probe produced a 14% volatile base rate that disagreed with the
T1.1 mapping, so the operating threshold was not pinned. First pickup item per the
kickoff.

## Work Completed

### 1. Root cause traced in code (this repo)
`dataset.build_params` is honored **only** by the gpu-burst pod path
(`scripts/ml/gpu_burst/_remote.py::_market_features_params`). The trainer-VM training
path (`ml/experiments/runner.py::run_experiment`) resolves the dataset via
`manifest.dataset.path_under(root)` = `root/family/symbol/timeframe/version` and reads a
**pre-built `data.jsonl`** — it never consults `build_params`. So a manifest's declared
`vol_threshold` never affects a trainer-VM `python -m ml train` run.

### 2. Empirical confirmation on the trainer VM (issue #6676, read-only relay)
Read every `market_features/BTCUSDT/15m/<version>/` dir's `regime_label` prevalence +
`forward_vol` distribution. **`forward_vol` is identical across all version dirs** (one
common 175k-row window), so the base rate at a threshold is fully determined by it:

- **Authoritative mapping pinned: 0.005→4.6% / 0.004→8.4% / 0.003→16.0%.**
- **The `v004` dir the vt004-pcv manifest points at is 0.003-labeled** (16.06% volatile;
  genuine 0.004 dirs are v104/v514 at 8.42%; 0.005 dirs are v002/v515/v520 at 4.6%).
- So the vt004-pcv run trained on a **0.003 label**, not 0.004 — its f1_volatile 0.44 is
  a 0.003 measurement (≈ the T1.1 0.003 LightGBM control 0.444). The thesis (0.005 is
  data-starved; a denser label separates the classes) **holds**; the specific 0.004 point
  is still **unmeasured** under purged CV.

### 3. Two compounding root causes recorded
(a) `build_params` silently ignored by the trainer train path; (b) version strings don't
encode the threshold and `metadata.json` records only `{version,row_count,notes}` — so a
mislabeled dir (a "v004" built at 0.003) is undetectable without measuring its prevalence.

### 4. Corrections landed
The vt004 evidence doc (correction banner), ROADMAP item-5, and the ml-review backlog
`MB-20260701-001` evidence all corrected to state the run was a 0.003 measurement. New
Tier-1 footgun item `MB-20260716-BUILDPARAMS-IGNORED` opened.

## Validation
- Code claims verified by reading `ml/experiments/runner.py`, `ml/manifest.py`,
  `ml/datasets/cli.py`, `scripts/ml/gpu_burst/_remote.py`, `scripts/ops/build_trainer_datasets.sh`.
- Empirical mapping verified from the trainer's actual dataset dirs (#6676), not inferred.
- `docs/claude/ml-review-backlog.json` re-validated (`json.load`) after the edit — parses,
  73 items, MB-20260701-001 evidence appended + MB-20260716-BUILDPARAMS-IGNORED added.

## Docs Updated
- NEW `docs/research/MB-20260701-mapping-reconcile-2026-07-16.md` — the reconcile (mapping +
  version→threshold table + two root causes).
- `docs/research/MB-20260701-vt004-evidence-2026-07-16.md` — correction banner.
- `ROADMAP.md` item-5 — mapping reconciled; the "0.004" was actually 0.003; gates re-stated.
- `docs/claude/ml-review-backlog.json` — MB-20260701-001 evidence + new footgun item.
- This sprint log.

## Tier-3 Proposals
None. The live vol-gate threshold stays 0.005. Any operating-point change remains gated on
the pinned operating curve (next), RG4, the vol-gate backtest A/B, and operator approval.

## Follow-ups / Next
- **Gate 2 (in progress):** pin the operating curve — train the live-faithful LightGBM mirror
  at **genuine** 0.005 (v515), 0.004 (v104), 0.003 (v513) under purged 5-fold CV with class
  weights matched to 4.6/8.4/16.0%, so the 0.004 point is measured for real. No feature rebuild
  needed (the genuine dirs exist).
- Then RG4 (`scripts/ml/rg4_targeted.sh`) → vol-gate backtest A/B → operator.
- `MB-20260716-BUILDPARAMS-IGNORED`: land a Tier-1 fix (persist effective build params into
  `metadata.json` + validate manifest build_params vs the resolved dir).
- fc→advisory powered re-read still due ~2026-07-20 (`MB-20260705-FC-ADVISORY-READINESS`).
