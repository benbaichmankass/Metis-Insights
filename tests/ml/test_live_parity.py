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


def _write_log(
    path: Path, rows: list[dict], *, score: float = _CONSTANT,
    offset_minutes: float = 31.0,
) -> None:
    """Write shadow rows stamped AFTER registration + the artifact grace.

    ``register()`` stamps the run at now, and fidelity only samples rows that
    postdate the current artifact by ``DEFAULT_ARTIFACT_GRACE_S`` (30 min) —
    so the default offset places rows just past that cutoff. Pass a negative
    ``offset_minutes`` to write artifact-STALE rows (pre-registration)."""
    now = datetime.now(timezone.utc)
    with path.open("w", encoding="utf-8") as fh:
        for i, feature_row in enumerate(rows):
            ts = now + timedelta(minutes=offset_minutes + i)
            fh.write(json.dumps({
                "predicted_at_utc": ts.isoformat(),
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


def test_dead_feature_universe_scoped_to_declared_feature_columns(tmp_path: Path):
    """When the manifest declares feature_columns, only CONSUMED features can
    be flagged dead — label/side-stream dataset columns the model never reads
    must not block the gate (the 2026-07-20 MES certification false-blocker:
    forward_log_return + the macro block flagged although none were in the
    head's consumed set)."""
    # Training set carries: consumed 'a' (varying), consumed 'x' (varying),
    # plus label-ish 'forward_log_return' and macro-ish 'vix_level' — both
    # varying in training but CONSTANT in the live rows below.
    train = [
        {"ts": i, "y": i * 0.1, "a": i * 0.2, "x": i * 0.3,
         "forward_log_return": i * 0.01, "vix_level": 12.0 + i}
        for i in range(30)
    ]
    registry_root, datasets_root, entry = _setup(tmp_path, train_rows=train)
    # Declare the consumed set on the registered manifest.
    import dataclasses
    manifest = dict(entry.manifest)
    manifest["trainer_config"] = {"target_column": "y",
                                  "feature_columns": ["a", "x"]}
    entry = dataclasses.replace(entry, manifest=manifest)

    log = tmp_path / "shadow_predictions.jsonl"
    # Live rows: consumed features vary; the unconsumed columns are constant
    # (they would be flagged dead-live under the unscoped universe).
    _write_log(log, [
        {"a": i * 0.2, "x": i * 0.3, "forward_log_return": 0.0,
         "vix_level": 0.0}
        for i in range(25)
    ])
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=datasets_root,
        registry_root=registry_root,
    )
    assert res.train_available
    assert res.dead_live_features == ()
    assert res.dead_train_features == ()


def test_dead_feature_universe_falls_back_without_declared_columns(tmp_path: Path):
    """No declared feature_columns → the old full-dataset-universe behaviour
    is unchanged (a varying-in-train, constant-live column IS flagged)."""
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2, "vix_level": 12.0 + i}
             for i in range(30)]
    registry_root, datasets_root, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow_predictions.jsonl"
    _write_log(log, [{"a": i * 0.2, "vix_level": 0.0} for i in range(25)])
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=datasets_root,
        registry_root=registry_root,
    )
    assert res.train_available
    assert "vix_level" in res.dead_live_features


def test_fidelity_scoped_to_artifact_fresh_rows(tmp_path: Path):
    """Rows scored BEFORE the current artifact's run are excluded from the
    fidelity sample (the nightly-retrain "mismatch 50/50" false blocker):
    stale rows with a wrong score do not count as mismatches, while the
    dead-feature check still runs over the recent window."""
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2} for i in range(30)]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow.jsonl"
    # All rows predate registration (offset −120 min) AND carry a score the
    # current artifact would never produce — the structural false-fail shape.
    _write_log(
        log, [{"a": i * 0.2} for i in range(25)],
        score=_CONSTANT + 0.1, offset_minutes=-120.0,
    )
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.error is None
    assert res.artifact_at is not None
    assert res.n_live_rows == 25
    assert res.n_fresh_rows == 0
    assert res.n_sampled == 0           # nothing fresh to judge
    assert res.n_mismatched == 0        # stale rows are NOT mismatches
    assert res.train_available is True  # dead-feature check still ran


