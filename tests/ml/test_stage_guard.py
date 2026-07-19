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


def _entry(stage, *, metrics=None, runs=(), created_days_ago=14.0, manifest=None):
    return RegistryEntry(
        model_id="m", status="candidate", manifest=manifest or {"model_id": "m"},
        model_state_path="x",
        metrics=metrics or {"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        code_revision="a",
        created_at=datetime.now(timezone.utc) - timedelta(days=created_days_ago),
        target_deployment_stage=stage, runs=runs,
    )


def _regime_entry(stage, *, runs=(), created_days_ago=14.0):
    """A multiclass regime-classifier head (model_id mirrors the live
    `btc-regime-15m-lgbm-v2`). Classified as regime by the manifest dataset
    family (`market_features`) — the precise path `is_regime_classifier` prefers."""
    return RegistryEntry(
        model_id="btc-regime-15m-lgbm-v2", status="candidate",
        manifest={"model_id": "btc-regime-15m-lgbm-v2",
                  "dataset": {"family": "market_features"}},
        model_state_path="x",
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        code_revision="a",
        created_at=datetime.now(timezone.utc) - timedelta(days=created_days_ago),
        target_deployment_stage=stage, runs=runs,
    )


def _decision_entry(stage, *, runs=(), created_days_ago=14.0):
    """A trade-outcome decision head: a SINGLE-class metric shape (no ≥2
    `f1_*` keys) so `is_regime_classifier` returns False — the family for
    which the trade-win brier_lift/auc demote axis IS correct."""
    return RegistryEntry(
        model_id="trade-outcome-lgbm-v1", status="candidate",
        manifest={"model_id": "trade-outcome-lgbm-v1"},
        model_state_path="x",
        metrics={"brier": 0.20, "accuracy": 0.62, "n_eval": 5000},
        code_revision="a",
        created_at=datetime.now(timezone.utc) - timedelta(days=created_days_ago),
        target_deployment_stage=stage, runs=runs,
    )


def _bad_trade_win_attr(stage="advisory"):
    """Attribution that is bad ONLY on the trade-win axis (negative brier_lift
    + inverted AUC), healthy elsewhere (live score spread is wide, not
    collapsed)."""
    return ModelAttribution(
        model_id="m", stage=stage, n=15, win_rate=0.4,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.42, brier=0.30, baseline_brier=0.24, brier_lift=-0.16256,
    )


def _good_attr():
    return ModelAttribution(
        model_id="m", stage="shadow", n=300, win_rate=0.5,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.62, brier=0.20, baseline_brier=0.25, brier_lift=0.05,
    )


def _good_parity():
    from ml.promotion.live_parity import LiveParityResult

    return LiveParityResult(
        model_id="m", n_live_rows=120, n_sampled=50, n_mismatched=0,
        train_available=True,
    )


def _good_labels():
    from ml.promotion.live_parity import LabelsAccruingResult

    return LabelsAccruingResult(model_id="m", n_live_rows=120, n_labeled=80)


def test_shadow_ready_proposes_promote():
    # The default `_entry` carries 2 per-class f1_* metrics, so it auto-detects
    # as a regime head. M25 gate reframe (operator-approved 2026-07-19,
    # docs/research/M25-promotion-consolidation-DESIGN.md § "The promotion gate
    # — REFRAMED 2026-07-19"): a regime head's required LIVE gates are the
    # serving-mechanics pair `live_parity` + `labels_accruing`; supply passing
    # results so it is promote-ready. `live_regime_auc` is advisory now.
    p = propose_for_model(
        _entry("shadow", runs=_runs([0.70, 0.71, 0.69])),
        attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=0.72,
        live_parity=_good_parity(), labels_accruing=_good_labels(),
    )
    assert p.action == "promote"
    assert p.proposed_stage == "advisory"


def test_shadow_regime_holds_without_mechanics_evidence():
    # M25 reframe (2026-07-19): the regime profile requires the deterministic
    # serving-mechanics gates (`live_parity` + `labels_accruing`). The
    # stage-guard SWEEP passes None for them (per-model candle/dataset
    # resolution is a separate follow-up), so an otherwise-healthy regime head
    # holds on the mechanics gates — while the demoted-to-advisory
    # `live_regime_discrimination` no longer blocks.
    p = propose_for_model(
        _entry("shadow", runs=_runs([0.70, 0.71, 0.69])),
        attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=None,
    )
    assert p.action == "hold"
    assert any("live_parity" in r for r in p.reasons)
    assert any("labels_accruing" in r for r in p.reasons)
    assert not any("live_regime_discrimination" in r for r in p.reasons)


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
    # A DECISION/outcome head whose live score IS a win-probability: the
    # trade-win brier_lift/auc axis is correct, so live underperformance
    # demotes. (Uses a decision-model entry — `_entry`'s default 2-class F1
    # shape would auto-classify as a regime head, for which this axis is
    # suppressed; see test_advisory_regime_trade_win_*.)
    bad = ModelAttribution(
        model_id="m", stage="advisory", n=300, win_rate=0.4,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.42, brier=0.30, baseline_brier=0.24, brier_lift=-0.06,
    )
    p = propose_for_model(_decision_entry("advisory"), attribution=bad, drift=None)
    assert p.action == "demote"
    assert p.proposed_stage == "shadow"
    assert any("inverted" in r or "base rate" in r for r in p.reasons)


def test_advisory_regime_trade_win_brier_lift_does_not_demote():
    # MB-20260630-001: a REGIME head (e.g. btc-regime-15m-lgbm-v2) with a
    # negative trade-win brier_lift AND inverted trade-win AUC must NOT demote
    # — those are the wrong axis for a head that predicts the regime, not the
    # trade outcome. With no drift-significant / score-collapse / RG4-collapse
    # signal it falls through to HOLD (matching Option A on the promote side).
    p = propose_for_model(
        _regime_entry("advisory"), attribution=_bad_trade_win_attr(), drift=None,
    )
    assert p.action == "hold"
    assert p.proposed_stage is None
    # The suppressed trade-win reasons must be absent from the proposal.
    assert not any("base rate" in r or "inverted" in r for r in p.reasons)


def test_advisory_decision_trade_win_brier_lift_still_demotes():
    # The SAME bad trade-win attribution on a DECISION/outcome head (where the
    # brier_lift axis IS right) still demotes on the base-rate trigger —
    # confirming the suppression is family-scoped, not a blanket change.
    p = propose_for_model(
        _decision_entry("advisory"), attribution=_bad_trade_win_attr(), drift=None,
    )
    assert p.action == "demote"
    assert p.proposed_stage == "shadow"
    assert any("base rate" in r for r in p.reasons)


def test_advisory_regime_still_demotes_on_significant_drift():
    # MB-20260630-001: suppressing the trade-win axis must NOT disarm the
    # regime-APPROPRIATE demote signals. A regime head with significant
    # score-distribution drift still demotes.
    class _D:
        overall_verdict = "significant"

    p = propose_for_model(
        _regime_entry("advisory"), attribution=_bad_trade_win_attr(), drift=_D(),
    )
    assert p.action == "demote"
    assert p.proposed_stage == "shadow"
    assert any("drift" in r for r in p.reasons)


def test_advisory_regime_still_demotes_on_collapsed_score():
    # A regime head whose live score OUTPUT has collapsed (spread ~0) still
    # demotes — that trigger is axis-independent (degenerate output), so the
    # MB-20260630-001 suppression leaves it intact.
    collapsed = ModelAttribution(
        model_id="btc-regime-15m-lgbm-v2", stage="advisory", n=15, win_rate=0.5,
        score_mean=0.5, score_min=0.5, score_max=0.5,
        auc=None, brier=None, baseline_brier=None, brier_lift=None,
    )
    p = propose_for_model(
        _regime_entry("advisory"), attribution=collapsed, drift=None,
    )
    assert p.action == "demote"
    assert any("collapsed" in r for r in p.reasons)


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
