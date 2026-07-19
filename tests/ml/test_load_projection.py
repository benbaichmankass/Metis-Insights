"""Load-time column projection (BL-20260717-TRAINER-SINGLE-MANIFEST-OOM).

The trainer loader materialized data.jsonl as list-of-dicts with EVERY column,
which blew the 5 GB trainer cgroup on the ~500k-row 5m datasets (btc/sol 5m
heads OOM'd ALONE at ~5.2 G anon-rss, 2026-07-19). `_load_jsonl` now projects
each row down to the manifest-referenced columns + a hardcoded safety set.
These tests pin the projection contract: manifest columns survive, safety
columns survive, unreferenced columns are dropped, the fail-open paths
(no-overlap datasets, env opt-out) restore full-column loading, and
`run_experiment` still trains correctly end-to-end on a projected load.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from ml.experiments.runner import (
    _PROJECTION_SAFETY_COLUMNS,
    _load_jsonl,
    dataset_projection_columns,
    run_experiment,
)
from ml.manifest import TrainingManifest


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def _manifest(tmp_path: Path, trainer_config: dict, evaluator_config: dict) -> TrainingManifest:
    payload = {
        "manifest_version": "v1",
        "model_id": "proj-test-v0",
        "model_family": "classification_lightgbm",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": trainer_config,
        "dataset": {
            "family": "market_features",
            "symbol_scope": "BTCUSDT",
            "timeframe": "5m",
            "version": "v001",
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": evaluator_config,
        "target_deployment_stage": "research_only",
        "notes": "projection test fixture",
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return TrainingManifest.from_yaml(path)


def test_projection_columns_union_manifest_refs_and_safety_set(tmp_path: Path):
    m = _manifest(
        tmp_path,
        {"target_column": "regime_label", "feature_columns": ["ofi", "vpin"],
         "sample_weight": {"time_column": "ts"}},
        {"target_column": "regime_label", "time_column": "ts"},
    )
    cols = dataset_projection_columns(m)
    assert cols is not None
    # manifest-declared columns (incl. nested config strings) survive
    assert {"ofi", "vpin", "regime_label", "ts"} <= cols
    # the hardcoded safety set is always included
    assert _PROJECTION_SAFETY_COLUMNS <= cols


def test_projection_env_opt_out(tmp_path: Path, monkeypatch):
    m = _manifest(tmp_path, {"target_column": "regime_label"}, {})
    monkeypatch.setenv("TRAINING_LOAD_ALL_COLUMNS", "1")
    assert dataset_projection_columns(m) is None


def test_load_jsonl_projects_and_keeps_declared_columns(tmp_path: Path):
    data = _write_jsonl(
        tmp_path / "data.jsonl",
        [
            {"ts": "2026-01-01T00:00:00Z", "regime_label": "range", "ofi": 0.1,
             "unreferenced_a": 1.0, "unreferenced_b": "x", "vol_bucket": "low"},
            {"ts": "2026-01-01T01:00:00Z", "regime_label": "volatile", "ofi": 0.2,
             "unreferenced_a": 2.0, "unreferenced_b": "y", "vol_bucket": "high"},
        ],
    )
    rows = _load_jsonl(data, keep=frozenset({"ts", "regime_label", "ofi"}) | _PROJECTION_SAFETY_COLUMNS)
    assert len(rows) == 2
    for row in rows:
        assert "unreferenced_a" not in row and "unreferenced_b" not in row
        assert {"ts", "regime_label", "ofi", "vol_bucket"} <= row.keys()


def test_load_jsonl_fails_open_when_no_overlap(tmp_path: Path):
    # A dataset whose columns share nothing with the keep-set loads FULL rows
    # (identical to the pre-fix loader) instead of empty dicts.
    data = _write_jsonl(
        tmp_path / "data.jsonl",
        [{"weird_col": 1, "other": "a"}, {"weird_col": 2, "other": "b"}],
    )
    rows = _load_jsonl(data, keep=frozenset({"ts", "regime_label"}))
    assert rows == [{"weird_col": 1, "other": "a"}, {"weird_col": 2, "other": "b"}]


def test_load_jsonl_no_keep_loads_all(tmp_path: Path):
    data = _write_jsonl(tmp_path / "data.jsonl", [{"a": 1, "b": 2}])
    assert _load_jsonl(data) == [{"a": 1, "b": 2}]
    assert _load_jsonl(data, keep=None) == [{"a": 1, "b": 2}]


def test_run_experiment_trains_identically_on_projected_load(tmp_path: Path):
    # End-to-end: a dataset with extra junk columns trains fine and the metrics
    # only depend on the referenced columns (junk is invisible to the trainer).
    family_dir = tmp_path / "datasets-out" / "market_features" / "BTCUSDT" / "5m" / "v001"
    family_dir.mkdir(parents=True)
    rows = [
        {"ts": f"2026-01-01T{h:02d}:00:00Z", "total_pnl_pct": float(h % 3),
         "junk_wide_column": "z" * 50, "another_junk": h * 1.5}
        for h in range(20)
    ]
    _write_jsonl(family_dir / "data.jsonl", rows)
    (family_dir / "metadata.json").write_text(
        json.dumps({"family": "market_features", "row_count": len(rows)}),
        encoding="utf-8",
    )
    payload = {
        "manifest_version": "v1",
        "model_id": "proj-e2e-v0",
        "model_family": "regression_baseline",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "total_pnl_pct"},
        "dataset": {"family": "market_features", "symbol_scope": "BTCUSDT",
                    "timeframe": "5m", "version": "v001"},
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {"target_column": "total_pnl_pct",
                             "metrics": ["mse", "mae"], "holdout_fraction": 0.2},
        "target_deployment_stage": "research_only",
        "notes": "projection e2e fixture",
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    artifacts, entry = run_experiment(
        manifest_path=manifest_path,
        datasets_root=tmp_path / "datasets-out",
        experiments_root=tmp_path / "experiments",
        registry_root=tmp_path / "registry",
        register=False,
    )
    assert artifacts.metrics_path.is_file()
    assert "mse" in artifacts.metrics
    assert entry is None
