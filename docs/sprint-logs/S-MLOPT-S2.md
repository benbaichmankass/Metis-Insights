# Sprint Log: S-MLOPT-S2

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
- Primary goal: Add opt-in **recency (age-decay)** and **de Prado average-uniqueness**
  sample weighting to the LightGBM trainers, and a **window-length/recency sweep**
  tool, to attack `MB-20260601-001` (widening the BTC regime window to 5y *lowered*
  `f1_volatile` — older history dilutes the recent volatility regime).
- Secondary goals: keep the change opt-in / default-preserving (Tier-1); produce the
  sweep table that picks the `f1_volatile`-maximising window/recency config so a
  Tier-3 manifest-default proposal can follow.

## Tier
- Tier 1.
- Justification: Trainer-side tooling + tests + docs only. The weighting is opt-in via
  `trainer_config.sample_weight`; no `ml/configs/*.yaml` default changed, no
  `src/runtime/` / order-path / live-VM file touched. Adopting a weighted config in a
  manifest is **Tier-3** (operator-gated) and is proposed, not shipped, here.

## Starting Context
- Active roadmap items: M14 ML-Optimization Program, **Session 0.2** (this sprint =
  S-MLOPT-S2). Builds directly on S-MLOPT-S1 (purged WF-CV, PR #2674) — the sweep
  reuses its `purge_and_embargo_indices` primitive for the fixed-holdout gap.
- Prior sprint reference: S-MLOPT-S1; backlog item `MB-20260601-001`.
- Known risks at start: keeping the trainer default path byte-for-byte unchanged;
  average-uniqueness is near-uniform on fixed-horizon bar labels (recency is the active
  lever — documented honestly).

## Repo State Checked
- Branch or commit reviewed: `claude/mlopt-s2-sample-weighting` off `origin/main`
  (`b1b41f8`, which already carries the merged S-MLOPT-S1).
- Deployment state reviewed: training runs on the trainer VM via
  `scripts/ops/run_training_cycle.sh`; datasets under `$REPO_ROOT/datasets-out`.
- Canonical docs reviewed: `docs/ml/optimization-roadmap.md` (Session 0.2 spec),
  `ROADMAP.md` M14 table, `MB-20260601-001`.

## Files and Systems Inspected
- Code files inspected: `ml/trainers/lightgbm_multiclass.py`,
  `ml/trainers/lightgbm_regression.py` (existing `class_weight` hook),
  `ml/experiments/splitters.py` (S-MLOPT-S1 primitive), `ml/experiments/runner.py`,
  `ml/manifest.py`.
- Tests inspected: `tests/ml/test_lightgbm_trainer.py` (skip-if-no-lightgbm pattern).
- Docs inspected: `docs/ml/optimization-roadmap.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

## Work Completed
- `ml/trainers/sample_weights.py` (new): `compute_sample_weights` (recency half-life ×
  avg uniqueness, mean-normalised to 1.0) + reusable `recency_weights` /
  `average_uniqueness_weights` primitives; robust ISO-8601/epoch timestamp parsing;
  fail-loud on a missing/unparseable timestamp when a factor is enabled.
- Both LightGBM trainers: opt-in `trainer_config.sample_weight`
  (`{half_life_days, uniqueness, label_horizon, time_column}`), folded into any existing
  `class_weight`; echoed into `model_state["sample_weight"]`. Knob absent → unchanged.
- `scripts/ml/window_recency_sweep.py` (new): 1y/2y/3y/5y(+decay) sweep vs a FIXED recent
  **purged** holdout (reuses the S-MLOPT-S1 primitive), ranked by `f1_volatile`.
- Tests: `tests/ml/test_sample_weights.py` (helper math, composition, fail-loud) +
  end-to-end recency/uniqueness cases in `tests/ml/test_lightgbm_trainer.py`.
- Docs: `ROADMAP.md` + `docs/ml/optimization-roadmap.md` Session 0.2 status.

## Validation Performed
- Tests run: `tests/ml/test_sample_weights.py` → 14 passed; full `tests/ml/`
  (ex. pandas-only `tests/ml/datasets`) → 312 passed, 1 skipped (the lightgbm trainer
  module — its new sample-weight end-to-end cases run on CI/trainer-VM where lightgbm is
  installed). `py_compile` clean; ruff F401s fixed after the first CI run.
- Manual code verification: smoke-ran `window_recency_sweep.py` locally with the
  constant-baseline trainer on synthetic 4-year data — window slicing (1y/2y/3y) +
  fixed holdout + decay variant all produce the expected `n_train` progression.
- Real-dataset sweep: ran on the trainer VM via `trainer-vm-diag` issue #2677 — results
  below.

## Sweep results (window length / recency vs fixed recent holdout)
Ran on the trainer VM via `trainer-vm-diag` #2677 (real `datasets-out`; fixed
recent 20% holdout, 20-row purge gap, `f1_volatile` target). **`MB-20260601-001`
confirmed: a longer window monotonically hurts `f1_volatile` (5y is the worst
config in every model). Recency decay recovers it** — for 1h it is the outright
best config; for 15m/5m it recovers most of the 5y collapse at 4–5× the data.

| Model | holdout n | 1y | 2y | 3y | 5y | **5y+decay(180d)** | Best |
|---|---|---|---|---|---|---|---|
| `btc-regime-1h-lgbm-v2` | 8 760 | 0.4510 | 0.4588 | 0.4576 | 0.4455 | **0.4601** | **5y+decay** |
| `btc-regime-15m-lgbm-v2` | 35 054 | **0.2023** | 0.1929 | 0.1647 | 0.1433 | 0.1916 | 1y (5y+decay close) |
| `btc-regime-5m-lgbm-v2` | 105 173 | **0.1005** | 0.0948 | 0.0799 | 0.0692 | 0.0963 | 1y (5y+decay close) |

(values = `f1_volatile` on the fixed recent holdout). `macro_f1` tracks the same
ordering — e.g. 1h: 5y=0.609 (worst) → 5y+decay=0.637 (best).

**Verdict / proposal (Tier-3, operator-gated — proposed, not shipped here):**
- **1h:** adopt `sample_weight: {half_life_days: 180}` on the 5y window — strictly
  best `f1_volatile` *and* `macro_f1`, full sample size. Clear win.
- **15m / 5m:** `1y` is narrowly best, but `5y+decay` recovers ~80% of the
  5y→1y gap while keeping 4–5× the data. Recommend a **half-life sweep**
  (e.g. 60/90/180 d) before choosing — a shorter half-life may beat the 1y
  window without throwing away history. Logged as the follow-up.
- Recency decay strictly beats the plain 5y window on `f1_volatile` for **all
  three** models, so the current 5y manifests are dominated either way.

Sweep command + raw per-window `n_train`/`macro_f1`: issue #2677.

## Documentation Updated
- Roadmap updates: `ROADMAP.md` M14 table (S-MLOPT-S2 → in progress).
- Subsystem doc updates: `docs/ml/optimization-roadmap.md` Session 0.2 (shipped + pending).
- (doc-freshness sweep at session end: trainer `sample_weight` knob noted in
  `docs/ml/training-center.md`.)

## Contradictions or Drift Found
- None new. `MB-20260601-001` is the item this sprint's tooling exists to resolve; it
  stays open until the sweep picks a window/recency config (the trainer-VM run + the
  Tier-3 manifest proposal).

## Risks and Follow-Ups
- Remaining technical risks: average-uniqueness on fixed-horizon bar labels is
  near-uniform (a global rescale) — the active lever is recency; uniqueness only bites
  with variable label spans (Phase 1 triple-barrier). The primitive is built ready for it.
- Remaining product decisions (Tier 3): adopting the winning window/recency config as a
  `btc-regime-*-lgbm-v2` manifest default (operator-gated edit).
- Blockers: none.

## Deferred Items
- Tier-3 manifest-default proposal for the regime models (after the sweep verdict).
- S-MLOPT-S3 (Session 0.3): Optuna HPO over purged folds + early stopping + class weights.

## Next Recommended Sprint
- Suggested next sprint: S-MLOPT-S3 (Optuna HPO on purged folds), per the canonical plan.
- Why next: HPO must run on the purged folds (S1) to avoid tuning to leakage; recency
  weighting (S2) is a natural HPO knob to fold in.
- Required verification before starting: review the S-MLOPT-S2 sweep verdict.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched (`docs/TRADE-PIPELINE.md` n/a).
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
