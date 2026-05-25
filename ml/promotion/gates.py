"""Computed promotion gates (ML go-live readiness, 2026-05-25).

``ModelRegistry.promote_stage`` enforces only edge legality + a non-blank
``by``/``reason``. It does **not** check whether a model has earned the
transition. ``ml.promotion.checklist`` lists the gates as documentation
strings, but nothing computes them. This module turns those into
**computed** checks so the operator approves a stage change on evidence,
not vibes.

Scope: this is the ``shadow → advisory`` gate — the go-live switch. It
reports; it never mutates the registry and never touches the order path.
The operator (or ``ml.promotion.stage_guard``) reads the report and
decides.

A gate is one of:

- ``pass`` — the check ran and the model cleared the threshold.
- ``fail`` — the check ran and the model is below the threshold.
- ``insufficient_data`` — the check could not run (e.g. no live
  attribution yet, fewer than two training runs). Treated as **not
  ready** — you cannot promote on missing evidence — but distinguished
  from ``fail`` so the operator knows whether to wait or to abandon.

``GateReport.ready`` is ``True`` only when every *required* gate is
``pass``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class GateThresholds:
    min_class_f1: float = 0.30
    min_trades: int = 200
    min_eval: int = 1000
    stability_metric_max_std: float = 0.05
    stability_min_runs: int = 3
    shadow_soak_days: float = 7.0
    min_auc: float = 0.55
    min_brier_lift: float = 0.0
    score_spread_eps: float = 1e-6


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str  # "pass" | "fail" | "insufficient_data"
    detail: str
    value: float | None = None
    threshold: float | None = None
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "value": self.value,
            "threshold": self.threshold,
            "required": self.required,
        }


@dataclass(frozen=True)
class GateReport:
    model_id: str
    current_stage: str
    target_stage: str
    results: tuple[GateResult, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return all(r.status == "pass" for r in self.results if r.required)

    @property
    def blocking(self) -> list[GateResult]:
        return [r for r in self.results if r.required and r.status != "pass"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "current_stage": self.current_stage,
            "target_stage": self.target_stage,
            "ready": self.ready,
            "gates": [r.to_dict() for r in self.results],
            "blocking": [r.name for r in self.blocking],
        }


_PRIMARY_METRICS = ("macro_f1", "weighted_f1", "accuracy", "brier", "mae")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _primary_metric(metrics: dict[str, Any]) -> str | None:
    for k in _PRIMARY_METRICS:
        if k in metrics:
            return k
    return None


def _gate_non_degenerate(entry: Any, attribution: Any, th: GateThresholds) -> GateResult:
    metrics = dict(entry.metrics)
    f1_keys = [k for k in metrics if k.startswith("f1_")]
    if f1_keys:
        worst = min(float(metrics[k]) for k in f1_keys)
        ok = worst >= th.min_class_f1
        return GateResult(
            "non_degenerate",
            "pass" if ok else "fail",
            f"min per-class F1 = {worst:.3f} over {sorted(f1_keys)}",
            value=worst, threshold=th.min_class_f1,
        )
    if attribution is not None:
        spread = attribution.score_max - attribution.score_min
        ok = spread > th.score_spread_eps
        return GateResult(
            "non_degenerate",
            "pass" if ok else "fail",
            f"live score spread = {spread:.6g} (collapsed output if ~0)",
            value=spread, threshold=th.score_spread_eps,
        )
    return GateResult(
        "non_degenerate", "insufficient_data",
        "no per-class F1 in metrics and no live attribution to measure spread",
    )


def _gate_beats_baseline(attribution: Any, th: GateThresholds) -> GateResult:
    if attribution is None or attribution.brier_lift is None:
        return GateResult(
            "beats_baseline", "insufficient_data",
            "no probability-calibrated live attribution (brier_lift) available",
            required=True,
        )
    ok = attribution.brier_lift > th.min_brier_lift
    return GateResult(
        "beats_baseline",
        "pass" if ok else "fail",
        f"brier_lift = {attribution.brier_lift:.5f} "
        f"(model brier {attribution.brier:.5f} vs base-rate {attribution.baseline_brier:.5f})",
        value=attribution.brier_lift, threshold=th.min_brier_lift,
    )


def _gate_sample_sufficiency(entry: Any, attribution: Any, th: GateThresholds) -> GateResult:
    if attribution is not None and attribution.n > 0:
        ok = attribution.n >= th.min_trades
        return GateResult(
            "sample_sufficiency",
            "pass" if ok else "fail",
            f"{attribution.n} live closed trades scored",
            value=float(attribution.n), threshold=float(th.min_trades),
        )
    n_eval = entry.metrics.get("n_eval")
    if n_eval is not None:
        ok = float(n_eval) >= th.min_eval
        return GateResult(
            "sample_sufficiency",
            "pass" if ok else "fail",
            f"no live trades yet; falling back to eval n={float(n_eval):.0f}",
            value=float(n_eval), threshold=float(th.min_eval),
        )
    return GateResult(
        "sample_sufficiency", "insufficient_data",
        "no live attribution and no n_eval metric",
    )


def _gate_cross_run_stability(entry: Any, th: GateThresholds) -> GateResult:
    runs = list(getattr(entry, "runs", ()) or ())
    metric = _primary_metric(dict(entry.metrics))
    if metric is None:
        return GateResult(
            "cross_run_stability", "insufficient_data",
            "no recognized primary metric to track across runs",
        )
    vals = [
        float(r.metrics[metric])
        for r in runs[-10:]
        if metric in (r.metrics or {})
    ]
    if len(vals) < th.stability_min_runs:
        return GateResult(
            "cross_run_stability", "insufficient_data",
            f"only {len(vals)} run(s) carry '{metric}'; "
            f"need {th.stability_min_runs}",
            value=float(len(vals)), threshold=float(th.stability_min_runs),
        )
    mean = sum(vals) / len(vals)
    std = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
    ok = std <= th.stability_metric_max_std
    return GateResult(
        "cross_run_stability",
        "pass" if ok else "fail",
        f"std('{metric}') = {std:.4f} over last {len(vals)} runs",
        value=std, threshold=th.stability_metric_max_std,
    )


def _stage_entered_at(entry: Any) -> datetime | None:
    """When the model entered its CURRENT stage (latest matching event),
    falling back to created_at if there is no stage_history."""
    stage = entry.target_deployment_stage
    for ev in reversed(list(getattr(entry, "stage_history", ()) or ())):
        if ev.to_stage == stage:
            return ev.at
    return getattr(entry, "created_at", None)


def _gate_shadow_soak(entry: Any, th: GateThresholds) -> GateResult:
    entered = _stage_entered_at(entry)
    if entered is None:
        return GateResult(
            "shadow_soak", "insufficient_data",
            "cannot determine when the model entered shadow",
        )
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=timezone.utc)
    days = (_now_utc() - entered).total_seconds() / 86400.0
    ok = days >= th.shadow_soak_days
    return GateResult(
        "shadow_soak",
        "pass" if ok else "fail",
        f"{days:.1f} days at '{entry.target_deployment_stage}' "
        f"(since {entered.isoformat(timespec='seconds')})",
        value=days, threshold=th.shadow_soak_days,
    )


def _gate_live_agreement(attribution: Any, th: GateThresholds) -> GateResult:
    if attribution is None or attribution.auc is None:
        return GateResult(
            "live_agreement", "insufficient_data",
            "need at least one live winning and one losing scored trade for AUC",
        )
    ok = attribution.auc >= th.min_auc
    return GateResult(
        "live_agreement",
        "pass" if ok else "fail",
        f"rank-AUC(score vs realized win) = {attribution.auc:.3f}",
        value=attribution.auc, threshold=th.min_auc,
    )


def _gate_drift_clean(drift: Any) -> GateResult:
    if drift is None:
        return GateResult(
            "drift_clean", "insufficient_data",
            "no drift report supplied (shadow-drift needs both windows populated)",
        )
    verdict = getattr(drift, "overall_verdict", None) or drift.get("overall_verdict")  # type: ignore[union-attr]
    ok = verdict in {"no_change", "minor"}
    return GateResult(
        "drift_clean",
        "pass" if ok else "fail",
        f"score-distribution drift verdict = {verdict!r}",
    )


def evaluate_gates(
    entry: Any,
    *,
    target_stage: str = "advisory",
    attribution: Any = None,
    drift: Any = None,
    thresholds: GateThresholds | None = None,
) -> GateReport:
    """Evaluate the shadow→advisory promotion gates for one model.

    ``entry`` is a ``ml.registry.model_registry.RegistryEntry``.
    ``attribution`` is the matching ``ModelAttribution`` (or ``None``).
    ``drift`` is a ``ml.shadow.drift.DriftReport`` or the dict the
    ``shadow-drift`` CLI emits (or ``None``).
    """
    th = thresholds or GateThresholds()
    results = (
        _gate_non_degenerate(entry, attribution, th),
        _gate_beats_baseline(attribution, th),
        _gate_sample_sufficiency(entry, attribution, th),
        _gate_cross_run_stability(entry, th),
        _gate_shadow_soak(entry, th),
        _gate_live_agreement(attribution, th),
        _gate_drift_clean(drift),
    )
    return GateReport(
        model_id=entry.model_id,
        current_stage=entry.target_deployment_stage,
        target_stage=target_stage,
        results=results,
    )
