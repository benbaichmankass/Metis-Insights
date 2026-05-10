# Training Center

> **Status:** Canonical (training-center scope). Adopted in **S-AI-WS4**
> (2026-05-10). Updated in **S-AI-WS4-FU** (2026-05-10):
> Predictor abstraction + split strategies + `compare` subcommand.
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
>
> **Companion:** [`model-registry-policy.md`](model-registry-policy.md).

## Purpose

Describes the repo-native training factory: directory layout, the
`TrainingManifest` YAML schema, the trainer / evaluator /
experiment-runner contracts, the `Predictor` abstraction (WS4-FU),
the split strategies (WS4-FU), and the umbrella CLI.

## Directory layout

```
ml/
  datasets/      # WS3 + WS5-A — dataset framework + builders
  predictors/    # WS4-FU — Predictor ABC + concrete predictors
  trainers/      # WS4 + WS5-A — Trainer ABC + concrete trainers
  evaluators/    # WS4 + WS5-A — Evaluator ABC + concrete evaluators
  experiments/   # WS4 — runner + WS4-FU splitters
  registry/      # WS4 — filesystem registry
  promotion/     # WS4 — documented promotion gates
  configs/       # WS4 + WS5-A + WS4-FU — YAML manifests
  manifest.py    # WS4 — TrainingManifest schema
  cli.py + __main__.py  # WS4 + WS4-FU — `python -m ml`
```

## Training manifest

Schema in [`ml/manifest.py`](../../ml/manifest.py)
(`TrainingManifest`). Fields:

| Field | Notes |
|---|---|
| `manifest_version` | Currently `v1`. |
| `model_id` | Unique identity in the registry. |
| `model_family` | Free-form. |
| `trainer` / `trainer_config` | Fully-qualified trainer + its kwargs. |
| `dataset` | `{family, symbol_scope, timeframe, version}`. |
| `evaluator` / `evaluator_config` | Fully-qualified evaluator + its kwargs. Includes split config (see below). |
| `target_deployment_stage` | One of `research_only`..`live_approved`. |
| `notes` | Free-form. |

## Predictor abstraction (WS4-FU)

Each trainer pairs itself with a `Predictor` subclass via
`PREDICTOR_CLASS`:

```python
class ConstantPredictionTrainer(Trainer):
    PREDICTOR_CLASS = ConstantPredictor
    def fit(self, rows, config) -> Mapping: ...
```

Evaluators consume predictions via
`Evaluator._resolve_predictor(state)`:

```python
class RegressionEvaluator(Evaluator):
    def score(self, model_state, rows, config):
        predictor = self._resolve_predictor(model_state)
        for row in rows:
            prediction = predictor.predict(row)
            ...
```

Resolution dispatches via `state['trainer']` qualname →
`trainer_cls.PREDICTOR_CLASS(state)`. Concrete predictors live in
[`ml/predictors/`](../../ml/predictors/):

| Predictor | Pairs with |
|---|---|
| `ConstantPredictor` | `ConstantPredictionTrainer` |
| `PerGroupPredictor` | `PerStrategyWinRateTrainer` (configurable `feature_column`) |

## Split strategies (WS4-FU)

Dispatched from `evaluator_config.split_strategy`. Default
`holdout` matches the WS4 behavior so existing manifests work
unchanged.

| Strategy | Behavior | Config |
|---|---|---|
| `holdout` | Stable suffix split. | `holdout_fraction: float in (0,1)` (default 0.2) |
| `time_aware_holdout` | Sort by `time_column` (default `created_at`), then suffix split. | `holdout_fraction`, `time_column` |
| `walk_forward` | Rolling-origin folds. Returns the LAST fold for single-split mode. Aggregated walk-forward (averaging metrics across folds) is filed as a follow-up. | `n_folds: int >= 2`, `min_train_fraction: float in (0,1)`, `time_column` |

Implementation in
[`ml/experiments/splitters.py`](../../ml/experiments/splitters.py).

## Experiment runner

[`ml/experiments/runner.py::run_experiment(...)`](../../ml/experiments/runner.py).
Steps:

1. Load + validate manifest.
2. Locate dataset under
   `<datasets_root>/<family>/<scope>/<tf>/<version>/data.jsonl`.
3. Split via `splitters.split(rows, evaluator_config)`.
4. Resolve trainer + evaluator via `importlib`.
5. `trainer.fit(...)` → `model_state` (carries `trainer` qualname).
6. `evaluator.score(state, rows, config)` → `metrics` (uses
   `_resolve_predictor` internally).
7. Write artifact triple under
   `<experiments_root>/<model_id>/<run_id>/`:
   `manifest.json`, `model_state.json`, `metrics.json`.
8. Register in registry as `candidate` (default).

## CLI

```
python -m ml build-dataset ...           # passthrough to ml.datasets
python -m ml validate-dataset <path>     # passthrough
python -m ml list-families               # passthrough
python -m ml train <manifest>            # train + evaluate + register
python -m ml promote <id> <new-status>   # operator-gated transition
python -m ml list-models [--status S]    # registry enumeration
python -m ml list-trainers               # introspection
python -m ml list-evaluators             # introspection
python -m ml compare <id-a> <id-b>       # WS4-FU side-by-side metric diff
```

The `compare` subcommand surfaces shared-metric deltas (`b - a`)
plus per-side-only metric lists, all as JSON for automation.

## End-to-end demo

```
python -m ml.datasets build trade_outcomes \
  --output-dir ./datasets-out --version v001 \
  --source trade_journal.db -- db_path=/path/to/trade_journal.db

python -m ml train ml/configs/baseline-trade-outcome-winrate.yaml \
  --datasets-root ./datasets-out
python -m ml train ml/configs/baseline-trade-outcome-global.yaml \
  --datasets-root ./datasets-out

python -m ml compare \
  trade-outcome-winrate-baseline-v0 \
  trade-outcome-global-baseline-v0
```

## Out of scope (deferred)

- Aggregated walk-forward (metrics averaged across folds).
- Per-strategy detail metrics artifact alongside scalar metrics.
- Registry concurrent-writer locking.
- `python -m ml.datasets publish` HF subcommand.

## Update rule

Review this doc in the same PR as any change to the manifest
schema, trainer / evaluator / predictor contracts, runner pipeline,
split strategies, registry status state machine / promotion gates,
or the CLI surface.
