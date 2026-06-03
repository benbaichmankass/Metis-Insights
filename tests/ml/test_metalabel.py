"""Tests for the live-holdout split + the meta-label manifest (S-MLOPT-S6)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml.experiments.splitters import split, split_live_holdout
from ml.manifest import TrainingManifest


def _rows(n_synth: int, n_live: int) -> list[dict]:
    rows = []
    for i in range(n_synth):
        rows.append({"ts": f"2026-01-01T00:{i:02d}:00Z", "won": i % 2,
                     "is_live_trade": False, "x": float(i)})
    for i in range(n_live):
        rows.append({"ts": f"2026-02-01T00:{i:02d}:00Z", "won": 1,
                     "is_live_trade": True, "x": float(i)})
    return rows


def test_live_holdout_partitions_on_flag():
    train, ev = split_live_holdout(_rows(6, 3), {"time_column": "ts"})
    assert len(train) == 6 and all(not r["is_live_trade"] for r in train)
    assert len(ev) == 3 and all(r["is_live_trade"] for r in ev)


def test_live_holdout_via_split_dispatch():
    train, ev = split(_rows(4, 2), {"split_strategy": "live_holdout", "time_column": "ts"})
    assert len(train) == 4 and len(ev) == 2


def test_live_holdout_requires_both_populations():
    with pytest.raises(ValueError, match="no real"):
        split_live_holdout(_rows(5, 0), {})
    with pytest.raises(ValueError, match="no synthetic"):
        split_live_holdout(_rows(0, 5), {})


def test_metalabel_manifest_is_valid():
    path = Path("ml/configs/setup-candidates-metalabel-v1.yaml")
    m = TrainingManifest.from_yaml(path)
    assert m.model_id == "setup-candidates-metalabel-v1"
    assert m.dataset.family == "setup_candidates"
    assert m.evaluator_config["split_strategy"] == "live_holdout"
    assert m.evaluator_config["target_column"] == "won"
    # The model trains at research_only — promotion past shadow is Tier-3.
    assert m.target_deployment_stage == "research_only"
    # No outcome/label column leaks into the feature set.
    feats = set(m.trainer_config["feature_columns"])
    assert feats.isdisjoint({"won", "label", "r_multiple", "ret",
                             "barrier_touched", "is_live_trade"})


def test_live_holdout_runs_end_to_end(tmp_path: Path):
    # Prove the runner's single-split path drives `live_holdout` with a real
    # trainer+evaluator (constant baseline → no lightgbm needed): train on
    # synthetic, score on the live holdout, emit classification metrics.
    from ml.experiments.runner import run_experiment

    ddir = tmp_path / "ds" / "setup_candidates" / "BTCUSDT" / "all" / "v001"
    ddir.mkdir(parents=True)
    with (ddir / "data.jsonl").open("w") as fh:
        for r in _rows(20, 5):
            fh.write(json.dumps(r) + "\n")
    manifest = {
        "manifest_version": "v1", "model_id": "ml-test",
        "model_family": "regression",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "won"},
        "dataset": {"family": "setup_candidates", "symbol_scope": "BTCUSDT",
                    "timeframe": "all", "version": "v001"},
        "evaluator": "ml.evaluators.classification.ClassificationEvaluator",
        "evaluator_config": {"target_column": "won", "split_strategy": "live_holdout",
                             "time_column": "ts"},
        "target_deployment_stage": "research_only",
    }
    mpath = tmp_path / "m.yaml"
    import yaml
    mpath.write_text(yaml.safe_dump(manifest))
    artifacts, _ = run_experiment(
        manifest_path=mpath, datasets_root=tmp_path / "ds",
        experiments_root=tmp_path / "exp", registry_root=tmp_path / "reg",
        register=False,
    )
    assert "accuracy" in artifacts.metrics
    assert artifacts.metrics["n_eval"] == 5.0  # scored on the 5 live rows
