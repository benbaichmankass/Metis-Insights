# Sprint Log: S-MLOPT-S3

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
- Primary goal: An Optuna HPO harness that tunes the LightGBM manifests over the
  **purged WF-CV folds** (S-MLOPT-S1) — so hyperparameter search is scored on a
  leakage-free, honest estimate, not the optimistic holdout — and emits a
  best-params proposal.
- Secondary goals: optional class-weight search for the imbalanced trade-outcome
  models (which ship with none → `f1=0`); a real OOS-lift demo on ≥1 model.

## Tier
- Tier 1.
- Justification: trainer-side tooling + tests only. The harness emits a
  `proposed_trainer_config` (a proposal); adopting tuned params in a manifest is
  Tier-3 (operator-gated). No `src/runtime/` / order-path / live file touched.

## Starting Context
- M14 Session 0.3, builds on S-MLOPT-S1 (purged WF-CV) + S-MLOPT-S2 (sample
  weighting). The no-leakage guardrail (HPO must run on purged folds) is the
  reason Session 0.1 came first.
- Optuna is **not** in the environment/CI; lazy-imported, tests skip it, and the
  trainer-VM run installs it into the venv.

## Files and Systems Inspected
- `ml/experiments/{splitters,runner}.py` (`iter_folds`, `_aggregate_fold_metrics`,
  `_load_jsonl`), `ml/trainers/lightgbm_*.py` (`lgbm_params`/`n_iter`/`class_weight`
  flow), `ml/manifest.py`, `docs/ml/optimization-roadmap.md` Session 0.3.

## Work Completed
- `scripts/ml/hpo_sweep.py` (new): Optuna TPE + MedianPruner over the
  `purged_walk_forward` folds (manifest `split_strategy` is forced to purged WF-CV).
  Searches `lgbm_params` + `n_iter`; `--tune-class-weight <label>` adds the minority
  class weight. Enqueues the current manifest params as trial 0 → best-vs-baseline on
  the same folds; emits a `proposed_trainer_config` patch (proposal only). The CV
  objective `cv_evaluate(...)` is a **pure function** (no Optuna).
- `tests/ml/test_hpo_sweep.py` (new): tests the pure CV core (pooling over purged
  folds, per-fold report callback, optuna-free import, baseline-param fill) — runs on
  CI without Optuna.
- Docs: `ROADMAP.md` + `optimization-roadmap.md` Session 0.3 + `ai-model-platform.md`
  change log.

## Validation Performed
- Tests run: `tests/ml/test_hpo_sweep.py` → 4 passed; full `tests/ml/`
  (ex. pandas-only datasets) → 316 passed, 1 skipped. `py_compile` clean.
- No-leakage: the harness reuses the S-MLOPT-S1 splitter (whose regression test pins
  that no future-dated row enters any train fold); the CV config is force-set to
  `purged_walk_forward`.
- Real HPO run on the trainer VM: results below.

## HPO results (best vs baseline under purged WF-CV)
<!-- FILLED FROM TRAINER-VM-DIAG -->
_Pending the trainer-relay HPO run; appended on return._

## Documentation Updated
- `ROADMAP.md` S-MLOPT-S3 row; `docs/ml/optimization-roadmap.md` Session 0.3;
  `docs/architecture/ai-model-platform.md` change-log row.

## Risks and Follow-Ups
- Early-stopping *inside the trainer* (valid-fold + `early_stopping_rounds`) is
  approximated by the `n_iter` search dimension for now; a trainer-level early-stop
  knob is a clean follow-up.
- Class-weight search is opt-in per-label; a sweep across all trade-outcome models is
  a follow-up once their datasets are denser (Phase 1).
- Adopting any tuned config is Tier-3 (operator-gated) — proposals only here.

## Next Recommended Sprint
- S-MLOPT-S4 (Session 0.4): promotion gates that compute PASS/FAIL against the
  purged-WF-CV edge — the natural consumer of S1–S3.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched.
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns were stated clearly.
