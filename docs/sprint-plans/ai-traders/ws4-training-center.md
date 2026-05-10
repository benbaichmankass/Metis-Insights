# WS4 — Training center

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Sprint id:** **S-AI-WS4** (started + closed 2026-05-10)
**Status:** ✅ DONE

## Objective

Repo-native training center that behaves like a repeatable factory
for training, evaluation, registration, and promotion.

## Target structure (delivered)

```
ml/
  datasets/      # ✅ S-AI-WS3
  trainers/      # ✅ S-AI-WS4
  evaluators/    # ✅ S-AI-WS4
  experiments/   # ✅ S-AI-WS4
  registry/      # ✅ S-AI-WS4
  promotion/     # ✅ S-AI-WS4
  configs/       # ✅ S-AI-WS4 (example manifest)
  manifest.py    # ✅ S-AI-WS4
  cli.py         # ✅ S-AI-WS4
  __main__.py    # ✅ S-AI-WS4
  features/      # ⏳ follow-up (per-feature WS5 prereq)
  labels/        # ⏳ follow-up
  reports/       # ⏳ follow-up
```

## Tasks (delivered)

1. [x] Training-manifest format (YAML): `manifest_version`,
   `model_id`, `model_family`, `trainer`, `trainer_config`,
   `dataset {family,scope,timeframe,version}`, `evaluator`,
   `evaluator_config`, `target_deployment_stage`, `notes`. See
   [`ml/manifest.py`](../../../ml/manifest.py) and
   [`docs/ml/training-center.md`](../../ml/training-center.md).
2. [x] CLI / Make entry points: `python -m ml {build-dataset,
   validate-dataset, list-families, train, promote, list-models,
   list-trainers, list-evaluators}`. `build-dataset` /
   `validate-dataset` / `list-families` are passthroughs to the
   WS3 dataset CLI.
3. [x] Experiment-tracking metadata. Each run writes
   `<experiments_root>/<model_id>/<run_id>/{manifest, model_state,
   metrics}.json` plus a registry entry tying it to a
   `code_revision`.
4. [x] Model registry with status categories. Statuses:
   `candidate`, `paper`, `advisory`, `live-approved`, `champion`.
   Transitions enforced by `ModelRegistry.promote(...)`. State
   machine + transition log documented in
   [`docs/ml/model-registry-policy.md`](../../ml/model-registry-policy.md).
5. [x] Promotion checklist. Documented gates per transition in
   [`ml/promotion/__init__.py`](../../../ml/promotion/__init__.py);
   the CLI `promote` subcommand surfaces them and refuses to act
   without `--gates-acknowledged` when gates are documented.
6. [x] All training artifacts tied to a specific dataset version +
   code revision. `RegistryEntry` carries the manifest snapshot
   (which holds the dataset ref) and `code_revision`.

## Acceptance

- [x] Documented training center exists in the repo.
- [x] At least one model trains and evaluates via a repeatable
  command path. `ConstantPredictionTrainer` +
  `RegressionEvaluator` against the `backtest_results` family,
  driven from
  [`ml/configs/baseline-backtest-mean.yaml`](../../../ml/configs/baseline-backtest-mean.yaml)
  via `python -m ml train ...`. Tested end-to-end in
  [`tests/ml/test_experiments_runner.py`](../../../tests/ml/test_experiments_runner.py).
- [x] Model-registry metadata supports promotion-state tracking.
  `RegistryEntry.history` is the append-only `StatusEvent` log.

## Out of scope (deferred)

- Real (non-baseline) model families. WS5 lands the first.
- Walk-forward / time-aware splitters. Current splitter is a
  stable holdout suffix.
- A general `predict()` interface decoupling trainer state from
  evaluator. Currently each evaluator may assume a specific
  trainer's state shape.
- A `compare` subcommand for side-by-side metric diffs across
  registry entries.
- A `python -m ml.datasets publish ...` subcommand wrapping HF.
- Wiring any model output into the live runtime path. WS7 owns
  the runtime hook.

## Risks (mitigated)

- **Trainer↔evaluator coupling makes substitution awkward.**
  Recorded as a Known Gap in the AI-platform doc; a generic
  `predict()` interface is filed for a follow-up.
- **Registry corruption via concurrent writes.** The first version
  is single-writer (operator-driven). Locking is filed for the
  follow-up sprint that wires shadow-mode hooks (WS7).
- **Manifest schema drift.** `manifest_version=v1` is enforced;
  bumping the version is a breaking change and triggers
  matching updates in `docs/ml/training-center.md`.

## Deliverables (this sprint)

Code:
- `ml/manifest.py` (new).
- `ml/trainers/{__init__, base, constant_baseline}.py` (new).
- `ml/evaluators/{__init__, base, regression}.py` (new).
- `ml/registry/{__init__, model_registry}.py` (new).
- `ml/promotion/{__init__, checklist}.py` (new).
- `ml/experiments/{__init__, runner}.py` (new).
- `ml/cli.py` (new) + `ml/__main__.py` (new).
- `ml/configs/baseline-backtest-mean.yaml` (new example).
- `tests/ml/test_{training_manifest, model_registry,
  experiments_runner}.py` (new).

Docs:
- `docs/ml/training-center.md` (new).
- `docs/ml/model-registry-policy.md` (new).
- `docs/architecture/ai-model-platform.md` (updated: layer table
  + Live audit + Known Gaps + Forbidden + Update Rule + Change
  Log + Mermaid annotation).
- This file (status → DONE, acceptance check-offs).
- `docs/AI-TRADERS-ROADMAP.md` (WS4 → DONE; change-log row).
- `ROADMAP.md` (WS4 → DONE; S-AI-WS4 ledger row).
- `docs/sprint-logs/S-AI-WS4.md` (new).
