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


# --- FIX-CA-21: the manifest's own purge horizon / embargo is honoured -------
# CA-B01-oos-edge-discards-manifest-purge-horizon (PI-20260927-3WM5HADW-0002):
# 28 manifests declare evaluator_config label_horizon=5 / embargo_fraction=0.01
# (incl. both live advisory fc-pcv-v2 heads), and the OOS-edge gate overwrote
# them with 1 / 0.0 — 4 bars of label overlap at every fold boundary.


def _purged_manifest() -> dict:
    m = _manifest()
    m["evaluator_config"] = {
        **m["evaluator_config"],
        "split_strategy": "purged_walk_forward",
        "label_horizon": 5, "embargo_fraction": 0.01,
    }
    return m


def _capture_cv_cfg(monkeypatch) -> list[dict]:
    import ml.promotion.oos_edge as oe

    seen: list[dict] = []
    real = oe._pooled_cv_metrics

    def _spy(rows, trainer, evaluator, trainer_config, cv_cfg):
        seen.append(dict(cv_cfg))
        return real(rows, trainer, evaluator, trainer_config, cv_cfg)

    monkeypatch.setattr(oe, "_pooled_cv_metrics", _spy)
    return seen


def test_build_cv_config_inherits_manifest_horizon_and_embargo():
    from ml.promotion.oos_edge import build_cv_config

    cfg = build_cv_config({"label_horizon": 5, "embargo_fraction": 0.01})
    assert cfg["label_horizon"] == 5 and cfg["embargo_fraction"] == 0.01
    # An explicit override may only WIDEN the manifest's purge, never shrink it.
    cfg = build_cv_config({"label_horizon": 5, "embargo_fraction": 0.01},
                          label_horizon=1, embargo_fraction=0.0)
    assert cfg["label_horizon"] == 5 and cfg["embargo_fraction"] == 0.01
    cfg = build_cv_config({"label_horizon": 5}, label_horizon=8, embargo_fraction=0.02)
    assert cfg["label_horizon"] == 8 and cfg["embargo_fraction"] == 0.02
    # Undeclared + no override: the historical 1 / 0.0 defaults.
    cfg = build_cv_config({})
    assert cfg["label_horizon"] == 1 and cfg["embargo_fraction"] == 0.0


def test_compute_oos_edge_uses_manifest_purge_horizon(tmp_path: Path, monkeypatch):
    seen = _capture_cv_cfg(monkeypatch)
    root = tmp_path / "datasets-out"
    _write_dataset(root, _rows())
    entry = _entry(tmp_path, _purged_manifest())
    assert compute_oos_edge(entry, datasets_root=root, n_folds=3) is not None
    assert seen and all(
        c["label_horizon"] == 5 and c["embargo_fraction"] == 0.01 for c in seen
    ), seen


def test_oos_edge_one_cli_uses_manifest_purge_horizon(tmp_path: Path, monkeypatch, capsys):
    from ml.cli import main

    seen = _capture_cv_cfg(monkeypatch)
    root = tmp_path / "datasets-out"
    _write_dataset(root, _rows())
    _entry(tmp_path, _purged_manifest())
    rc = main(["_oos-edge-one", "m-const",
               "--registry-root", str(tmp_path / "registry-store"),
               "--datasets-root", str(root)])
    assert rc == 0
    assert seen and all(
        c["label_horizon"] == 5 and c["embargo_fraction"] == 0.01 for c in seen
    ), seen


def test_gate_check_cli_defaults_do_not_shrink_manifest_purge(tmp_path: Path, monkeypatch, capsys):
    from ml.cli import main

    seen = _capture_cv_cfg(monkeypatch)
    root = tmp_path / "datasets-out"
    _write_dataset(root, _rows())
    _entry(tmp_path, _purged_manifest())
    main(["gate-check", "m-const",
          "--registry-root", str(tmp_path / "registry-store"),
          "--datasets-root", str(root), "--n-folds", "3"])
    assert seen and all(
        c["label_horizon"] == 5 and c["embargo_fraction"] == 0.01 for c in seen
    ), seen
