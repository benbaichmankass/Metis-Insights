"""End-to-end test for the WS4 experiments runner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ml.experiments.runner import (
    EMPTY_DATASET_EXIT_CODE,
    DatasetMissingError,
    EmptyDatasetError,
    ManifestDatasetMismatchError,
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
    """A missing dataset file raises DatasetMissingError — a subclass of
    EmptyDatasetError so the CLI maps it to exit 78 and run_training_cycle.sh
    skips it (manifest_skipped, reason=dataset_absent) instead of failing the
    whole cycle on an orphan/not-yet-built manifest (MB-20260606-001).
    """
    manifest_path = _write_manifest(tmp_path)
    with pytest.raises(DatasetMissingError) as exc_info:
        run_experiment(
            manifest_path=manifest_path,
            datasets_root=tmp_path / "nonexistent",
            experiments_root=tmp_path / "exp",
            registry_root=tmp_path / "reg",
        )
    # Must ride the EmptyDatasetError exit-78 skip path, and the manual-run
    # hint must survive in the message.
    assert isinstance(exc_info.value, EmptyDatasetError)
    assert exc_info.value.data_path.name == "data.jsonl"
    assert "run `python -m ml.datasets build`" in str(exc_info.value)


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


def test_cli_train_skips_missing_dataset(tmp_path: Path, capsys):
    """Cycle-skip contract (MB-20260606-001 regression guard): `ml train` on a
    manifest whose dataset FILE is absent must return exit 78 and print
    reason=dataset_absent, so run_training_cycle.sh records `manifest_skipped`
    (overall_rc stays 0) instead of `manifest_failed` (overall_rc=1) on an
    orphan / not-yet-built manifest.
    """
    import argparse

    from ml.cli import _cmd_train

    manifest_path = _write_manifest(tmp_path)
    args = argparse.Namespace(
        manifest=str(manifest_path),
        datasets_root=str(tmp_path / "nonexistent"),
        experiments_root=str(tmp_path / "exp"),
        registry_root=str(tmp_path / "reg"),
        commit_sha="x",
        no_register=True,
    )
    rc = _cmd_train(args)
    assert rc == EMPTY_DATASET_EXIT_CODE
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is True
    assert out["reason"] == "dataset_absent"
    assert out["dataset_path"].endswith("data.jsonl")


# --- MB-20260716-BUILDPARAMS-IGNORED: manifest build_params vs dataset ---------

def _write_dataset_with_build_params(tmp_path: Path, build_params) -> Path:
    """A minimal dataset whose metadata.json records build_params (or none)."""
    family, scope, tf, version = "backtest_results", "all", "all", "v001"
    ds = tmp_path / "datasets-out" / family / scope / tf / version
    ds.mkdir(parents=True)
    with (ds / "data.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(6):
            fh.write(json.dumps({"id": i, "total_pnl_pct": 0.1,
                                 "strategy_version": "v"}, sort_keys=True) + "\n")
    meta = {"family": family, "version": version, "row_count": 6}
    if build_params is not None:
        meta["build_params"] = build_params
    (ds / "metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True),
                                      encoding="utf-8")
    return tmp_path / "datasets-out"


def _write_manifest_with_build_params(tmp_path: Path, build_params) -> Path:
    payload = {
        "manifest_version": "v1",
        "model_id": "demo-bp",
        "model_family": "regression_baseline",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "total_pnl_pct"},
        "dataset": {
            "family": "backtest_results", "symbol_scope": "all",
            "timeframe": "all", "version": "v001",
            "build_params": build_params,
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {"target_column": "total_pnl_pct",
                             "metrics": ["mse", "mae"], "holdout_fraction": 0.2},
        "target_deployment_stage": "research_only",
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_declared_build_params_mismatch_raises(tmp_path: Path):
    # Dir records vol_threshold 0.003; manifest declares 0.004 -> fail loud
    # (the trainer path does NOT apply build_params, so this would mislabel).
    datasets_root = _write_dataset_with_build_params(
        tmp_path, {"vol_threshold": 0.003, "n_vol_buckets": 3})
    manifest_path = _write_manifest_with_build_params(
        tmp_path, {"vol_threshold": 0.004})
    with pytest.raises(ManifestDatasetMismatchError):
        run_experiment(
            manifest_path=manifest_path, datasets_root=datasets_root,
            experiments_root=tmp_path / "exp", registry_root=tmp_path / "reg",
            register=False,
        )


def test_declared_build_params_match_proceeds(tmp_path: Path):
    datasets_root = _write_dataset_with_build_params(
        tmp_path, {"vol_threshold": 0.004})
    manifest_path = _write_manifest_with_build_params(
        tmp_path, {"vol_threshold": 0.004})
    artifacts, entry = run_experiment(
        manifest_path=manifest_path, datasets_root=datasets_root,
        experiments_root=tmp_path / "exp", registry_root=tmp_path / "reg",
        register=False,
    )
    assert artifacts.metrics_path.is_file()


def test_declared_build_params_unverifiable_warns_but_proceeds(tmp_path, capsys):
    # Legacy dir with no recorded build_params -> can't verify -> warn, proceed.
    datasets_root = _write_dataset_with_build_params(tmp_path, None)
    manifest_path = _write_manifest_with_build_params(
        tmp_path, {"vol_threshold": 0.004})
    artifacts, _ = run_experiment(
        manifest_path=manifest_path, datasets_root=datasets_root,
        experiments_root=tmp_path / "exp", registry_root=tmp_path / "reg",
        register=False,
    )
    assert artifacts.metrics_path.is_file()
    assert "BUILDPARAMS-IGNORED" in capsys.readouterr().err
