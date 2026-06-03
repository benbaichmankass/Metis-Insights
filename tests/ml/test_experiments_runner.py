"""End-to-end test for the WS4 experiments runner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ml.experiments.runner import (
    EMPTY_DATASET_EXIT_CODE,
    EmptyDatasetError,
    run_experiment,
)


def _write_dataset(tmp_path: Path, rows: list[dict]) -> Path:
    family, scope, tf, version = "backtest_results", "all", "all", "v001"
    ds = tmp_path / "datasets-out" / family / scope / tf / version
    ds.mkdir(parents=True)
    with (ds / "data.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    (ds / "metadata.json").write_text(
        json.dumps(
            {"family": family, "version": version, "row_count": len(rows)},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return tmp_path / "datasets-out"


def _write_manifest(tmp_path: Path, model_id: str = "demo-mean-v0") -> Path:
    payload = {
        "manifest_version": "v1",
        "model_id": model_id,
        "model_family": "regression_baseline",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "total_pnl_pct"},
        "dataset": {
            "family": "backtest_results",
            "symbol_scope": "all",
            "timeframe": "all",
            "version": "v001",
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {
            "target_column": "total_pnl_pct",
            "metrics": ["mse", "mae"],
            "holdout_fraction": 0.2,
        },
        "target_deployment_stage": "research_only",
        "notes": "test fixture",
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_run_experiment_round_trip(tmp_path: Path):
    rows = [
        {"id": i, "total_pnl_pct": 0.1 + 0.01 * i, "strategy_version": "v"}
        for i in range(10)
    ]
    datasets_root = _write_dataset(tmp_path, rows)
    manifest_path = _write_manifest(tmp_path)
    experiments_root = tmp_path / "experiments"
    registry_root = tmp_path / "registry"

    artifacts, entry = run_experiment(
        manifest_path=manifest_path,
        datasets_root=datasets_root,
        experiments_root=experiments_root,
        registry_root=registry_root,
        code_revision="abc123",
    )

    assert artifacts.manifest_path.is_file()
    assert artifacts.model_state_path.is_file()
    assert artifacts.metrics_path.is_file()

    metrics = json.loads(artifacts.metrics_path.read_text())
    assert "mse" in metrics
    assert "mae" in metrics
    assert metrics["mse"] >= 0
    assert metrics["mae"] >= 0

    state = json.loads(artifacts.model_state_path.read_text())
    assert "constant" in state
    assert state["target_column"] == "total_pnl_pct"

    assert entry is not None
    assert entry.status == "candidate"
    assert entry.code_revision == "abc123"
    assert entry.metrics["mse"] == metrics["mse"]


def test_run_experiment_no_register(tmp_path: Path):
    rows = [
        {"id": i, "total_pnl_pct": 0.1, "strategy_version": "v"}
        for i in range(4)
    ]
    datasets_root = _write_dataset(tmp_path, rows)
    manifest_path = _write_manifest(tmp_path, model_id="demo-no-reg")
    artifacts, entry = run_experiment(
        manifest_path=manifest_path,
        datasets_root=datasets_root,
        experiments_root=tmp_path / "exp",
        registry_root=tmp_path / "reg",
        code_revision="x",
        register=False,
    )
    assert entry is None
    assert artifacts.experiment_dir.is_dir()


def test_run_experiment_missing_dataset(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)
    with pytest.raises(FileNotFoundError):
        run_experiment(
            manifest_path=manifest_path,
            datasets_root=tmp_path / "nonexistent",
            experiments_root=tmp_path / "exp",
            registry_root=tmp_path / "reg",
        )


def test_run_experiment_empty_dataset_raises_distinct_error(tmp_path: Path):
    """0-row datasets must raise EmptyDatasetError (not the generic ValueError
    they raised before). The CLI maps this to exit code 78 so the cycle
    shell can emit `manifest_skipped` instead of `manifest_failed`.
    """
    manifest_path = _write_manifest(tmp_path)
    datasets_root = _write_dataset(tmp_path, rows=[])
    with pytest.raises(EmptyDatasetError) as exc_info:
        run_experiment(
            manifest_path=manifest_path,
            datasets_root=datasets_root,
            experiments_root=tmp_path / "exp",
            registry_root=tmp_path / "reg",
        )
    assert exc_info.value.data_path.name == "data.jsonl"


def test_empty_dataset_exit_code_pinned_to_78():
    """BSD `EX_CONFIG` convention — pin so the shell branch on rc=78 stays correct."""
    assert EMPTY_DATASET_EXIT_CODE == 78


def _write_cv_manifest(tmp_path: Path, model_id: str = "demo-cv-v0") -> Path:
    """Manifest opting into purged walk-forward CV via evaluator_config."""
    payload = {
        "manifest_version": "v1",
        "model_id": model_id,
        "model_family": "regression_baseline",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "total_pnl_pct"},
        "dataset": {
            "family": "backtest_results",
            "symbol_scope": "all",
            "timeframe": "all",
            "version": "v001",
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {
            "target_column": "total_pnl_pct",
            "metrics": ["mse", "mae"],
            "split_strategy": "purged_walk_forward",
            "time_column": "created_at",
            "n_folds": 4,
            "min_train_fraction": 0.5,
            "label_horizon": 1,
            "embargo_n": 1,
        },
        "target_deployment_stage": "research_only",
        "notes": "cv test fixture",
    }
    path = tmp_path / "cv_manifest.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_run_experiment_purged_walk_forward_cv(tmp_path: Path):
    """Opt-in purged WF-CV: runner iterates folds, writes a cv_folds.json
    artifact, and registers pooled metrics + n_folds. Default holdout path
    (other tests) is unaffected."""
    rows = [
        {
            "id": i,
            "total_pnl_pct": 0.1 + 0.01 * i,
            "created_at": f"2026-05-{i + 1:02d}T00:00:00Z",
        }
        for i in range(20)
    ]
    datasets_root = _write_dataset(tmp_path, rows)
    manifest_path = _write_cv_manifest(tmp_path)

    artifacts, entry = run_experiment(
        manifest_path=manifest_path,
        datasets_root=datasets_root,
        experiments_root=tmp_path / "exp",
        registry_root=tmp_path / "reg",
        code_revision="cv123",
    )

    # CV artifact written, with one entry per fold.
    assert artifacts.cv_folds_path is not None
    assert artifacts.cv_folds_path.is_file()
    cv = json.loads(artifacts.cv_folds_path.read_text())
    assert cv["split_strategy"] == "purged_walk_forward"
    assert cv["n_folds"] == len(cv["folds"]) >= 2
    # Every fold trained on a strictly smaller-than-full block (gap enforced).
    for fold in cv["folds"]:
        assert fold["n_train"] > 0
        assert fold["n_eval"] > 0
        assert fold["n_train"] + fold["n_eval"] < len(rows)

    # Pooled metrics carry the fold count and a full-data refit marker.
    metrics = json.loads(artifacts.metrics_path.read_text())
    assert metrics["n_folds"] == float(cv["n_folds"])
    assert metrics["n_train_final"] == float(len(rows))
    assert "mae" in metrics and "mse" in metrics

    # Deployable model_state is the full-data refit; registry holds pooled metrics.
    assert entry is not None
    assert entry.metrics["mae"] == metrics["mae"]


def test_holdout_path_writes_no_cv_artifact(tmp_path: Path):
    """The default single-split path must not emit a cv_folds.json."""
    rows = [
        {"id": i, "total_pnl_pct": 0.1 + 0.01 * i, "strategy_version": "v"}
        for i in range(10)
    ]
    datasets_root = _write_dataset(tmp_path, rows)
    manifest_path = _write_manifest(tmp_path, model_id="demo-holdout")
    artifacts, _ = run_experiment(
        manifest_path=manifest_path,
        datasets_root=datasets_root,
        experiments_root=tmp_path / "exp",
        registry_root=tmp_path / "reg",
        code_revision="h",
    )
    assert artifacts.cv_folds_path is None
    assert not (artifacts.experiment_dir / "cv_folds.json").exists()
