"""Stage guard — promote/demote proposal generator (ML go-live, 2026-05-25).

Walks every model in the registry and emits a **proposal** for each:

- ``promote`` — a ``shadow`` model that clears every promotion gate
  (``ml.promotion.gates``) is proposed for ``advisory``.
- ``demote`` — a live-influencing model (``advisory`` and above) that
  trips a demote trigger (drift, degeneracy, live underperformance) is
  proposed for the next step down the ladder.
- ``hold`` — everything else, with the blocking reasons attached.

**This module never mutates the registry and never touches the order
path.** Both promotion and demotion are operator-gated (the operator's
explicit policy, 2026-05-25): the guard produces evidence and a
recommendation; a human runs ``python -m ml promote-stage`` to act. The
intended deployment is a daily job that prints this report and, when WS8
alerting lands, pings the operator on any non-``hold`` proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..manifest import canonical_stage
from ..registry.model_registry import ModelRegistry
from ..shadow.drift import compute_drift
from ..shadow.inspector import filter_records, iter_records
from .attribution import compute_attribution
from .gates import (
    GateReport,
    GateThresholds,
    evaluate_gates,
    is_regime_classifier,
    thresholds_for,
)

# One-step rollback toward shadow (mirrors the registry's rollback edges).
# 3-stage collapse (2026-06-16): the only canonical influence stage is
# `advisory`, which demotes to `shadow`. Legacy `limited_live` /
# `live_approved` normalize to `advisory` before the lookup.
_DEMOTE_TARGET: dict[str, str] = {
    "advisory": "shadow",
}
_LIVE_STAGES = frozenset(_DEMOTE_TARGET)


@dataclass(frozen=True)
class Proposal:
    model_id: str
    current_stage: str
    action: str  # "promote" | "demote" | "hold"
    proposed_stage: str | None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "current_stage": self.current_stage,
            "action": self.action,
            "proposed_stage": self.proposed_stage,
            "reasons": list(self.reasons),
            "evidence": self.evidence,
        }


def _demote_triggers(
    attribution: Any, drift: Any, thresholds: GateThresholds,
    *, is_regime: bool = False,
) -> list[str]:
    """Reasons a live-influencing model should be pulled back. Empty list
    = healthy.

    ``is_regime`` flags a multiclass regime-classifier head. For a regime
    head the two TRADE-WIN-axis triggers — ``brier_lift < 0`` (live
    calibration worse than base rate) and ``auc < 0.5`` (live discrimination
    inverted) — are **suppressed**: both are computed from the rank-AUC /
    Brier of the regime SCORE vs realized trade WIN (`ml.promotion.attribution`),
    which is the WRONG AXIS for a head that predicts the *regime*, not the
    trade outcome (MB-20260630-001). This mirrors Option A on the PROMOTE
    side (PR #4700, `ml.promotion.gates`), which made the trade-win
    ``live_agreement`` (AUC) + ``beats_baseline`` (brier_lift) gates
    NON-required for regime heads, replacing them with the regime-appropriate
    ``live_regime_discrimination`` (RG4) gate.

    A regime head can still be demoted on a regime-appropriate signal that
    lives in this same code path — score-distribution drift verdict
    ``significant`` or a collapsed live score output (spread ~0). RG4 itself
    is not available to the stage-guard sweep yet (per-model candle
    resolution is a separate follow-up; see the NOTE in ``run_stage_guard``),
    so an RG4-collapse demote is intentionally not asserted here — for a
    regime head we fall through to ``hold`` rather than demote on the wrong
    (trade-win) axis. The brier_lift / auc-inverted triggers stay UNCHANGED
    for the decision/outcome model families (trade-outcome, setup-quality,
    conviction, execution-quality, prop-mission), where trade-win IS the
    right axis."""
    reasons: list[str] = []
    verdict = None
    if drift is not None:
        verdict = getattr(drift, "overall_verdict", None)
    if verdict == "significant":
        reasons.append("score-distribution drift verdict is 'significant'")
    if attribution is not None:
        spread = attribution.score_max - attribution.score_min
        if spread <= thresholds.score_spread_eps:
            reasons.append(f"live score output collapsed (spread {spread:.6g})")
        # Trade-win-axis triggers: meaningful for a decision/outcome model
        # whose live score IS a win-probability, but the WRONG AXIS for a
        # regime classifier (MB-20260630-001). Suppress them for regime heads.
        if not is_regime:
            if attribution.brier_lift is not None and attribution.brier_lift < 0:
                reasons.append(
                    f"live calibration worse than base rate "
                    f"(brier_lift {attribution.brier_lift:.5f})"
                )
            if attribution.auc is not None and attribution.auc < 0.5:
                reasons.append(
                    f"live discrimination inverted (AUC {attribution.auc:.3f} < 0.5)"
                )
    return reasons


def propose_for_model(
    entry: Any,
    *,
    attribution: Any = None,
    drift: Any = None,
    oos_edge: Any = None,
    live_regime_auc: float | None = None,
    thresholds: GateThresholds | None = None,
) -> Proposal:
    """Pure proposal decision for one model (no I/O)."""
    # Auto-select the classifier profile for a regime head when no explicit
    # thresholds override is given; an explicit `thresholds` still wins.
    th = thresholds_for(entry, override=thresholds)
    # Normalize so a stage stored under a legacy alias still routes correctly.
    try:
        stage = canonical_stage(entry.target_deployment_stage)
    except ValueError:
        stage = entry.target_deployment_stage

    if stage == "shadow":
        report: GateReport = evaluate_gates(
            entry, target_stage="advisory",
            attribution=attribution, drift=drift, oos_edge=oos_edge,
            live_regime_auc=live_regime_auc, thresholds=th,
        )
        if report.ready:
            return Proposal(
                entry.model_id, stage, "promote", "advisory",
                reasons=("all promotion gates pass",),
                evidence={"gate_report": report.to_dict()},
            )
        return Proposal(
            entry.model_id, stage, "hold", None,
            reasons=tuple(f"gate not met: {r.name} ({r.status})" for r in report.blocking),
            evidence={"gate_report": report.to_dict()},
        )

    if stage in _LIVE_STAGES:
        # Classify the head's FAMILY via the canonical helper Option A uses on
        # the promote side (gates.is_regime_classifier) so the demote axis is
        # aligned with the promote axis (MB-20260630-001) — no new ad-hoc regex.
        triggers = _demote_triggers(
            attribution, drift, th, is_regime=is_regime_classifier(entry),
        )
        if triggers:
            return Proposal(
                entry.model_id, stage, "demote", _DEMOTE_TARGET[stage],
                reasons=tuple(triggers),
                evidence={
                    "attribution": attribution.to_dict() if attribution else None,
                    "drift_verdict": getattr(drift, "overall_verdict", None),
                },
            )
        return Proposal(
            entry.model_id, stage, "hold", None,
            reasons=("no demote trigger tripped",),
            evidence={
                "attribution": attribution.to_dict() if attribution else None,
                "drift_verdict": getattr(drift, "overall_verdict", None),
            },
        )

    # Pre-shadow stage (canonical `candidate`; legacy research_only /
    # backtest_approved normalize to it): off the live evaluation path —
    # nothing to propose.
    return Proposal(
        entry.model_id, stage, "hold", None,
        reasons=("pre-shadow stage; not in the live influence path",),
    )


def _drift_for_model(
    records: list, model_id: str, *, reference_days: float, current_days: float,
) -> Any:
    """Window-over-window drift for one model, or None when either window
    is empty (mirrors the shadow-drift CLI windowing). Backfill records
    are excluded so synthetic timestamps don't pollute the comparison."""
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=current_days)
    reference_start = current_start - timedelta(days=reference_days)
    rows = [
        r for r in filter_records(records, model_id=model_id)
        if r.backfill_kind is None
    ]
    ref = [r.score for r in rows if reference_start <= r.predicted_at_utc < current_start]
    cur = [r.score for r in rows if r.predicted_at_utc >= current_start]
    if not ref or not cur:
        return None
    return compute_drift(ref, cur)


