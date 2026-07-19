"""Experiment runner (WS4 + WS4-FU).

Loads a `TrainingManifest`, reads the dataset produced by WS3,
dispatches to a split strategy via `evaluator_config.split_strategy`
(default `holdout` — stable WS4 behavior), runs trainer + evaluator,
writes the artifact triple under
`<experiments_root>/<model_id>/<runid>/`, and (by default) registers
the result in the model registry as a `candidate`.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..manifest import TrainingManifest
from ..registry.model_registry import ModelRegistry, RegistryEntry
from .splitters import MULTI_FOLD_STRATEGIES, iter_folds
from .splitters import split as split_rows


class EmptyDatasetError(RuntimeError):
    """Raised when a manifest's dataset is not available for training.

    Covers BOTH "the dataset file exists but has 0 rows" (this class) and
    "the dataset file was never built" (the `DatasetMissingError` subclass).
    The CLI maps this whole family to exit code 78 (BSD `EX_CONFIG`) so
    `run_training_cycle.sh` emits a clean `manifest_skipped` instead of
    `manifest_failed`. Rationale: a not-yet-built or orphan manifest (one
    whose dataset the daily build step doesn't produce) must NOT fail the
    whole cycle — that turned `overall_rc=1` on every run (MB-20260606-001).
    Real training failures still raise other exceptions and exit non-78.
    """

    def __init__(self, data_path: Path):
        super().__init__(f"dataset at {data_path} is empty")
        self.data_path = data_path


class DatasetMissingError(EmptyDatasetError):
    """The dataset file does not exist — its build step never produced it
    (e.g. an orphan manifest whose dataset family the daily cycle doesn't
    build). A subclass of `EmptyDatasetError` so it rides the same exit-78
    `manifest_skipped` path: a missing dataset is "data not ready", not a
    training failure. The build step + `dataset_builds.jsonl` remain the
    place where a genuinely-wired dataset that fails to build is surfaced.
    """

    def __init__(self, data_path: Path):
        # Keep the manual-run hint; bypass the parent's "is empty" message.
        RuntimeError.__init__(
            self,
            f"dataset data not found at {data_path}; "
            f"run `python -m ml.datasets build` first",
        )
        self.data_path = data_path


class ManifestDatasetMismatchError(RuntimeError):
    """A manifest declares `dataset.build_params` that disagree with the
    resolved dataset dir's recorded metadata `build_params`.

    The trainer training path resolves a dataset by
    `family/symbol_scope/timeframe/version` and reads a PRE-BUILT `data.jsonl`
    — it does NOT rebuild from `build_params` (only the gpu-burst pod path
    honors them). So a mismatch means the run would silently train on a
    differently-parameterized dataset than the manifest declares — the exact
    footgun that mislabeled the MB-20260701-001 vt004 probe as a 0.004 run when
    its `v004` dir was 0.003-labeled (MB-20260716-BUILDPARAMS-IGNORED). Fail
    loud rather than mislabel.
    """


def _verify_declared_build_params(
    declared: Mapping[str, Any], dataset_dir: Path
) -> None:
    """Guard the MB-20260716-BUILDPARAMS-IGNORED footgun.

    When a manifest declares `dataset.build_params`, verify them against the
    resolved dir's recorded `metadata.json::build_params`. Raise on a genuine
    mismatch; warn (can't verify) when the dir predates the recorded field.
    """
    meta_path = dataset_dir / "metadata.json"
    recorded: Any = None
    if meta_path.is_file():
        try:
            recorded = json.loads(meta_path.read_text(encoding="utf-8")).get(
                "build_params"
            )
        except (OSError, ValueError):
            recorded = None
    if not recorded:
        sys.stderr.write(
            f"WARNING: manifest declares dataset.build_params {dict(declared)} but "
            f"{dataset_dir} records none — the trainer path does NOT apply "
            f"build_params (only gpu-burst does), so this cannot be verified. "
            f"Confirm the version dir was built with these params "
            f"(MB-20260716-BUILDPARAMS-IGNORED).\n"
        )
        return
    mismatched = {
        k: {"declared": declared[k], "recorded": recorded.get(k)}
        for k in declared
        if str(recorded.get(k)) != str(declared[k])
    }
    if mismatched:
        raise ManifestDatasetMismatchError(
            f"manifest dataset.build_params disagree with the recorded metadata at "
            f"{dataset_dir}: {mismatched}. The trainer path reads a pre-built dataset "
            f"by version and does NOT apply build_params — training would use the "
            f"RECORDED params and mislabel the run. Fix the version or rebuild the "
            f"dataset with the declared params (MB-20260716-BUILDPARAMS-IGNORED)."
        )


EMPTY_DATASET_EXIT_CODE = 78


@dataclass(frozen=True)
class ExperimentArtifacts:
    experiment_dir: Path
    manifest_path: Path
    model_state_path: Path
    metrics_path: Path
    metrics: Mapping[str, float]
    # Written only for multi-fold (cross-validation) split strategies; None
    # for the single-split default path.
    cv_folds_path: Path | None = None


# Metric keys that are counts (summed across folds) rather than rates
# (sample-weighted averaged).
def _is_count_metric(key: str) -> bool:
    return key == "n_eval" or key.startswith("support_")


def _aggregate_fold_metrics(
    fold_metrics: list[Mapping[str, float]],
) -> dict[str, float]:
    """Pool per-fold evaluator metrics into one scalar metric set.

    Rate metrics (accuracy, f1, mae, …) are averaged across folds weighted
    by each fold's ``n_eval`` — the pooled estimate, so a small final fold
    can't dominate a large one. Count metrics (``n_eval``, ``support_*``)
    are summed. Adds ``n_folds`` so the registry records how the estimate
    was produced. Keeps the output flat + float-valued so it round-trips
    through the registry unchanged.
    """
    keys: set[str] = set()
    for m in fold_metrics:
        keys.update(m.keys())
    total_w = sum(float(m.get("n_eval", 0.0)) for m in fold_metrics)
    agg: dict[str, float] = {}
    for key in sorted(keys):
        if _is_count_metric(key):
            agg[key] = float(sum(float(m.get(key, 0.0)) for m in fold_metrics))
        elif total_w > 0:
            agg[key] = float(
                sum(
                    float(m.get(key, 0.0)) * float(m.get("n_eval", 0.0))
                    for m in fold_metrics
                )
                / total_w
            )
        else:
            vals = [float(m[key]) for m in fold_metrics if key in m]
            agg[key] = float(sum(vals) / len(vals)) if vals else 0.0
    agg["n_folds"] = float(len(fold_metrics))
    return agg


def _resolve_callable(qualname: str):
    module_name, _, attr = qualname.rpartition(".")
    if not module_name:
        raise ValueError(f"qualname must include module path: {qualname!r}")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _resolve_commit_sha(override: str | None) -> str:
    if override:
        return override
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --- Load-time column projection (BL-20260717-TRAINER-SINGLE-MANIFEST-OOM) ---
# Materializing data.jsonl as list-of-dicts with EVERY column is what blew the
# 5 GB trainer cgroup on the 5m datasets (~500k rows x ~40 cols of boxed Python
# objects ~= 5 GB anon-rss — the btc/sol 5m heads OOM'd ALONE, kernel memcg
# kills at ~5.2 G on 2026-07-19). The trainers/evaluators/splitters only ever
# read the columns the manifest references plus a small fixed set of
# hardcoded/default names — so the loader projects each row down to that set at
# parse time, cutting peak RSS roughly proportionally (~40 -> ~15 cols ~= 3x).
#
# Safety properties:
#   - `_PROJECTION_SAFETY_COLUMNS` carries every column name any trainer /
#     splitter / evaluator accesses OUTSIDE the manifest config (hardcoded
#     reads like `row.get("vol_bucket")` and config-default fallbacks like
#     time_column="created_at").
#   - Every string VALUE anywhere in trainer_config / evaluator_config is
#     treated as a potentially-referenced column (recursive walk), so a
#     manifest-declared column can never be dropped.
#   - Fail-open: if the first row shares NO key with the projection set (an
#     unforeseen dataset shape), projection disables itself and the full rows
#     are loaded — behaviour identical to the pre-fix loader.
#   - `TRAINING_LOAD_ALL_COLUMNS=1` (env) is the zero-touch opt-out.
_PROJECTION_SAFETY_COLUMNS = frozenset({
    # time / split columns (splitters + sample_weight defaults)
    "ts", "created_at", "date",
    # identity / spec columns trainers read directly
    "symbol", "timeframe",
    # hardcoded feature reads + config-default fallbacks
    "vol_bucket", "rolling_log_return_vol", "is_live_trade",
    "seq_window", "values", "strategy_name",
    # default target columns across the trainer fleet
    "regime_label", "r_multiple", "should_hold", "won",
})

_ENV_LOAD_ALL_COLUMNS = "TRAINING_LOAD_ALL_COLUMNS"


def _collect_config_strings(obj: Any, out: set[str]) -> None:
    """Recursively collect every string value in a config mapping/sequence."""
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, Mapping):
        for v in obj.values():
            _collect_config_strings(v, out)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            _collect_config_strings(v, out)


def dataset_projection_columns(manifest: TrainingManifest) -> frozenset[str] | None:
    """Columns worth keeping when loading this manifest's dataset (None = all).

    Union of every string referenced in trainer_config/evaluator_config with
    the hardcoded safety set. Returns None (load everything) when the
    `TRAINING_LOAD_ALL_COLUMNS` env opt-out is set.
    """
    if os.environ.get(_ENV_LOAD_ALL_COLUMNS, "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return None
    refs: set[str] = set()
    _collect_config_strings(manifest.trainer_config, refs)
    _collect_config_strings(manifest.evaluator_config, refs)
    return frozenset(refs) | _PROJECTION_SAFETY_COLUMNS


def _load_jsonl(
    path: Path, keep: frozenset[str] | set[str] | None = None
) -> list[dict[str, Any]]:
    """Load a JSONL dataset, optionally projecting rows to `keep` columns.

    Keys (and short string values, e.g. a repeated "range" label) are interned
    so the ~13 surviving key strings are shared across all rows instead of
    re-allocated per line (json.loads only memoizes keys within one call).
    """
    intern = sys.intern
    rows: list[dict[str, Any]] = []
    project: bool | None = None if keep else False
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            obj = json.loads(line)
            if project is None:
                # First row decides: project only if the keep-set actually
                # intersects the data's columns (fail-open on odd shapes).
                project = bool(keep and (obj.keys() & keep))
            if not project:
                rows.append(obj)
                continue
            proj: dict[str, Any] = {}
            for k, v in obj.items():
                if k in keep:
                    if type(v) is str and len(v) <= 24:
                        v = intern(v)
                    proj[intern(k)] = v
            rows.append(proj)
    return rows


def run_experiment(
    *,
    manifest_path: Path,
    datasets_root: Path,
    experiments_root: Path,
    registry_root: Path,
    code_revision: str | None = None,
    by: str = "experiments-runner",
    register: bool = True,
) -> tuple[ExperimentArtifacts, RegistryEntry | None]:
    manifest = TrainingManifest.from_yaml(manifest_path)
    dataset_dir = manifest.dataset.path_under(datasets_root)
    declared_build_params = dict(manifest.dataset.build_params or {})
    if declared_build_params:
        _verify_declared_build_params(declared_build_params, dataset_dir)
    data_path = dataset_dir / "data.jsonl"
    if not data_path.is_file():
        # Missing dataset file = "data not ready / orphan manifest", handled
        # as a clean skip (exit 78), not a cycle-failing error. See
        # DatasetMissingError + run_training_cycle.sh exit-78 handling.
        raise DatasetMissingError(data_path)
    rows = _load_jsonl(data_path, keep=dataset_projection_columns(manifest))
    if not rows:
        raise EmptyDatasetError(data_path)

    trainer_cls = _resolve_callable(manifest.trainer)
    evaluator_cls = _resolve_callable(manifest.evaluator)
    trainer = trainer_cls()
    evaluator = evaluator_cls()

    strategy = manifest.evaluator_config.get("split_strategy", "holdout")
    cv_folds: list[dict[str, Any]] | None = None
    if strategy in MULTI_FOLD_STRATEGIES:
        # Multi-fold cross-validation (opt-in). Fit + score each fold on its
        # own purged/embargoed train block, then pool the per-fold metrics.
        # The persisted, deployable model is refit on the FULL dataset — the
        # CV metrics estimate *its* generalization (de Prado, AFML Ch. 7).
        folds = iter_folds(rows, manifest.evaluator_config)
        fold_metrics: list[Mapping[str, float]] = []
        cv_folds = []
        for idx, (train_f, eval_f) in enumerate(folds):
            fold_state = dict(trainer.fit(train_f, manifest.trainer_config))
            fold_score = dict(
                evaluator.score(fold_state, eval_f, manifest.evaluator_config)
            )
            fold_metrics.append(fold_score)
            cv_folds.append(
                {
                    "fold": idx,
                    "n_train": len(train_f),
                    "n_eval": len(eval_f),
                    "metrics": fold_score,
                }
            )
        metrics = _aggregate_fold_metrics(fold_metrics)
        metrics["n_train_final"] = float(len(rows))
        model_state = dict(trainer.fit(rows, manifest.trainer_config))
    else:
        train_rows, eval_rows = split_rows(rows, manifest.evaluator_config)
        model_state = dict(trainer.fit(train_rows, manifest.trainer_config))
        metrics = dict(
            evaluator.score(model_state, eval_rows, manifest.evaluator_config)
        )

    started_at = _now_utc()
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    experiment_dir = experiments_root / manifest.model_id / run_id
    experiment_dir.mkdir(parents=True, exist_ok=True)

    manifest_out = experiment_dir / "manifest.json"
    manifest_out.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    model_state_path = experiment_dir / "model_state.json"
    model_state_path.write_text(
        json.dumps(model_state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metrics_path = experiment_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    cv_folds_path: Path | None = None
    if cv_folds is not None:
        cv_folds_path = experiment_dir / "cv_folds.json"
        cv_folds_path.write_text(
            json.dumps(
                {
                    "split_strategy": strategy,
                    "n_folds": len(cv_folds),
                    "folds": cv_folds,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    artifacts = ExperimentArtifacts(
        experiment_dir=experiment_dir,
        manifest_path=manifest_out,
        model_state_path=model_state_path,
        metrics_path=metrics_path,
        metrics=metrics,
        cv_folds_path=cv_folds_path,
    )

    entry: RegistryEntry | None = None
    if register:
        registry = ModelRegistry(registry_root)
        entry = registry.register(
            model_id=manifest.model_id,
            manifest=manifest.to_dict(),
            model_state_path=str(model_state_path.resolve()),
            metrics=metrics,
            code_revision=_resolve_commit_sha(code_revision),
            run_id=run_id,
            notes=manifest.notes,
            by=by,
        )

    return artifacts, entry
