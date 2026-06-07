"""Tests for the S-MLOPT-S16 drift-triggered retrain orchestrator (`ml.shadow.drift_retrain`)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ml.cli import main as cli_main
from ml.registry.model_registry import ModelRegistry
from ml.shadow.drift_retrain import evaluate_models, write_log


def _seed_registry(tmp_path: Path, stage: str = "shadow") -> Path:
    reg_root = tmp_path / "registry-store"
    registry = ModelRegistry(reg_root)
    registry.register(
        model_id="drift-head",
        manifest={"model_id": "drift-head", "target_deployment_stage": stage},
        model_state_path="x", metrics={"macro_f1": 0.6}, code_revision="a",
    )
    return reg_root


def _seed_configs(tmp_path: Path) -> Path:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "drift-head.yaml").write_text(yaml.safe_dump({
        "model_id": "drift-head",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "dataset": {"family": "market_features", "path": "x", "version": "v1"},
    }))
    return configs


def _write_shadow_log(log_path: Path, model_id: str, scores: list[float]) -> None:
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with log_path.open("w") as fh:
        for i, score in enumerate(scores):
            fh.write(json.dumps({
                "predicted_at_utc": (base + timedelta(minutes=i)).isoformat(),
                "model_id": model_id,
                "stage": "shadow",
                "score": score,
                "row_keys": ["symbol"],
            }) + "\n")


def test_evaluate_models_dispatches_on_step_change(tmp_path: Path):
    reg = _seed_registry(tmp_path)
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    # Sharp step from 0.1 → 0.9 — must trip ADWIN.
    _write_shadow_log(log, "drift-head", [0.1] * 200 + [0.9] * 200)
    decisions = evaluate_models(
        registry_root=reg,
        shadow_log=log,
        configs_root=cfg,
    )
    assert len(decisions) == 1
    d = decisions[0]
    assert d.model_id == "drift-head"
    assert d.action == "dispatch"
    assert d.drift_detected is True
    assert d.manifest_path is not None
    assert d.manifest_path.endswith("drift-head.yaml")


def test_evaluate_models_skip_no_drift_on_stationary(tmp_path: Path):
    reg = _seed_registry(tmp_path)
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    _write_shadow_log(log, "drift-head", [0.5] * 300)
    decisions = evaluate_models(
        registry_root=reg, shadow_log=log, configs_root=cfg,
    )
    assert decisions[0].action == "skip_no_drift"
    assert decisions[0].drift_detected is False


def test_evaluate_models_skip_thin_data_when_no_records(tmp_path: Path):
    reg = _seed_registry(tmp_path)
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    log.write_text("")  # no scored predictions yet
    decisions = evaluate_models(
        registry_root=reg, shadow_log=log, configs_root=cfg,
    )
    assert decisions[0].action == "skip_thin_data"
    assert decisions[0].n_observations == 0


def test_evaluate_models_skip_no_manifest_when_glob_misses(tmp_path: Path):
    reg = _seed_registry(tmp_path)
    cfg = tmp_path / "empty_configs"
    cfg.mkdir()  # no yaml file present
    log = tmp_path / "shadow.jsonl"
    _write_shadow_log(log, "drift-head", [0.1] * 200 + [0.9] * 200)
    decisions = evaluate_models(
        registry_root=reg, shadow_log=log, configs_root=cfg,
    )
    assert decisions[0].action == "skip_no_manifest"
    assert decisions[0].drift_detected is True  # drift IS detected, just unactionable


def test_pre_shadow_stage_is_ignored(tmp_path: Path):
    # research_only models aren't on the live evaluation path; drift on
    # them doesn't justify a retrain (and they typically don't even emit
    # shadow predictions).
    reg = _seed_registry(tmp_path, stage="research_only")
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    _write_shadow_log(log, "drift-head", [0.1] * 200 + [0.9] * 200)
    decisions = evaluate_models(
        registry_root=reg, shadow_log=log, configs_root=cfg,
    )
    assert decisions == []


def test_backfill_records_excluded_from_detector(tmp_path: Path):
    # Backfill rows carry synthetic timestamps; mixing them into the
    # online detector would let a retroactive replay artificially trip
    # drift. Confirm a head whose ONLY records are backfill rows lands
    # as skip_thin_data, not skip_no_drift / dispatch.
    reg = _seed_registry(tmp_path)
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with log.open("w") as fh:
        for i in range(400):
            fh.write(json.dumps({
                "predicted_at_utc": (base + timedelta(minutes=i)).isoformat(),
                "model_id": "drift-head",
                "stage": "shadow",
                "score": 0.1 if i < 200 else 0.9,
                "row_keys": ["symbol"],
                "backfill_kind": "retroactive_decision",
                "trade_id": str(i),
            }) + "\n")
    decisions = evaluate_models(
        registry_root=reg, shadow_log=log, configs_root=cfg,
    )
    assert decisions[0].action == "skip_thin_data"
    assert decisions[0].n_observations == 0


def test_write_log_appends_jsonl(tmp_path: Path):
    reg = _seed_registry(tmp_path)
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    _write_shadow_log(log, "drift-head", [0.5] * 100)
    decisions = evaluate_models(
        registry_root=reg, shadow_log=log, configs_root=cfg,
    )
    out_log = tmp_path / "drift_retrain.jsonl"
    written = write_log(decisions, out_log,
                        now_utc=datetime(2026, 6, 7, tzinfo=timezone.utc))
    assert written == 1
    rows = [json.loads(line) for line in out_log.read_text().splitlines()]
    assert rows[0]["model_id"] == "drift-head"
    assert rows[0]["ts"].startswith("2026-06-07")
    # A second call appends rather than truncates.
    written2 = write_log(decisions, out_log,
                         now_utc=datetime(2026, 6, 8, tzinfo=timezone.utc))
    assert written2 == 1
    assert len(out_log.read_text().splitlines()) == 2


def test_drift_retrain_cli_returns_11_on_dispatch(tmp_path: Path, capsys):
    reg = _seed_registry(tmp_path)
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    _write_shadow_log(log, "drift-head", [0.1] * 200 + [0.9] * 200)
    rc = cli_main([
        "drift-retrain",
        "--registry-root", str(reg),
        "--configs-root", str(cfg),
        "--shadow-log", str(log),
    ])
    assert rc == 11
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["dispatch"] == ["drift-head"]


def test_drift_retrain_cli_returns_0_when_quiet(tmp_path: Path, capsys):
    reg = _seed_registry(tmp_path)
    cfg = _seed_configs(tmp_path)
    log = tmp_path / "shadow.jsonl"
    _write_shadow_log(log, "drift-head", [0.5] * 300)
    rc = cli_main([
        "drift-retrain",
        "--registry-root", str(reg),
        "--configs-root", str(cfg),
        "--shadow-log", str(log),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["dispatch"] == []
