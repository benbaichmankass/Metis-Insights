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