def test_fidelity_fresh_rows_still_catch_real_mismatch(tmp_path: Path):
    """Rows that DO postdate the artifact and mismatch are the real
    serving-staleness signal — scoping must not blind the gate to it."""
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2} for i in range(30)]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow.jsonl"
    _write_log(log, [{"a": i * 0.2} for i in range(25)], score=_CONSTANT + 0.1)
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.n_fresh_rows == 25
    assert res.n_mismatched == res.n_sampled == 25


def test_gate_insufficient_when_no_artifact_fresh_rows(tmp_path: Path):
    """The live_parity GATE reports insufficient_data (not fail, not pass)
    when plenty of live rows exist but none postdate the current artifact."""
    from ml.promotion.gates import _gate_live_parity, regime_classifier_thresholds
    from ml.promotion.live_parity import LiveParityResult

    parity = LiveParityResult(
        model_id=_MODEL_ID, n_live_rows=1292, n_sampled=0, n_mismatched=0,
        train_available=True,
        artifact_at="2026-07-20T01:38:51+00:00", n_fresh_rows=3,
    )
    th = regime_classifier_thresholds()
    result = _gate_live_parity(parity, th)
    assert result.status == "insufficient_data"
    assert result.required is True
    assert "since the current artifact" in result.detail


def test_score_fidelity_passes_raw_row_without_numeric_coercion():
    """Regression: MB-20260720-LIVE-SERVING-PARITY-SKEW false alarm.

    The live scorer predicts on the feature_row EXACTLY as logged; lightgbm's
    frame build is dtype-sensitive, so coercing int features (dayofweek,
    hour_of_day) to float shifted every re-score and reported a 100% "serving
    skew" that did not exist. score_fidelity must therefore hand predict_fn
    the raw row — a dtype-sensitive predictor sees ints as ints.
    """
    from ml.promotion.live_parity import score_fidelity

    def dtype_sensitive_predict(row):
        # Mimics the lightgbm dtype sensitivity: an int-typed dayofweek scores
        # differently from a float-typed one.
        return 0.9 if isinstance(row["dayofweek"], int) else 0.1

    pairs = [({"dayofweek": 0, "x": 1.5}, 0.9)] * 3
    assert score_fidelity(pairs, dtype_sensitive_predict, score_tol=1e-6) == 0


def test_calendar_feature_alive_over_wide_window_not_flagged(tmp_path: Path):
    """Regression: dayofweek false-dead on sub-24h windows (2026-07-20).

    Liveness is judged over the wide DEAD_WINDOW, not the 50-row fidelity
    sample: a calendar-style feature constant across the newest 50 rows but
    varying earlier in the window must NOT be flagged dead-on-live.
    """
    train = [{"ts": i, "y": i * 0.1, "a": i * 0.2, "d": i % 7} for i in range(30)]
    reg_root, ds_root, entry = _setup(tmp_path, train_rows=train)
    log = tmp_path / "shadow.jsonl"
    # 100 rows: 'd' varies in the older half, constant over the newest 50
    # ("today"); 'a' varies throughout.
    rows = [
        {"a": i * 0.2, "d": (i // 25) if i < 50 else 3} for i in range(100)
    ]
    _write_log(log, rows)
    res = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
    )
    assert res.error is None
    assert "d" not in res.dead_live_features

    # Same data judged with the liveness window shrunk to the fidelity sample
    # reproduces the false positive — proving the wide window is load-bearing.
    res_narrow = compute_live_parity(
        entry, shadow_log=log, datasets_root=ds_root, registry_root=reg_root,
        dead_window_n=50,
    )
    assert "d" in res_narrow.dead_live_features
