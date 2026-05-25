"""Tests for computed promotion gates (ml.promotion.gates)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ml.promotion.attribution import ModelAttribution
from ml.promotion.gates import GateThresholds, evaluate_gates
from ml.registry.model_registry import RegistryEntry, RunRecord, StageEvent


def _runs(metric_key: str, vals: list[float]) -> tuple[RunRecord, ...]:
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return tuple(
        RunRecord(
            run_id=f"r{i}",
            model_state_path="x",
            metrics={metric_key: v},
            code_revision="abc",
            at=base + timedelta(days=i),
        )
        for i, v in enumerate(vals)
    )


def _entry(
    *,
    metrics: dict,
    stage: str = "shadow",
    created_days_ago: float = 10.0,
    runs=(),
    stage_history=(),
) -> RegistryEntry:
    created = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    return RegistryEntry(
        model_id="m",
        status="candidate",
        manifest={"model_id": "m"},
        model_state_path="x",
        metrics=metrics,
        code_revision="abc",
        created_at=created,
        target_deployment_stage=stage,
        runs=runs,
        stage_history=stage_history,
    )


def _good_attr() -> ModelAttribution:
    return ModelAttribution(
        model_id="m", stage="shadow", n=300, win_rate=0.5,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.62, brier=0.20, baseline_brier=0.25, brier_lift=0.05,
    )


def test_healthy_model_is_ready():
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
    )
    assert report.ready, report.to_dict()["blocking"]


def test_degenerate_class_f1_fails_non_degenerate():
    entry = _entry(
        metrics={"macro_f1": 0.40, "f1_a": 0.80, "f1_b": 0.0, "n_eval": 5000},
        runs=_runs("macro_f1", [0.40, 0.41, 0.39]),
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
    )
    assert not report.ready
    nd = next(r for r in report.results if r.name == "non_degenerate")
    assert nd.status == "fail"


def test_missing_attribution_blocks_on_insufficient_data():
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
    )
    report = evaluate_gates(entry, attribution=None, drift=None)
    assert not report.ready
    statuses = {r.name: r.status for r in report.results}
    assert statuses["beats_baseline"] == "insufficient_data"
    assert statuses["live_agreement"] == "insufficient_data"
    assert statuses["drift_clean"] == "insufficient_data"


def test_soak_fails_when_recently_entered_shadow():
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
        stage_history=(
            StageEvent(
                from_stage="research_only", to_stage="shadow",
                by="t", reason="x", at=recent,
            ),
        ),
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
    )
    soak = next(r for r in report.results if r.name == "shadow_soak")
    assert soak.status == "fail"


def test_low_auc_fails_live_agreement():
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
    )
    attr = ModelAttribution(
        model_id="m", stage="shadow", n=300, win_rate=0.5,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.51, brier=0.20, baseline_brier=0.25, brier_lift=0.05,
    )
    report = evaluate_gates(
        entry, attribution=attr, drift={"overall_verdict": "no_change"},
        thresholds=GateThresholds(min_auc=0.55),
    )
    la = next(r for r in report.results if r.name == "live_agreement")
    assert la.status == "fail"


def test_drift_significant_fails_drift_clean():
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "significant"},
    )
    dc = next(r for r in report.results if r.name == "drift_clean")
    assert dc.status == "fail"
    assert not report.ready
