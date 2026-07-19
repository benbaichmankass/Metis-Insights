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


def _good_parity(**overrides):
    from ml.promotion.live_parity import LiveParityResult

    kwargs = dict(
        model_id="m", n_live_rows=120, n_sampled=50, n_mismatched=0,
        train_available=True,
    )
    kwargs.update(overrides)
    return LiveParityResult(**kwargs)


def _good_labels(**overrides):
    from ml.promotion.live_parity import LabelsAccruingResult

    kwargs = dict(model_id="m", n_live_rows=120, n_labeled=80)
    kwargs.update(overrides)
    return LabelsAccruingResult(**kwargs)


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
        live_regime_auc=0.72,
        # M25 gate reframe (operator-approved 2026-07-19): the regime
        # profile's required LIVE gates are the serving-mechanics pair.
        live_parity=_good_parity(), labels_accruing=_good_labels(),
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
    # M25 gate reframe (operator-approved 2026-07-19,
    # docs/research/M25-promotion-consolidation-DESIGN.md § "The promotion
    # gate — REFRAMED 2026-07-19"): advisory reporting under EVERY stock
    # profile — the soak proves mechanics, not edge. A custom GateThresholds
    # can still opt back in.
    assert _gate_live_regime_discrimination(0.60, GateThresholds()).required is False
    assert _gate_live_regime_discrimination(
        0.60, regime_classifier_thresholds()
    ).required is False
    from dataclasses import replace as _replace

    opt_in = _replace(GateThresholds(), require_live_regime_discrimination=True)
    assert _gate_live_regime_discrimination(0.60, opt_in).required is True


def test_regime_profile_swaps_live_track_record_gates():
    # M25 reframe (2026-07-19): regime profile — live_agreement NOT required,
    # live_regime_discrimination ADVISORY (not required), and the
    # serving-mechanics pair (live_parity + labels_accruing) required.
    # Default profile: live_agreement required; everything new not required.
    reg = regime_classifier_thresholds()
    assert reg.require_live_agreement is False
    assert reg.require_live_regime_discrimination is False
    assert reg.require_live_parity is True
    assert reg.require_labels_accruing is True
    d = GateThresholds()
    assert d.require_live_agreement is True
    assert d.require_live_regime_discrimination is False
    assert d.require_live_parity is False
    assert d.require_labels_accruing is False


