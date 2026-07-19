"""Integration tests for ``ml.promotion.live_parity.compute_live_parity``.

M25 gate reframe (operator-approved 2026-07-19,
docs/research/M25-promotion-consolidation-DESIGN.md § "The promotion gate —
REFRAMED 2026-07-19"): the live soak proves serving MECHANICS. These tests
exercise the end-to-end compute path — shadow log → registered artifact
re-score → training-dataset dead-feature comparison — over a real (tiny)
registry using the constant-baseline predictor, mirroring the fixture style
of ``tests/ml/test_shadow_factory.py``. The pure helpers and the gate-status
mapping are covered in ``tests/ml/test_gates.py``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.promotion.live_parity import compute_live_parity
from ml.registry.model_registry import ModelRegistry

_MODEL_ID = "eth-regime-15m-const-v1"
_CONSTANT = 0.5


def _manifest() -> dict:
    return {
        "manifest_version": "v1",
        "model_id": _MODEL_ID,
        "model_family": "regime",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "y"},
        "dataset": {
            "family": "market_features", "symbol_scope": "ETHUSDT",
            "timeframe": "15m", "version": "v1",
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {"target_column": "y", "time_column": "ts"},
        "target_deployment_stage": "shadow",
    }


def _setup(tmp_path: Path, *, train_rows: list[dict]) -> tuple[Path, Path, object]:
    """Build (registry_root, datasets_root, entry) with a loadable constant
    model + a training dataset on disk."""
    state_path = tmp_path / "model_state.json"
    state_path.write_text(json.dumps({
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "constant": _CONSTANT,
    }))
    registry_root = tmp_path / "registry-store"
    registry = ModelRegistry(registry_root)
    registry.register(
        model_id=_MODEL_ID, manifest=_manifest(),
        model_state_path=str(state_path),
        metrics={"macro_f1": 0.6}, code_revision="x",
    )  # default registration stage: shadow → loadable by the factory
    datasets_root = tmp_path / "datasets-out"
    ds_dir = datasets_root / "market_features" / "ETHUSDT" / "15m" / "v1"
    ds_dir.mkdir(parents=True)
    (ds_dir / "data.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in train_rows)
    )
    return registry_root, datasets_root, registry.get(_MODEL_ID)


def _write_log(path: Path, rows: list[dict], *, score: float = _CONSTANT) -> None:
    now = datetime.now(timezone.utc)
    with path.open("w", encoding="utf-8") as fh:
        for i, feature_row in enumerate(rows):
            fh.write(json.dumps({
                "predicted_at_utc": (now - timedelta(minutes=len(rows) - i)).isoformat(),
                "model_id": _MODEL_ID, "stage": "shadow", "score": score,
                "row_keys": sorted(feature_row), "feature_row": feature_row,
            }) + "\n")


def test_compute_live_parity_happy_path(tmp_path: Path):
    # Live rows the constant artifact reproduces exactly; every train column
    # varies on both sides → no mismatches, no dead features.
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2, "x": i * 0.3} for i in range(30)]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow.jsonl"
    _write_log(log, [{"a": i * 0.2, "x": i * 0.3} for i in range(25)])
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.error is None
    assert res.train_available is True
    assert res.n_live_rows == 25
    assert res.n_sampled == 25
    assert res.n_mismatched == 0
    assert res.dead_live_features == ()
    assert res.dead_train_features == ()


def test_compute_live_parity_detects_score_mismatch(tmp_path: Path):
    # Logged scores disagree with what the registered artifact produces →
    # every sampled row is a serving-fidelity mismatch.
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2} for i in range(30)]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow.jsonl"
    _write_log(log, [{"a": i * 0.2} for i in range(25)], score=_CONSTANT + 0.1)
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.n_mismatched == res.n_sampled == 25


def test_compute_live_parity_detects_dead_live_feature(tmp_path: Path):
    # The ETH-xa class end-to-end: `x` varies in training but is constant
    # 0.0 across every live row → dead-on-LIVE. The target/time columns are
    # excluded from the universe (never flagged).
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2, "x": i * 0.3} for i in range(30)]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow.jsonl"
    _write_log(log, [{"a": i * 0.2, "x": 0.0} for i in range(25)])
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.dead_live_features == ("x",)
    assert "y" not in res.dead_live_features  # target column excluded
    assert "ts" not in res.dead_live_features  # time column excluded


def test_compute_live_parity_missing_dataset_is_train_unavailable(tmp_path: Path):
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2} for i in range(5)]
    reg_root, _, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow.jsonl"
    _write_log(log, [{"a": i * 0.2} for i in range(25)])
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=None, registry_root=reg_root,
    )
    assert res.error is None
    assert res.train_available is False  # gate → insufficient_data


def test_compute_live_parity_model_load_failure_is_error(tmp_path: Path):
    # Fail-safe: an unloadable artifact populates `error` (→ the gate reports
    # insufficient_data) instead of raising or silently passing.
    train = [{"ts": 0, "y": 0.0, "a": 0.0}]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    Path(tmp_path / "model_state.json").unlink()
    log = tmp_path / "shadow.jsonl"
    _write_log(log, [{"a": i * 0.2} for i in range(25)])
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.error is not None
    assert "load failed" in res.error
    assert res.n_live_rows == 25  # the live-row count still reported


def test_compute_live_parity_missing_log_is_error(tmp_path: Path):
    train = [{"ts": 0, "y": 0.0, "a": 0.0}]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    res = compute_live_parity(
        entry, shadow_log=tmp_path / "nope.jsonl",
        datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.error is not None
