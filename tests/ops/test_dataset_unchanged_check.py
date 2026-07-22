"""Tests for the dataset-unchanged retrain skip check
(scripts/ops/dataset_unchanged_check.py, MB-20260720-FCPCV-RETRAIN-NOOP).

Covers the SKIP case (frozen pin: dataset AND manifest older than the newest
registered run), every TRAIN escape hatch (fresh dataset, edited manifest,
never trained, no registry entry, missing dataset), and the fail-open path.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "dataset_unchanged_check",
    Path(__file__).resolve().parents[2]
    / "scripts" / "ops" / "dataset_unchanged_check.py",
)
duc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(duc)

_MODEL_ID = "frozen-pin-test-v1"


def _write_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "manifest.yaml"
    p.write_text(
        "manifest_version: v1\n"
        f"model_id: {_MODEL_ID}\n"
        "model_family: regime\n"
        "trainer: ml.trainers.constant_baseline.ConstantPredictionTrainer\n"
        "trainer_config: {target_column: y}\n"
        "dataset:\n"
        "  family: market_features\n"
        "  symbol_scope: BTCUSDT\n"
        "  timeframe: 15m\n"
        "  version: v520\n"
        "evaluator: ml.evaluators.regression.RegressionEvaluator\n"
        "evaluator_config: {target_column: y, time_column: ts}\n"
        "target_deployment_stage: shadow\n"
    )
    return p


def _write_dataset(tmp_path: Path) -> Path:
    ds_dir = tmp_path / "datasets-out" / "market_features" / "BTCUSDT" / "15m" / "v520"
    ds_dir.mkdir(parents=True)
    data = ds_dir / "data.jsonl"
    data.write_text('{"ts": 1, "y": 0.1}\n')
    return data


def _write_registry(tmp_path: Path, *, run_at: str, runs: bool = True) -> Path:
    reg = tmp_path / "registry-store"
    reg.mkdir(exist_ok=True)
    # Filename deliberately != model_id — the checker must match on content.
    (reg / "some-file.json").write_text(json.dumps({
        "model_id": _MODEL_ID,
        "runs": [{"at": run_at}] if runs else [],
    }))
    # A sibling non-entry artifact must not break the scan.
    (reg / "not-an-entry.json").write_text("[1, 2, 3]")
    return reg


def _set_old(path: Path, seconds_ago: float = 86400.0) -> None:
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def _future_iso() -> str:
    """A run timestamp comfortably in the future (newer than any file)."""
    import datetime as dt

    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()


def _past_iso(days: float = 2.0) -> str:
    import datetime as dt

    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


class TestDecide:
    def test_skip_when_dataset_and_manifest_predate_last_run(self, tmp_path):
        m = _write_manifest(tmp_path)
        d = _write_dataset(tmp_path)
        _set_old(m)
        _set_old(d)
        reg = _write_registry(tmp_path, run_at=_past_iso(days=0.5))
        assert duc.decide(str(m), str(tmp_path / "datasets-out"), str(reg)) == "SKIP"

    def test_train_when_dataset_rebuilt_after_last_run(self, tmp_path):
        m = _write_manifest(tmp_path)
        _write_dataset(tmp_path)  # fresh mtime = now
        _set_old(m)
        reg = _write_registry(tmp_path, run_at=_past_iso(days=0.5))
        assert duc.decide(str(m), str(tmp_path / "datasets-out"), str(reg)) == "TRAIN"

    def test_train_when_manifest_edited_after_last_run(self, tmp_path):
        m = _write_manifest(tmp_path)  # fresh mtime = now
        d = _write_dataset(tmp_path)
        _set_old(d)
        reg = _write_registry(tmp_path, run_at=_past_iso(days=0.5))
        assert duc.decide(str(m), str(tmp_path / "datasets-out"), str(reg)) == "TRAIN"

    def test_train_when_never_trained(self, tmp_path):
        m = _write_manifest(tmp_path)
        d = _write_dataset(tmp_path)
        _set_old(m)
        _set_old(d)
        reg = _write_registry(tmp_path, run_at=_past_iso(), runs=False)
        assert duc.decide(str(m), str(tmp_path / "datasets-out"), str(reg)) == "TRAIN"

    def test_train_when_no_registry_entry(self, tmp_path):
        m = _write_manifest(tmp_path)
        d = _write_dataset(tmp_path)
        _set_old(m)
        _set_old(d)
        reg = tmp_path / "registry-store"
        reg.mkdir()
        assert duc.decide(str(m), str(tmp_path / "datasets-out"), str(reg)) == "TRAIN"

    def test_train_when_dataset_missing(self, tmp_path):
        m = _write_manifest(tmp_path)
        reg = _write_registry(tmp_path, run_at=_future_iso())
        assert duc.decide(str(m), str(tmp_path / "datasets-out"), str(reg)) == "TRAIN"

    def test_fail_open_on_garbage_manifest(self, tmp_path):
        p = tmp_path / "garbage.yaml"
        p.write_text(":::not yaml at all\n\t{")
        assert duc.decide(str(p), str(tmp_path), str(tmp_path)) == "TRAIN"

    def test_skip_regardless_of_glob_order_with_non_dict_sibling(self, tmp_path, monkeypatch):
        """Regression for a real bug (found investigating a CI-only failure of
        test_skip_when_dataset_and_manifest_predate_last_run, 2026-07-22):
        `glob.glob()` order is filesystem-dependent, not guaranteed sorted. The
        registry scan called `d.get("model_id")` on every parsed JSON file
        OUTSIDE the try/except that only guarded `json.load()` -- so when the
        non-dict sibling artifact (`not-an-entry.json`, a JSON list) happened to
        glob-order BEFORE the real dict entry, `d.get(...)` raised
        AttributeError, which the function-level fail-open silently swallowed
        into "TRAIN". This test pins the invariant the code already claimed
        ("a sibling non-entry artifact must not break the scan") by forcing
        BOTH glob orderings explicitly, instead of relying on whatever order
        the test's own filesystem happens to produce.
        """
        m = _write_manifest(tmp_path)
        d = _write_dataset(tmp_path)
        _set_old(m)
        _set_old(d)
        reg = _write_registry(tmp_path, run_at=_past_iso(days=0.5))
        real_glob = duc.glob.glob
        for reverse in (False, True):
            def _ordered_glob(*args, _reverse=reverse, **kwargs):
                results = real_glob(*args, **kwargs)
                return list(reversed(results)) if _reverse else results
            monkeypatch.setattr(duc.glob, "glob", _ordered_glob)
            assert duc.decide(str(m), str(tmp_path / "datasets-out"), str(reg)) == "SKIP"