def run_stage_guard(
    *,
    registry_root: Path | str,
    db_path: Path | str,
    shadow_log: Path | str,
    backfill_log: Path | str | None = None,
    thresholds: GateThresholds | None = None,
    reference_days: float = 30.0,
    current_days: float = 7.0,
    include_demo: bool = False,
    datasets_root: Path | str | None = None,
) -> list[Proposal]:
    """Evaluate every registered model and return its proposal.

    Loads attribution once (one DB + log pass) and computes per-model
    drift from the in-memory record set. Read-only throughout.

    When ``datasets_root`` is supplied (the trainer VM, where the datasets
    live), the offline purged-WF-CV OOS edge is computed for every
    ``shadow``-stage model so the promote gate has its champion-challenger
    evidence; without it those models hold on ``oos_edge`` insufficient
    data — you cannot certify readiness without the OOS evidence.
    """
    registry = ModelRegistry(Path(registry_root))
    attribution = {
        a.model_id: a
        for a in compute_attribution(
            db_path=db_path, shadow_log=shadow_log,
            backfill_log=backfill_log, include_demo=include_demo,
        )
    }
    records = list(iter_records(shadow_log))
    proposals: list[Proposal] = []
    for entry in registry.list():
        drift = _drift_for_model(
            records, entry.model_id,
            reference_days=reference_days, current_days=current_days,
        )
        oos_edge = None
        if datasets_root is not None and entry.target_deployment_stage == "shadow":
            from .oos_edge import compute_oos_edge

            # A regime head needs the multiclass-compatible modal baseline;
            # the compute_oos_edge default (constant baseline) silently yields
            # None against the multiclass evaluator (BL-20260607-002).
            oos_kwargs: dict[str, Any] = {"datasets_root": datasets_root}
            if is_regime_classifier(entry):
                oos_kwargs["baseline_trainer"] = (
                    "ml.trainers.regime_classifier.RegimeClassifierTrainer"
                )
            oos_edge = compute_oos_edge(entry, **oos_kwargs)
        # NOTE: the RG4 live regime-discrimination AUC is NOT computed here
        # yet — it needs a per-model candle source (symbol/timeframe → the
        # right `market_raw/.../data.jsonl`) for the realized-regime join, and
        # the sweep doesn't resolve candles per model. So a regime head's
        # `live_regime_discrimination` gate reports `insufficient_data` (→ not
        # ready) in this sweep until that per-model candle resolution is wired
        # (separate follow-up). The single-model `gate-check` CLI DOES compute
        # it (see `ml/cli.py::_cmd_gate_check`).
        proposals.append(propose_for_model(
            entry,
            attribution=attribution.get(entry.model_id),
            drift=drift,
            oos_edge=oos_edge,
            live_regime_auc=None,
            thresholds=thresholds,
        ))
    return proposals
