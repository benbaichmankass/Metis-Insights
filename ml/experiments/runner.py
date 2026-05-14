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
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..manifest import TrainingManifest
from ..registry.model_registry import ModelRegistry, RegistryEntry
from .splitters import split as split_rows


class EmptyDatasetError(RuntimeError):
    """Raised when the dataset exists but has 0 rows.

    Distinct from `FileNotFoundError` (build never ran). The CLI maps this
    to exit code 78 (BSD `EX_CONFIG`) so `run_training_cycle.sh` can emit
    a clean `manifest_skipped` status instead of `manifest_failed` —
    distinguishes "no data yet" from real training failures.
    """

    def __init__(self, data_path: Path):
        super().__init__(f"dataset at {data_path} is empty")
        self.data_path = data_path


EMPTY_DATASET_EXIT_CODE = 78


@dataclass(frozen=True)
class ExperimentArtifacts:
    experiment_dir: Path
    manifest_path: Path
    model_state_path: Path
    metrics_path: Path
    metrics: Mapping[str, float]


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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            rows.append(json.loads(line))
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
    data_path = dataset_dir / "data.jsonl"
    if not data_path.is_file():
        raise FileNotFoundError(
            f"dataset data not found at {data_path}; "
            f"run `python -m ml.datasets build` first"
        )
    rows = _load_jsonl(data_path)
    if not rows:
        raise EmptyDatasetError(data_path)

    train_rows, eval_rows = split_rows(rows, manifest.evaluator_config)

    trainer_cls = _resolve_callable(manifest.trainer)
    evaluator_cls = _resolve_callable(manifest.evaluator)
    trainer = trainer_cls()
    evaluator = evaluator_cls()

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

    artifacts = ExperimentArtifacts(
        experiment_dir=experiment_dir,
        manifest_path=manifest_out,
        model_state_path=model_state_path,
        metrics_path=metrics_path,
        metrics=metrics,
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
            notes=manifest.notes,
            by=by,
        )

    return artifacts, entry
