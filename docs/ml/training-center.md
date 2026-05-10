# Training Center

> **Status:** Canonical (training-center scope). Adopted in **S-AI-WS4**
> (2026-05-10).
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
> (AI-scope canonical) and
> [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md)
> (system-wide).
>
> **Companion:** [`model-registry-policy.md`](model-registry-policy.md)
> covers the registry state machine + promotion rules.

## Purpose

Describes the repo-native training factory: directory layout, the
`TrainingManifest` YAML schema, the trainer / evaluator / experiment
runner contracts, and the umbrella CLI.

## Directory layout

```
ml/
  datasets/                  # WS3 — dataset framework + builders
  trainers/                  # WS4 — Trainer ABC + concrete baselines
  evaluators/                # WS4 — Evaluator ABC + concrete metrics
  experiments/               # WS4 — runner that ties the pipeline together
  registry/                  # WS4 — filesystem model registry
  promotion/                 # WS4 — documented promotion gates
  configs/                   # WS4 — YAML manifests (committed examples + per-run files)
  manifest.py                # WS4 — TrainingManifest schema
  cli.py + __main__.py       # WS4 — `python -m ml`
```

The legacy `ml/config/` and `ml/src/` trees are untouched (vestigial
from S-004/S-005/S-006); WS4 does not migrate them. They are flagged
as a Known Gap in the AI-platform doc.

## Training manifest

Schema at [`ml/manifest.py`](../../ml/manifest.py)
(`TrainingManifest`). Stored as YAML; loaded into a frozen dataclass
with invariant checks at construction time.

```yaml
manifest_version: v1
model_id: backtest-pnl-mean-baseline-v0
model_family: regression_baseline
trainer: ml.trainers.constant_baseline.ConstantPredictionTrainer
trainer_config:
  target_column: total_pnl_pct
dataset:
  family: backtest_results
  symbol_scope: all
  timeframe: all
  version: v001
evaluator: ml.evaluators.regression.RegressionEvaluator
evaluator_config:
  target_column: total_pnl_pct
  metrics: [mse, mae]
  holdout_fraction: 0.2
target_deployment_stage: research_only
notes: |
  Smallest possible baseline; demo of the WS4 round-trip.
```

### Mandatory fields

| Field | Notes |
|---|---|
| `manifest_version` | Currently `v1`. |
| `model_id` | Unique identity in the registry. |
| `model_family` | Free-form; informational. |
| `trainer` | Fully-qualified Python callable resolvable via `importlib.import_module(...).<attr>`. |
| `trainer_config` | Mapping passed to `Trainer.fit(rows, config)`. |
| `dataset` | `{family, symbol_scope, timeframe, version}` referencing a WS3 dataset artifact. |
| `evaluator` | Fully-qualified Python callable. |
| `evaluator_config` | Mapping passed to `Evaluator.score(state, rows, config)`. |
| `target_deployment_stage` | One of `research_only`, `candidate`, `backtest_approved`, `shadow`, `advisory`, `limited_live`, `live_approved`. |
| `notes` | Free-form. |

## Trainer / Evaluator contracts

### Trainer

```python
class Trainer(ABC):
    @abstractmethod
    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Return a JSON-serialisable model state dict."""
```

### Evaluator

```python
class Evaluator(ABC):
    @abstractmethod
    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        """Return decision-useful metrics."""
```

### Pairing

Each evaluator may assume a specific trainer's state shape. The
manifest is the contract: pairing a trainer with an incompatible
evaluator is a manifest authoring error and surfaces at
`Evaluator.score` time. WS5 will expand the trainer / evaluator
family; a general predict() interface is filed as a follow-up.

## Experiment runner

[`ml/experiments/runner.py::run_experiment(...)`](../../ml/experiments/runner.py)
is the orchestrator. Steps:

1. Load and validate the manifest.
2. Locate the dataset under
   `<datasets_root>/<family>/<scope>/<tf>/<version>/data.jsonl`.
3. Holdout-split (last `evaluator_config.holdout_fraction` of rows
   = test; order-preserving for backtest_results).
4. Resolve trainer + evaluator callables via `importlib`.
5. `Trainer.fit(train_rows, trainer_config)` → `model_state`.
6. `Evaluator.score(model_state, eval_rows, evaluator_config)` → `metrics`.
7. Write the artifact triple under
   `<experiments_root>/<model_id>/<run_id>/`:
   - `manifest.json`
   - `model_state.json`
   - `metrics.json`
8. (default) Register in the model registry as `candidate`. Disable
   with `register=False`.

Each artifact triple is tied to a specific `code_revision`
(`git rev-parse HEAD` by default; overridable). The registry entry
holds the manifest snapshot, the model state path, the metrics, and
the code revision — enough lineage to reproduce later.

## CLI

Umbrella entry point: `python -m ml`. See
[`ml/cli.py`](../../ml/cli.py).

| Subcommand | Purpose |
|---|---|
| `build-dataset ...` | Passthrough to `python -m ml.datasets build` (WS3). |
| `validate-dataset <path>` | Passthrough to `python -m ml.datasets validate`. |
| `list-families` | Passthrough to `python -m ml.datasets list-families`. |
| `train <manifest>` | Run the orchestrator end to end. |
| `promote <model_id> <new_status>` | State transition with operator gate. Requires `--by` and `--reason`; transitions with documented gates require `--gates-acknowledged`. |
| `list-models [--status S]` | Enumerate registry entries. |
| `list-trainers` / `list-evaluators` | Introspection helpers. |

### End-to-end demo

```
python -m ml.datasets build backtest_results \
  --output-dir ./datasets-out --version v001 \
  --source trade_journal.db -- db_path=/path/to/trade_journal.db

python -m ml train ml/configs/baseline-backtest-mean.yaml \
  --datasets-root ./datasets-out

python -m ml list-models --status candidate
```

The last step shows the freshly-registered candidate. Promotion is a
separate explicit step — see
[`model-registry-policy.md`](model-registry-policy.md).

## Out of scope (deferred)

- Walk-forward / time-aware split strategies (current splitter is
  a stable holdout suffix). Filed for a follow-up.
- General `predict()` interface decoupling trainer state from
  evaluator. Filed for a follow-up.
- A `compare` subcommand for side-by-side metric diffs across two
  registry entries. Filed for a follow-up.
- Heavy training jobs (the current trainer is a constant baseline).
  Real model families come in WS5.

## Update rule

This doc must be reviewed in the same PR as any change to the
manifest schema, the runner pipeline, the CLI surface, or the
directory layout above.
