"""Tests for the offline OOS-edge computation (ml.promotion.oos_edge).

Synthetic-only: a tiny on-disk dataset + a manifest that uses the
constant-mean trainer for BOTH the candidate and the baseline, so the
edge is deterministically ~0 and the plumbing (manifest reconstruction →
purged WF-CV folds → pooled metric → oriented edge) is exercised without
any live data or LightGBM dependency.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ml.promotion.oos_edge import (
    OOSEdgeResult,
    compute_oos_edge,
    orient_edge,
)
from ml.registry.model_registry import ModelRegistry


def _write_dataset(root: Path, rows: list[dict]) -> None:
    # datasets_root / family / scope / timeframe / version / data.jsonl
    ddir = root / "fam" / "all" / "all" / "v1"
    ddir.mkdir(parents=True, exist_ok=True)
    with (ddir / "data.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _manifest() -> dict:
    return {
        "manifest_version": "v1",
        "model_id": "m-const",
        "model_family": "regression",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "y"},
        "dataset": {
            "family": "fam", "symbol_scope": "all",
            "timeframe": "all", "version": "v1",
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {
            "target_column": "y", "metrics": ["mae", "mse"],
            "time_column": "created_at",
        },
        "target_deployment_stage": "shadow",
    }


def _entry(tmp_path: Path, manifest: dict):
    registry = ModelRegistry(tmp_path / "registry-store")
    return registry.register(
        model_id=manifest["model_id"], manifest=manifest,
        model_state_path="x", metrics={"mae": 0.0}, code_revision="a",
    )


def _rows(n: int = 60) -> list[dict]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {"created_at": base.replace(minute=i % 60).isoformat(),
         "y": float(i % 5)}
        for i in range(n)
    ]


def test_orient_edge_direction():
    # higher-is-better: candidate>baseline → positive edge
    assert orient_edge("macro_f1", 0.7, 0.6) > 0
    assert orient_edge("macro_f1", 0.5, 0.6) < 0
    # lower-is-better: candidate<baseline → positive edge
    assert orient_edge("mae", 0.05, 0.07) > 0
    assert orient_edge("mae", 0.07, 0.05) < 0


def test_compute_oos_edge_constant_vs_constant_is_zero(tmp_path: Path):
    root = tmp_path / "datasets-out"
    _write_dataset(root, _rows())
    entry = _entry(tmp_path, _manifest())
    result = compute_oos_edge(entry, datasets_root=root, n_folds=3, label_horizon=1)
    assert isinstance(result, OOSEdgeResult)
    # Same trainer for candidate + baseline → identical pooled metric → 0 edge.
    assert result.metric in {"mae", "mse"}
    assert result.higher_is_better is False
    assert abs(result.edge) < 1e-9
    assert result.n_folds == 3
    assert result.baseline_trainer.endswith("ConstantPredictionTrainer")


def test_compute_oos_edge_missing_dataset_returns_none(tmp_path: Path):
    entry = _entry(tmp_path, _manifest())
    # datasets_root has no data.jsonl under it → insufficient evidence.
    result = compute_oos_edge(entry, datasets_root=tmp_path / "empty")
    assert result is None


def test_compute_oos_edge_bad_manifest_returns_none(tmp_path: Path):
    # A partial manifest (the shape the gate unit-tests use) can't be
    # reconstructed into a TrainingManifest → None, not a crash.
    registry = ModelRegistry(tmp_path / "registry-store")
    entry = registry.register(
        model_id="partial", manifest={"model_id": "partial"},
        model_state_path="x", metrics={}, code_revision="a",
    )
    assert compute_oos_edge(entry, datasets_root=tmp_path) is None


def test_compute_oos_edge_too_few_rows_returns_none(tmp_path: Path):
    root = tmp_path / "datasets-out"
    _write_dataset(root, _rows(n=3))  # fewer than n_folds+1
    entry = _entry(tmp_path, _manifest())
    assert compute_oos_edge(entry, datasets_root=root, n_folds=5) is None
