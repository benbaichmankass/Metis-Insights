"""Tests for computed promotion gates (ml.promotion.gates)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ml.promotion.attribution import ModelAttribution
from ml.promotion.gates import GateThresholds, evaluate_gates
from ml.promotion.oos_edge import OOSEdgeResult
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


def _good_oos_edge(edge: float = 0.02) -> OOSEdgeResult:
    return OOSEdgeResult(
        model_id="m", metric="mae", higher_is_better=False,
        candidate_score=0.05, baseline_score=0.05 + edge, edge=edge,
        n_folds=5, n_rows=2000,
        candidate_trainer="ml.trainers.lightgbm_regression.LightGBMRegressionTrainer",
        baseline_trainer="ml.trainers.constant_baseline.ConstantPredictionTrainer",
    )


def test_healthy_model_is_ready():
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(),
    )
    assert report.ready, report.to_dict()["blocking"]


def test_missing_oos_edge_blocks_on_insufficient_data():
    # No purged-WF-CV run → the offline champion-challenger gate cannot be
    # certified, so a model that's otherwise healthy is NOT ready.
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=None,
    )
    oe = next(r for r in report.results if r.name == "oos_edge")
    assert oe.status == "insufficient_data"
    assert not report.ready


def test_oos_edge_boundary_pass_fail():
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
    )
    th = GateThresholds(min_oos_edge=0.0)
    # Exactly at the threshold (edge == 0.0): no edge over baseline → fail.
    at = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(edge=0.0), thresholds=th,
    )
    assert next(r for r in at.results if r.name == "oos_edge").status == "fail"
    # Just above (edge strictly positive) → pass.
    above = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(edge=1e-4), thresholds=th,
    )
    assert next(r for r in above.results if r.name == "oos_edge").status == "pass"
    # A candidate that LOSES to the baseline (negative oriented edge) fails.
    below = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(edge=-0.01), thresholds=th,
    )
    assert next(r for r in below.results if r.name == "oos_edge").status == "fail"


def test_drift_ks_psi_numeric_boundary():
    entry = _entry(
        metrics={"macro_f1": 0.70, "f1_a": 0.73, "f1_b": 0.68, "n_eval": 5000},
        runs=_runs("macro_f1", [0.70, 0.71, 0.69]),
    )
    th = GateThresholds(max_ks=0.2, max_psi=0.25)

    class _Drift:
        def __init__(self, ks, psi):
            self.ks = ks
            self.psi = psi
            self.overall_verdict = "moderate"

    # KS exactly at the ceiling + PSI under → pass (≤ is acceptable).
    ok = evaluate_gates(
        entry, attribution=_good_attr(), oos_edge=_good_oos_edge(),
        drift=_Drift(ks=0.2, psi=0.10), thresholds=th,
    )
    assert next(r for r in ok.results if r.name == "drift_clean").status == "pass"
    # KS just over the ceiling → fail, even though the verdict bucket alone
    # might have been judged differently.
    bad_ks = evaluate_gates(
        entry, attribution=_good_attr(), oos_edge=_good_oos_edge(),
        drift=_Drift(ks=0.21, psi=0.10), thresholds=th,
    )
    assert next(r for r in bad_ks.results if r.name == "drift_clean").status == "fail"
    # PSI over the ceiling → fail.
    bad_psi = evaluate_gates(
        entry, attribution=_good_attr(), oos_edge=_good_oos_edge(),
        drift=_Drift(ks=0.05, psi=0.30), thresholds=th,
    )
    assert next(r for r in bad_psi.results if r.name == "drift_clean").status == "fail"


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


def test_imbalance_aware_path_passes_high_precision_minority():
    # Imbalanced (99% / 1%) regime label. F1 on minority caps low even when
    # precision is excellent and recall clears the floor — strict
    # min_class_f1 ≥ 0.3 unfairly fails this. The imbalance-aware alt
    # (precision lift over base rate + recall floor) recognises it.
    entry = _entry(
        metrics={
            "macro_f1": 0.45,
            "f1_majority": 0.99,
            "f1_minority": 0.20,
            "precision_majority": 0.99,
            "precision_minority": 0.60,    # base_rate=0.01 → lift = 60×
            "recall_majority": 0.99,
            "recall_minority": 0.10,       # clears default 0.05 floor
            "support_majority": 4950.0,
            "support_minority": 50.0,
            "n_eval": 5000,
        },
        runs=_runs("macro_f1", [0.45, 0.46, 0.44]),
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
    )
    nd = next(r for r in report.results if r.name == "non_degenerate")
    assert nd.status == "pass", nd.detail


def test_imbalance_aware_path_fails_when_recall_too_low():
    # Same imbalance but the model only fires on the easiest minority cases
    # — high precision, but recall under the 5% floor. Should still fail.
    entry = _entry(
        metrics={
            "macro_f1": 0.45,
            "f1_majority": 0.99,
            "f1_minority": 0.05,
            "precision_majority": 0.99,
            "precision_minority": 0.60,
            "recall_majority": 0.99,
            "recall_minority": 0.03,       # below 0.05 floor
            "support_majority": 4950.0,
            "support_minority": 50.0,
            "n_eval": 5000,
        },
        runs=_runs("macro_f1", [0.45, 0.46, 0.44]),
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
    )
    nd = next(r for r in report.results if r.name == "non_degenerate")
    assert nd.status == "fail", nd.detail
    assert "recall=0.030" in nd.detail


def test_imbalance_aware_path_fails_when_precision_no_lift():
    # Model predicts minority class but no better than random — precision
    # equals the base rate. Should fail the lift check.
    entry = _entry(
        metrics={
            "macro_f1": 0.10,
            "f1_majority": 0.99,
            "f1_minority": 0.02,
            "precision_majority": 0.99,
            "precision_minority": 0.01,    # base_rate = 0.01 → lift = 1.0
            "recall_majority": 0.99,
            "recall_minority": 0.10,
            "support_majority": 4950.0,
            "support_minority": 50.0,
            "n_eval": 5000,
        },
        runs=_runs("macro_f1", [0.10, 0.11, 0.09]),
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
    )
    nd = next(r for r in report.results if r.name == "non_degenerate")
    assert nd.status == "fail", nd.detail
    assert "precision_lift=1.00" in nd.detail


def test_always_majority_degenerate_still_fails_under_imbalance_path():
    # The original degenerate case (always-predict-majority) MUST still
    # fail — the new path doesn't relax that, it just stops punishing
    # genuinely-discriminating-but-imbalanced models.
    entry = _entry(
        metrics={
            "macro_f1": 0.50,
            "f1_majority": 0.99,
            "f1_minority": 0.00,
            "precision_majority": 0.99,
            "precision_minority": 0.00,
            "recall_majority": 1.00,
            "recall_minority": 0.00,       # collapsed — never fires
            "support_majority": 4950.0,
            "support_minority": 50.0,
            "n_eval": 5000,
        },
        runs=_runs("macro_f1", [0.50, 0.51, 0.49]),
    )
    report = evaluate_gates(
        entry, attribution=_good_attr(), drift={"overall_verdict": "no_change"},
    )
    nd = next(r for r in report.results if r.name == "non_degenerate")
    assert nd.status == "fail", nd.detail


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


# --- Regime-classifier gate profile (Tier-3, 2026-06-07) -------------------

from types import SimpleNamespace  # noqa: E402

from ml.promotion.gates import (  # noqa: E402
    REGIME_MIN_LIVE_TRADES,
    is_regime_classifier,
    regime_classifier_thresholds,
    thresholds_for,
)


def _thin_live_attr() -> ModelAttribution:
    # A regime head with only a handful of overlapping live trades, all wins,
    # so brier_lift is degenerate (base-rate brier 0.0) but rank-AUC is fine.
    return ModelAttribution(
        model_id="m", stage="shadow", n=REGIME_MIN_LIVE_TRADES, win_rate=0.6,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.62, brier=0.20, baseline_brier=0.0, brier_lift=None,
    )


def test_regime_profile_threshold_values():
    th = regime_classifier_thresholds()
    assert th.min_trades == REGIME_MIN_LIVE_TRADES
    assert th.require_beats_baseline is False
    # safety gates are untouched
    assert th.shadow_soak_days == 7.0
    assert th.min_oos_edge == 0.0
    assert th.min_auc == 0.55
    assert th.max_ks == 0.2 and th.max_psi == 0.25


def test_is_regime_classifier_detection():
    assert is_regime_classifier(
        SimpleNamespace(manifest={"dataset": {"family": "market_features"}}, metrics={})
    )
    assert is_regime_classifier(
        SimpleNamespace(manifest={"trainer_config": {"target_column": "regime_label"}}, metrics={})
    )
    assert is_regime_classifier(
        SimpleNamespace(manifest={}, metrics={"f1_range": 0.5, "f1_volatile": 0.4})
    )
    # a single-probability decision model is NOT a regime head
    assert not is_regime_classifier(
        SimpleNamespace(manifest={"dataset": {"family": "trade_outcomes"}}, metrics={"brier": 0.2})
    )


def test_thresholds_for_selection():
    regime = SimpleNamespace(manifest={}, metrics={"f1_a": 0.5, "f1_b": 0.4})
    decision = SimpleNamespace(manifest={}, metrics={"brier": 0.2})
    assert thresholds_for(regime).min_trades == REGIME_MIN_LIVE_TRADES  # auto
    assert thresholds_for(decision).min_trades == 200  # auto → decision default
    assert thresholds_for(regime, regime=False).min_trades == 200  # forced default
    assert thresholds_for(decision, regime=True).min_trades == REGIME_MIN_LIVE_TRADES
    override = GateThresholds(min_trades=42)
    assert thresholds_for(regime, override=override).min_trades == 42  # override wins


def test_regime_profile_ready_on_small_live_floor():
    entry = _entry(
        metrics={"macro_f1": 0.66, "f1_range": 0.73, "f1_volatile": 0.48, "n_eval": 8760},
        runs=_runs("macro_f1", [0.66, 0.66, 0.66]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_thin_live_attr(),
        drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(),
        # option A (2026-06-26): the regime profile now requires the RG4 live
        # regime-discrimination AUC instead of the trade-outcome live_agreement.
        live_regime_auc=0.72,
        thresholds=regime_classifier_thresholds(),
    )
    assert report.ready, report.to_dict()["blocking"]
    bb = next(r for r in report.results if r.name == "beats_baseline")
    assert bb.required is False  # oos_edge carries the beats-baseline role
    ss = next(r for r in report.results if r.name == "sample_sufficiency")
    assert ss.status == "pass" and ss.threshold == float(REGIME_MIN_LIVE_TRADES)


def test_default_profile_blocks_same_thin_model():
    # The identical thin-live-trade regime head is NOT ready under the
    # decision-model profile: it fails the 200-trade floor and the required
    # live beats_baseline.
    entry = _entry(
        metrics={"macro_f1": 0.66, "f1_range": 0.73, "f1_volatile": 0.48, "n_eval": 8760},
        runs=_runs("macro_f1", [0.66, 0.66, 0.66]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_thin_live_attr(),
        drift={"overall_verdict": "no_change"}, oos_edge=_good_oos_edge(),
    )  # default thresholds
    assert not report.ready
    blocking = {r.name for r in report.blocking}
    assert "sample_sufficiency" in blocking
    assert "beats_baseline" in blocking


# --- Live regime-discrimination gate (RG4, option A, 2026-06-26) -----------

from ml.promotion.gates import _gate_live_regime_discrimination  # noqa: E402


def test_live_regime_discrimination_none_is_insufficient():
    th = GateThresholds()
    r = _gate_live_regime_discrimination(None, th)
    assert r.status == "insufficient_data"
    assert "RG4 live-row regime AUC" in r.detail
    assert r.threshold == th.min_live_regime_auc


def test_live_regime_discrimination_pass_above_default():
    r = _gate_live_regime_discrimination(0.60, GateThresholds())
    assert r.status == "pass"
    assert r.value == 0.60
    assert r.threshold == 0.55


def test_live_regime_discrimination_fail_below_default():
    r = _gate_live_regime_discrimination(0.40, GateThresholds())
    assert r.status == "fail"
    assert r.value == 0.40


def test_live_regime_discrimination_required_flag_honored():
    # default profile: not required; regime profile: required.
    assert _gate_live_regime_discrimination(0.60, GateThresholds()).required is False
    assert _gate_live_regime_discrimination(
        0.60, regime_classifier_thresholds()
    ).required is True


def test_regime_profile_swaps_live_track_record_gates():
    # regime profile: live_agreement NOT required, live_regime_discrimination
    # required; default profile: the reverse.
    reg = regime_classifier_thresholds()
    assert reg.require_live_agreement is False
    assert reg.require_live_regime_discrimination is True
    d = GateThresholds()
    assert d.require_live_agreement is True
    assert d.require_live_regime_discrimination is False


def test_regime_ready_depends_on_live_regime_not_trade_agreement():
    # A regime head whose trade-outcome live_agreement is degenerate but whose
    # RG4 live regime AUC clears the bar IS ready under the regime profile.
    entry = _entry(
        metrics={"macro_f1": 0.66, "f1_range": 0.73, "f1_volatile": 0.48, "n_eval": 8760},
        runs=_runs("macro_f1", [0.66, 0.66, 0.66]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_thin_live_attr(),
        drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=0.72,
        thresholds=regime_classifier_thresholds(),
    )
    assert report.ready, report.to_dict()["blocking"]
    la = next(r for r in report.results if r.name == "live_agreement")
    assert la.required is False  # not blocking under the regime profile
    lrd = next(r for r in report.results if r.name == "live_regime_discrimination")
    assert lrd.status == "pass" and lrd.required is True


def test_regime_blocks_on_low_live_regime_auc_not_live_agreement():
    entry = _entry(
        metrics={"macro_f1": 0.66, "f1_range": 0.73, "f1_volatile": 0.48, "n_eval": 8760},
        runs=_runs("macro_f1", [0.66, 0.66, 0.66]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_thin_live_attr(),
        drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=0.40,
        thresholds=regime_classifier_thresholds(),
    )
    assert not report.ready
    blocking = {r.name for r in report.blocking}
    assert "live_regime_discrimination" in blocking
    assert "live_agreement" not in blocking
