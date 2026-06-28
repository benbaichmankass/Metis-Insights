"""Tests for the stage-guard proposal generator (ml.promotion.stage_guard)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.promotion.attribution import ModelAttribution
from ml.promotion.oos_edge import OOSEdgeResult
from ml.promotion.stage_guard import propose_for_model, run_stage_guard
from ml.registry.model_registry import ModelRegistry, RegistryEntry, RunRecord


def _good_oos_edge():
    return OOSEdgeResult(
        model_id="m", metric="mae", higher_is_better=False,
        candidate_score=0.05, baseline_score=0.07, edge=0.02,
        n_folds=5, n_rows=2000,
        candidate_trainer="ml.trainers.lightgbm_regression.LightGBMRegressionTrainer",
        baseline_trainer="ml.trainers.constant_baseline.ConstantPredictionTrainer",
    )


def _runs(vals):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return tuple(
        RunRecord(run_id=f"r{i}", model_state_path="x",
                  metrics={"macro_f1": v}, code_revision="a", at=base + timedelta(days=i))
        for i, v in enumerate(vals)
    )


def _entry(stage, *, metrics=None, runs=(), created_days_ago=14.0):
    return RegistryEntry(
        model_id="m", status="candidate", manifest={"model_id": "m"},
        model_state_path="x",
        metrics=metrics or {"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        code_revision="a",
        created_at=datetime.now(timezone.utc) - timedelta(days=created_days_ago),
        target_deployment_stage=stage, runs=runs,
    )


def _good_attr():
    return ModelAttribution(
        model_id="m", stage="shadow", n=300, win_rate=0.5,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.62, brier=0.20, baseline_brier=0.25, brier_lift=0.05,
    )


def test_shadow_ready_proposes_promote():
    # The default `_entry` carries 2 per-class f1_* metrics, so it auto-detects
    # as a regime head. Option A (2026-06-26): a regime head's required live
    # gate is now `live_regime_discrimination` (RG4); supply a passing AUC so
    # it is promote-ready.
    p = propose_for_model(
        _entry("shadow", runs=_runs([0.70, 0.71, 0.69])),
        attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=0.72,
    )
    assert p.action == "promote"
    assert p.proposed_stage == "advisory"


def test_shadow_regime_holds_without_live_regime_auc():
    # The regime profile now requires the RG4 live regime-discrimination AUC.
    # The stage-guard SWEEP passes None for it (per-model candle resolution is
    # a separate follow-up), so an otherwise-healthy regime head holds on the
    # new gate rather than promoting on the (off) trade-outcome live_agreement.
    p = propose_for_model(
        _entry("shadow", runs=_runs([0.70, 0.71, 0.69])),
        attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=None,
    )
    assert p.action == "hold"
    assert any("live_regime_discrimination" in r for r in p.reasons)


def test_shadow_without_oos_edge_holds():
    # Otherwise-healthy shadow model with no purged-WF-CV evidence holds on
    # the offline champion-challenger gate.
    p = propose_for_model(
        _entry("shadow", runs=_runs([0.70, 0.71, 0.69])),
        attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=None,
    )
    assert p.action == "hold"
    assert any("oos_edge" in r for r in p.reasons)


def test_shadow_not_ready_holds():
    p = propose_for_model(_entry("shadow"), attribution=None, drift=None)
    assert p.action == "hold"
    assert p.proposed_stage is None
    assert p.reasons  # carries the blocking gate names


def test_advisory_healthy_holds():
    attr = ModelAttribution(
        model_id="m", stage="advisory", n=300, win_rate=0.55,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.6, brier=0.2, baseline_brier=0.25, brier_lift=0.05,
    )

    class _D:
        overall_verdict = "minor"

    p = propose_for_model(_entry("advisory"), attribution=attr, drift=_D())
    assert p.action == "hold"


def test_advisory_underperformance_proposes_demote():
    bad = ModelAttribution(
        model_id="m", stage="advisory", n=300, win_rate=0.4,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.42, brier=0.30, baseline_brier=0.24, brier_lift=-0.06,
    )
    p = propose_for_model(_entry("advisory"), attribution=bad, drift=None)
    assert p.action == "demote"
    assert p.proposed_stage == "shadow"
    assert any("inverted" in r or "base rate" in r for r in p.reasons)


def test_advisory_significant_drift_proposes_demote():
    class _D:
        overall_verdict = "significant"

    healthy = _good_attr()
    p = propose_for_model(_entry("advisory"), attribution=healthy, drift=_D())
    assert p.action == "demote"


def test_pre_shadow_holds():
    p = propose_for_model(_entry("research_only"), attribution=None, drift=None)
    assert p.action == "hold"


def _seed_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL, "
        "pnl_percent REAL, status TEXT, timestamp TEXT, notes TEXT, "
        "is_backtest INT, is_demo INT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (id INTEGER PRIMARY KEY, linked_trade_id INT, "
        "updated_at TEXT)"
    )
    conn.commit()
    conn.close()


def test_run_stage_guard_end_to_end(tmp_path: Path):
    reg_root = tmp_path / "registry-store"
    registry = ModelRegistry(reg_root)
    registry.register(
        model_id="m", manifest={"model_id": "m", "target_deployment_stage": "shadow"},
        model_state_path="x", metrics={"macro_f1": 0.7}, code_revision="a",
    )
    db = tmp_path / "j.db"
    _seed_db(db)
    log = tmp_path / "shadow.jsonl"
    log.write_text("")
    proposals = run_stage_guard(
        registry_root=reg_root, db_path=db, shadow_log=log,
    )
    assert len(proposals) == 1
    # No live data + freshly registered → not promotable → hold.
    assert proposals[0].action == "hold"
