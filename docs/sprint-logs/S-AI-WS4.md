# S-AI-WS4 — AI traders WS4: Training center

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) (master plan); subordinate to [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
**Status:** ✅ COMPLETE

## Goal

Land a repo-native training factory: YAML manifest schema, Trainer
/ Evaluator ABCs, experiments runner, filesystem model registry
with promotion state machine, and an umbrella CLI. Round-trip a
first (trivial) model end to end as the acceptance proof.

## Deliverables

Code (stdlib + pyyaml; pyyaml already a runtime dep):
- New: [`ml/manifest.py`](../../ml/manifest.py) —
  `TrainingManifest` + `DatasetRef` with `__post_init__` invariant
  checks and YAML loader.
- New: [`ml/trainers/`](../../ml/trainers/) — `Trainer` ABC +
  `ConstantPredictionTrainer` (mean-prediction baseline).
- New: [`ml/evaluators/`](../../ml/evaluators/) — `Evaluator`
  ABC + `RegressionEvaluator` (MSE/MAE on the constant-baseline
  state shape).
- New: [`ml/registry/`](../../ml/registry/) — `ModelRegistry`
  + `RegistryEntry` + `StatusEvent` + `_ALLOWED_TRANSITIONS`
  state machine.
- New: [`ml/promotion/`](../../ml/promotion/) — documented
  promotion gates per transition.
- New: [`ml/experiments/`](../../ml/experiments/) — orchestrator
  that loads dataset → splits → trains → evaluates → writes
  artifact triple → registers as candidate.
- New: [`ml/cli.py`](../../ml/cli.py) +
  [`ml/__main__.py`](../../ml/__main__.py) — umbrella CLI with
  `train`, `promote`, `list-models`, `list-trainers`,
  `list-evaluators`, plus `build-dataset` /
  `validate-dataset` / `list-families` passthroughs to the WS3
  dataset CLI.
- New: [`ml/configs/baseline-backtest-mean.yaml`](../../ml/configs/baseline-backtest-mean.yaml)
  — demo manifest.
- New: [`tests/ml/test_training_manifest.py`](../../tests/ml/test_training_manifest.py),
  [`test_model_registry.py`](../../tests/ml/test_model_registry.py),
  [`test_experiments_runner.py`](../../tests/ml/test_experiments_runner.py).

Docs:
- New: [`docs/ml/training-center.md`](../ml/training-center.md) —
  layout, manifest schema, runner pipeline, CLI surface.
- New: [`docs/ml/model-registry-policy.md`](../ml/model-registry-policy.md) —
  status set, transition graph, gates, rollback rules.
- Updated: [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md) —
  layer table now points at WS4 paths; Live audit row added;
  Known Gaps refreshed; Forbidden list extended (no editing past
  StatusEvent entries; no live-approved/champion without
  operator approval); Mermaid diagram annotated; Update Rule
  scoped to training center + registry; Change Log row.
- Updated: [`docs/sprint-plans/ai-traders/ws4-training-center.md`](../sprint-plans/ai-traders/ws4-training-center.md) —
  status → DONE, acceptance check-offs.
- Updated: [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md),
  [`ROADMAP.md`](../../ROADMAP.md) — WS4 → DONE; change-log /
  ledger rows.
- This file: sprint log.

## Acceptance (from WS4 sprint plan)

- [x] Documented training center exists.
- [x] At least one model trains and evaluates via a repeatable
  command path —
  [`tests/ml/test_experiments_runner.py::test_run_experiment_round_trip`](../../tests/ml/test_experiments_runner.py)
  exercises it end-to-end against synthetic dataset rows.
- [x] Model-registry metadata supports promotion-state tracking.
  `RegistryEntry.history` is the append-only `StatusEvent` log;
  `_ALLOWED_TRANSITIONS` enforces the legal graph; tested under
  [`tests/ml/test_model_registry.py`](../../tests/ml/test_model_registry.py).

## Decisions

- **YAML over JSON for manifests.** Consistent with the rest of
  the repo (`config/strategies.yaml`, `config/units.yaml`).
  pyyaml is already a runtime dep.
- **Filesystem registry, not SQLite.** One JSON per `model_id`.
  Easy to diff, grep, and version. SQLite would be overkill for
  the expected entry volume.
- **Trainer↔evaluator coupling, not generic predict().** WS4
  ships the simplest pairing that makes the demo work. The
  generic interface is recorded as a Known Gap and filed.
- **Holdout splitter is suffix-stable.** No shuffle, no time
  awareness. Keeps `backtest_results` deterministic for the WS4
  demo. Walk-forward / time-aware splitters are filed.
- **Promotion gates documented, not enforced beyond CLI guard.**
  `--gates-acknowledged` is a self-attestation by the operator.
  Hard runtime enforcement (e.g. requiring a leakage-test JSON
  in the experiment dir before allowing a `candidate → paper`
  transition) is filed for a follow-up.
- **Registry append-only at the entry level too.** No `delete`
  API. Failed candidates remain as historical record.
- **`ml/configs/`, not `ml/config/`.** Master plan target name.
  Legacy `ml/config/` (S-004 era) is untouched.

## Out of scope (deferred)

- Real (non-baseline) model families (WS5 onwards).
- Walk-forward / time-aware splitters.
- Generic `predict()` interface.
- `compare` CLI subcommand.
- `publish` CLI subcommand for HF.
- Wiring registry entries into the live runtime (WS7).
- Concurrent-writer locking on the registry.

## Hand-off

1. **WS5 — baseline models.** Pick the first specialist baseline
   (recommended: regime classifier, since `market_raw` is a
   prereq and could be the first dataset family after
   `backtest_results` to gain a builder). Build the dataset
   family, write the trainer + evaluator, train + register.
   Sprint plan:
   [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md).
2. **Per-family dataset builders.** Same logic as WS3 hand-off:
   one sprint per remaining family.
3. **Tier 2 follow-up: live-path migration onto WS2 types.**
   Still pending; needs operator-ack.

## Live runtime impact

None. Stdlib + pyyaml additive code; only NEW paths under `ml/`,
`tests/ml/`, `docs/ml/`, `docs/sprint-plans/`, plus updates to
existing canonical / roadmap docs. Operator-hold paths
(`src/runtime/`, `src/units/accounts/`, `src/main.py`,
`config/accounts.yaml`, `deploy/*`) not modified.