def test_regime_ready_depends_on_mechanics_not_trade_agreement():
    # A regime head whose trade-outcome live_agreement is degenerate but whose
    # serving-mechanics gates pass IS ready under the regime profile (M25
    # reframe 2026-07-19). The RG4 AUC is still REPORTED, advisory.
    entry = _entry(
        metrics={"macro_f1": 0.66, "f1_range": 0.73, "f1_volatile": 0.48, "n_eval": 8760},
        runs=_runs("macro_f1", [0.66, 0.66, 0.66]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_thin_live_attr(),
        drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=0.72,
        live_parity=_good_parity(), labels_accruing=_good_labels(),
        thresholds=regime_classifier_thresholds(),
    )
    assert report.ready, report.to_dict()["blocking"]
    la = next(r for r in report.results if r.name == "live_agreement")
    assert la.required is False  # not blocking under the regime profile
    lrd = next(r for r in report.results if r.name == "live_regime_discrimination")
    assert lrd.status == "pass" and lrd.required is False  # advisory, still reported


def test_regime_low_live_regime_auc_reported_but_not_blocking():
    # M25 reframe (2026-07-19): a LOW RG4 AUC no longer blocks promotion — it
    # is advisory reporting (an outcome-statistics gate that takes weeks to
    # power in calm regimes). Mechanics gates passing → still ready; the fail
    # is visible in the report for the operator's judgement.
    entry = _entry(
        metrics={"macro_f1": 0.66, "f1_range": 0.73, "f1_volatile": 0.48, "n_eval": 8760},
        runs=_runs("macro_f1", [0.66, 0.66, 0.66]),
        created_days_ago=14,
    )
    report = evaluate_gates(
        entry, attribution=_thin_live_attr(),
        drift={"overall_verdict": "no_change"},
        oos_edge=_good_oos_edge(), live_regime_auc=0.40,
        live_parity=_good_parity(), labels_accruing=_good_labels(),
        thresholds=regime_classifier_thresholds(),
    )
    assert report.ready, report.to_dict()["blocking"]
    lrd = next(r for r in report.results if r.name == "live_regime_discrimination")
    assert lrd.status == "fail" and lrd.required is False
    blocking = {r.name for r in report.blocking}
    assert "live_regime_discrimination" not in blocking
    assert "live_agreement" not in blocking


def test_regime_blocks_without_mechanics_evidence():
    # M25 reframe (2026-07-19): the regime profile blocks on the REQUIRED
    # serving-mechanics gates when no evidence was computed — never a silent
    # pass on missing mechanics proof.
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
    assert not report.ready
    blocking = {r.name for r in report.blocking}
    assert "live_parity" in blocking
    assert "labels_accruing" in blocking


# --- Serving-mechanics gates (M25 gate reframe, operator-approved 2026-07-19,
# docs/research/M25-promotion-consolidation-DESIGN.md § "The promotion gate —
# REFRAMED 2026-07-19") -------------------------------------------------------

from ml.promotion.gates import (  # noqa: E402
    _gate_labels_accruing,
    _gate_live_parity,
)
from ml.promotion.live_parity import (  # noqa: E402
    dead_features,
    labels_accruing_from_counts,
    score_fidelity,
)


def test_live_parity_pass():
    r = _gate_live_parity(_good_parity(), regime_classifier_thresholds())
    assert r.status == "pass"
    assert r.required is True
    assert r.value == 0.0


def test_live_parity_fidelity_mismatch_fail():
    # 3/50 = 6% > the 2% floor → fail.
    r = _gate_live_parity(
        _good_parity(n_mismatched=3), regime_classifier_thresholds()
    )
    assert r.status == "fail"
    assert "serving-fidelity mismatch 3/50" in r.detail


def test_live_parity_mismatch_boundary():
    # Exactly at the floor (1/50 = 2%) → not ABOVE it → pass.
    r = _gate_live_parity(
        _good_parity(n_mismatched=1), regime_classifier_thresholds()
    )
    assert r.status == "pass"


def test_live_parity_dead_live_feature_fail_names_feature():
    # The ETH-xa class: xa_* constant/zero on the LIVE side, varying in
    # training (BL-20260628-XA-TRAINING-ZERO's mirror image).
    r = _gate_live_parity(
        _good_parity(dead_live_features=("xa_btc_ret_1h",)),
        regime_classifier_thresholds(),
    )
    assert r.status == "fail"
    assert "xa_btc_ret_1h" in r.detail
    assert "dead-on-LIVE" in r.detail


def test_live_parity_dead_train_feature_fail_names_feature():
    # The reverse: a feature the training build zeroed but the live pipeline
    # populates — the model learned nothing from it.
    r = _gate_live_parity(
        _good_parity(dead_train_features=("xa_eth_vol_1h",)),
        regime_classifier_thresholds(),
    )
    assert r.status == "fail"
    assert "xa_eth_vol_1h" in r.detail
    assert "dead-in-TRAINING" in r.detail


def test_live_parity_min_rows_insufficient():
    r = _gate_live_parity(
        _good_parity(n_live_rows=10, n_sampled=10),
        regime_classifier_thresholds(),
    )
    assert r.status == "insufficient_data"
    assert "10 live rows" in r.detail


def test_live_parity_error_is_insufficient_not_pass():
    # Fail-safe direction: a compute ERROR (model load failure, unreadable
    # log) surfaces as insufficient_data with the error in the detail — never
    # a silent pass, never a crash.
    r = _gate_live_parity(
        _good_parity(error="model artifact load failed: boom"),
        regime_classifier_thresholds(),
    )
    assert r.status == "insufficient_data"
    assert "boom" in r.detail


def test_live_parity_train_unavailable_is_insufficient():
    r = _gate_live_parity(
        _good_parity(train_available=False), regime_classifier_thresholds()
    )
    assert r.status == "insufficient_data"
    assert "training dataset unavailable" in r.detail


def test_live_parity_none_is_insufficient():
    r = _gate_live_parity(None, regime_classifier_thresholds())
    assert r.status == "insufficient_data"


def test_labels_accruing_pass():
    r = _gate_labels_accruing(_good_labels(), regime_classifier_thresholds())
    assert r.status == "pass"
    assert r.required is True


def test_labels_accruing_fail_with_fraction_in_detail():
    # The MES stale-candle-base class: 1213/1861 unlabeled → 0.35 clears the
    # 0.30 floor, but a harder blockage (e.g. 20/120 labeled = 0.17) fails
    # with the fraction visible.
    r = _gate_labels_accruing(
        _good_labels(n_labeled=20), regime_classifier_thresholds()
    )
    assert r.status == "fail"
    assert "0.17" in r.detail
    assert "20/120" in r.detail


def test_labels_accruing_insufficient_below_min_rows():
    r = _gate_labels_accruing(
        _good_labels(n_live_rows=10, n_labeled=1), regime_classifier_thresholds()
    )
    assert r.status == "insufficient_data"


def test_labels_accruing_none_and_error_are_insufficient():
    th = regime_classifier_thresholds()
    assert _gate_labels_accruing(None, th).status == "insufficient_data"
    r = _gate_labels_accruing(_good_labels(error="candles unreadable"), th)
    assert r.status == "insufficient_data"
    assert "candles unreadable" in r.detail


def test_labels_accruing_from_counts_matches_rg4_shape():
    # The RG4-replay counts path (n_records/n_unlabeled) — e.g. the MES case:
    # 1213/1861 unlabeled → labeled fraction ≈ 0.35.
    res = labels_accruing_from_counts("m", n_live_rows=1861, n_unlabeled=1213)
    assert res.n_labeled == 648
    assert abs(res.labeled_fraction - 648 / 1861) < 1e-12


def test_default_profile_reports_mechanics_gates_unrequired():
    # Non-regime profiles: UNCHANGED behaviour — the new gates are present in
    # the report but not required, so a decision model's readiness is
    # untouched by missing mechanics inputs.
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
    lp = next(r for r in report.results if r.name == "live_parity")
    la = next(r for r in report.results if r.name == "labels_accruing")
    assert lp.required is False and lp.status == "insufficient_data"
    assert la.required is False and la.status == "insufficient_data"


# --- pure helpers (ml.promotion.live_parity) --------------------------------


def test_score_fidelity_counts_mismatches_and_errors():
    rows = [
        ({"a": 1.0}, 1.0),   # match
        ({"a": 2.0}, 2.0),   # match
        ({"a": 3.0}, 9.9),   # mismatch
        ({"a": "boom"}, 0.0),  # predict raises → mismatch
    ]

    def predict(row):
        if row["a"] == "boom":
            raise RuntimeError("cannot score")
        return float(row["a"])

    assert score_fidelity(rows, predict, score_tol=1e-6) == 2


def test_score_fidelity_tolerance():
    rows = [({"a": 1.0}, 1.0 + 5e-7)]
    assert score_fidelity(rows, lambda r: r["a"], score_tol=1e-6) == 0
    assert score_fidelity(rows, lambda r: r["a"], score_tol=1e-8) == 1


def test_dead_features_constant_live_vs_varying_train():
    live = [{"x": 0.0, "y": 1.0}, {"x": 0.0, "y": 2.0}]
    train = [{"x": 0.1, "y": 1.0}, {"x": 0.2, "y": 2.0}]
    dead_live, dead_train = dead_features(live, train)
    assert dead_live == ("x",)
    assert dead_train == ()


def test_dead_features_constant_train_vs_varying_live():
    live = [{"x": 0.1, "y": 1.0}, {"x": 0.2, "y": 2.0}]
    train = [{"x": 0.0, "y": 1.0}, {"x": 0.0, "y": 2.0}]
    dead_live, dead_train = dead_features(live, train)
    assert dead_live == ()
    assert dead_train == ("x",)


def test_dead_features_missing_live_feature_is_dead_live():
    # A trained-on feature entirely ABSENT from the live rows is dead-live
    # (the live pipeline never populates it) — the exact ETH-xa failure mode.
    live = [{"y": 1.0}, {"y": 2.0}]
    train = [{"xa_peer": 0.1, "y": 1.0}, {"xa_peer": 0.7, "y": 2.0}]
    dead_live, dead_train = dead_features(live, train)
    assert dead_live == ("xa_peer",)


def test_dead_features_constant_both_sides_not_flagged():
    live = [{"sym": "ETHUSDT", "y": 1.0}, {"sym": "ETHUSDT", "y": 2.0}]
    train = [{"sym": "ETHUSDT", "y": 3.0}, {"sym": "ETHUSDT", "y": 4.0}]
    dead_live, dead_train = dead_features(live, train)
    assert dead_live == () and dead_train == ()


def test_dead_features_excludes_target_column():
    live = [{"y": 1.0}, {"y": 2.0}]
    train = [
        {"y": 1.0, "regime_label": "range"},
        {"y": 2.0, "regime_label": "volatile"},
    ]
    dead_live, _ = dead_features(live, train, exclude={"regime_label"})
    assert dead_live == ()
