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

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class GateThresholds:
    min_class_f1: float = 0.30
    # Imbalance-aware alternative path through `non_degenerate`. The plain
    # `min_class_f1 ≥ 0.30` rule is unfair to high-precision/low-recall
    # minority predictors on heavily skewed labels: a 0.5%-base-rate class
    # caps F1 around its support no matter how good precision is. The
    # alternative requires precision lift over a random predictor AND a
    # floor on recall, so a model that's right when it fires AND fires
    # sometimes can pass even with low F1. Both legs must hold for every
    # observed class. Requires the evaluator to emit `support_<class>`.
    min_class_precision_lift: float = 2.0
    min_class_recall: float = 0.05
    min_trades: int = 200
    min_eval: int = 1000
    stability_metric_max_std: float = 0.05
    stability_min_runs: int = 3
    shadow_soak_days: float = 7.0
    min_auc: float = 0.55
    min_brier_lift: float = 0.0
    score_spread_eps: float = 1e-6
    # Offline champion-challenger edge (S-MLOPT-S4): the candidate must
    # beat the baseline on purged WF-CV by a strictly-positive oriented
    # margin (default 0.0 → "must beat the baseline at all"). Tighten per
    # model family if a margin of safety is wanted.
    min_oos_edge: float = 0.0
    # Drift bounds (S-MLOPT-S4): the score-distribution must stay within
    # these KS / PSI ceilings. Defaults are the industry-standard "moderate"
    # thresholds from ml.shadow.drift (KS 0.2, PSI 0.25) — anything at or
    # below is acceptable, above is blocking.
    max_ks: float = 0.2
    max_psi: float = 0.25
    # Whether the live `beats_baseline` (probability-calibrated brier_lift)
    # gate is *required*. Default True for a trade-outcome decision model,
    # whose live score IS a win-probability so brier_lift is meaningful. For
    # a multiclass regime CLASSIFIER head the live "score" is a regime
    # probability, not a win-probability, so brier-vs-trade-outcome is not a
    # meaningful champion-challenger signal — the leak-free `oos_edge`
    # (purged-WF-CV macro_f1 vs the modal baseline) is. The regime profile
    # turns this off so a classifier isn't blocked on a degenerate live
    # brier; `oos_edge` carries the beats-baseline role. (S-MLOPT regime
    # gate profile, 2026-06-07.)
    require_beats_baseline: bool = True
    # Live regime-discrimination gate (RG4, 2026-06-26 — regime profile only).
    # For a regime CLASSIFIER head the right LIVE track-record signal is not
    # rank-AUC(score vs trade WIN) — a vol-regime head predicts the *regime*,
    # not trade win/loss, and on thin real-money flow the trade-outcome AUC is
    # both wrong and un-satisfiable. The RG4 Stage-2 replay re-scores the head
    # on the EXACT logged-live feature rows and computes AUC vs the realized
    # *regime* label (`scripts/ml/replay_pregate_live.py`). The regime profile
    # makes this the required live gate and turns `live_agreement`
    # (trade-outcome) off; the default decision-model profile leaves this not
    # required and keeps `live_agreement` required.
    min_live_regime_auc: float = 0.55
    # M25 gate reframe (operator-approved 2026-07-19, docs/research/
    # M25-promotion-consolidation-DESIGN.md § "The promotion gate — REFRAMED
    # 2026-07-19"): the edge of an ML head is proven OFFLINE (the powered
    # walk-forward `oos_edge` gate); the live soak's job is to prove serving
    # MECHANICS. `live_regime_discrimination` is therefore ADVISORY (still
    # computed + reported, `required: false`) even under the regime profile —
    # it is an outcome-statistics gate that takes weeks to power in calm
    # regimes. The required live gates for a regime head are the deterministic
    # mechanics checks below (`live_parity` + `labels_accruing`).
    require_live_regime_discrimination: bool = False
    # `live_parity` (M25 reframe) — deterministic serving-mechanics checks
    # over the head's live-logged shadow rows (ml.promotion.live_parity):
    # (a) serving fidelity — re-score up to `parity_sample_n` most-recent
    # live rows with the registered artifact; logged-vs-recomputed must agree
    # within `parity_score_tol`, with the mismatch fraction capped at
    # `parity_max_mismatch_frac`; (b) dead-feature parity — a feature
    # constant/all-zeros on one side (live vs training data) but varying on
    # the other fails, named in the detail (the ETH-xa bug class,
    # BL-20260628-XA-TRAINING-ZERO). Fewer than `parity_min_rows` live rows
    # with feature_row → insufficient_data (mechanics unproven yet).
    require_live_parity: bool = False
    parity_sample_n: int = 50
    parity_score_tol: float = 1e-6
    parity_max_mismatch_frac: float = 0.02
    parity_min_rows: int = 20
    # Fidelity rows must postdate the current artifact's training run by this
    # grace (mirror-sync + predictor-reload lag) — see
    # ml.promotion.live_parity.DEFAULT_ARTIFACT_GRACE_S (2026-07-20 scoping).
    parity_artifact_grace_s: float = 1800.0
    # `labels_accruing` (M25 reframe) — the labeled fraction of the head's
    # live rows must reach `labels_min_fraction` once `labels_min_rows` live
    # rows exist (catches the stale-candle-base labeling blockage class, e.g.
    # MES 1213/1861 unlabeled — BL-20260626-MES-BASE-STALE). Fewer than
    # `labels_min_rows` live rows → insufficient_data.
    require_labels_accruing: bool = False
    labels_min_fraction: float = 0.30
    labels_min_rows: int = 20
    # Whether the live trade-outcome `live_agreement` gate is *required*.
    # Default True for a trade-outcome decision model. The regime profile turns
    # this off (a regime head is judged on live REGIME discrimination, not
    # trade win/loss), mirroring how `require_beats_baseline` opts the live
    # brier gate out for a classifier.
    require_live_agreement: bool = True


# Regime-classifier promotion profile (2026-06-07, Tier-3 — operator-gated).
# A regime head's promotion-worthy quality is its leak-free purged-WF-CV
# macro_f1 edge over the modal baseline (`oos_edge`), NOT live-trade volume:
# a 1h regime head joined to a thin-flow symbol accrues live scored trades
# far too slowly to ever clear the 200-trade `min_trades` bar that's right
# for a trade-outcome decision model. The regime profile keeps every quality
# + safety gate (`oos_edge`, `non_degenerate`, `cross_run_stability`,
# `shadow_soak` 7d, `drift_clean`) and (a) lowers the live sample floor to a
# small sanity count, (b) drops the degenerate live `beats_baseline`
# requirement, and (c) swaps the live track-record gate — a vol-regime head
# predicts the regime, not trade win/loss, so `live_agreement` (rank-AUC vs
# trade WIN) is OFF (2026-06-26, option A). It NEVER loosens `oos_edge`,
# `shadow_soak`, or `drift_clean`.
#
# M25 reframe (operator-approved 2026-07-19, docs/research/
# M25-promotion-consolidation-DESIGN.md § "The promotion gate — REFRAMED
# 2026-07-19"): the required LIVE gates for a regime head are the
# deterministic serving-mechanics checks `live_parity` + `labels_accruing`
# (soak = mechanics, not edge — the edge is proven offline by `oos_edge`).
# `live_regime_discrimination` (RG4 live-row AUC vs the realized regime
# label) is still computed + reported but ADVISORY (`required: false`) —
# an outcome-statistics gate that takes weeks to power in calm regimes.
REGIME_MIN_LIVE_TRADES: int = 5


def regime_classifier_thresholds(base: GateThresholds | None = None) -> GateThresholds:
    """The classifier-appropriate gate profile (see `REGIME_MIN_LIVE_TRADES`)."""
    return replace(
        base or GateThresholds(),
        min_trades=REGIME_MIN_LIVE_TRADES,
        require_beats_baseline=False,
        require_live_agreement=False,
        # M25 reframe 2026-07-19: RG4 discrimination demoted to advisory;
        # the required live gates are the serving-mechanics pair below.
        require_live_regime_discrimination=False,
        require_live_parity=True,
        require_labels_accruing=True,
    )


def is_regime_classifier(entry: Any) -> bool:
    """True when `entry` is a multiclass regime-classifier head.

    Prefers the manifest's dataset family (`market_features` is the regime
    family) so the classification is precise; falls back to "carries ≥2
    per-class F1 metrics" for entries with no manifest (e.g. test fakes).
    Never raises — an unreadable manifest just falls through to the metric
    shape.
    """
    try:
        manifest = dict(getattr(entry, "manifest", {}) or {})
        dataset = manifest.get("dataset")
        if isinstance(dataset, dict) and dataset.get("family") == "market_features":
            return True
        trainer_cfg = manifest.get("trainer_config")
        if isinstance(trainer_cfg, dict) and trainer_cfg.get("target_column") == "regime_label":
            return True
    except (TypeError, AttributeError):
        pass
    metrics = dict(getattr(entry, "metrics", {}) or {})
    return sum(1 for k in metrics if k.startswith("f1_")) >= 2


def thresholds_for(
    entry: Any,
    *,
    override: GateThresholds | None = None,
    regime: bool | None = None,
) -> GateThresholds:
    """Select the gate profile for one model.

    `override` wins outright. Otherwise `regime` forces (`True` → classifier
    profile, `False` → default); `regime=None` auto-detects via
    `is_regime_classifier`. Default callers that pass nothing keep the
    decision-model profile, so `evaluate_gates`' own default is unchanged.
    """
    if override is not None:
        return override
    use_regime = is_regime_classifier(entry) if regime is None else regime
    return regime_classifier_thresholds() if use_regime else GateThresholds()


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
        # Two pass paths — model is non-degenerate if EITHER holds:
        #
        #   (A) Strict F1 floor: min_per_class_f1 ≥ th.min_class_f1.
        #       Original gate semantics — works fine when classes are
        #       roughly balanced.
        #
        #   (B) Imbalance-aware: for every observed class, precision is at
        #       least th.min_class_precision_lift × the class's base rate
        #       AND recall is at least th.min_class_recall. Catches the
        #       case where a heavy class imbalance caps F1 on the minority
        #       no matter how well-calibrated the predictor is, but the
        #       model is still actually predicting both classes and being
        #       right about them more than chance would. Requires
        #       per-class support in the metrics dict (a class without
        #       support is treated as absent — won't gate on it).
        worst_f1 = min(float(metrics[k]) for k in f1_keys)
        if worst_f1 >= th.min_class_f1:
            return GateResult(
                "non_degenerate",
                "pass",
                f"min per-class F1 = {worst_f1:.3f} over {sorted(f1_keys)}",
                value=worst_f1, threshold=th.min_class_f1,
            )
        imbalance_result = _eval_imbalance_aware_alt(metrics, f1_keys, worst_f1, th)
        if imbalance_result is not None:
            return imbalance_result
        return GateResult(
            "non_degenerate", "fail",
            f"min per-class F1 = {worst_f1:.3f} over {sorted(f1_keys)} "
            f"(imbalance-aware alt requires per-class support metrics)",
            value=worst_f1, threshold=th.min_class_f1,
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


def _eval_imbalance_aware_alt(
    metrics: dict[str, Any],
    f1_keys: list[str],
    worst_f1: float,
    th: GateThresholds,
) -> GateResult | None:
    """Return a GateResult if the imbalance-aware path can be evaluated, else None.

    `None` means the metrics dict didn't carry enough info (no support
    fields) — the caller falls back to its original fail message rather
    than guessing.
    """
    classes = [k[len("f1_"):] for k in f1_keys]
    supports = {c: metrics.get(f"support_{c}") for c in classes}
    if any(s is None for s in supports.values()):
        return None
    n_total = sum(float(s) for s in supports.values())
    if n_total <= 0:
        return None
    # Classes with zero support don't impose a constraint (model can't be
    # judged on them).
    judged = [c for c in classes if float(supports[c]) > 0]
    if not judged:
        return None
    failures: list[str] = []
    precision_lifts: list[float] = []
    recalls: list[float] = []
    for c in judged:
        base_rate = float(supports[c]) / n_total
        precision = float(metrics.get(f"precision_{c}", 0.0))
        recall = float(metrics.get(f"recall_{c}", 0.0))
        # Precision-lift floor: how much better than a random predictor's
        # precision (= base_rate). Skip the lift check for classes whose
        # base rate is already ≥ 0.5 — there's no headroom to "lift" the
        # majority class, and we already enforce recall ≥ floor separately.
        if base_rate < 0.5:
            lift = precision / base_rate if base_rate > 0 else 0.0
            precision_lifts.append(lift)
            if lift < th.min_class_precision_lift:
                failures.append(
                    f"{c}: precision_lift={lift:.2f} "
                    f"(precision={precision:.3f} / base_rate={base_rate:.3f}) "
                    f"< {th.min_class_precision_lift:.2f}"
                )
        if recall < th.min_class_recall:
            failures.append(
                f"{c}: recall={recall:.3f} < {th.min_class_recall:.2f}"
            )
        recalls.append(recall)
    if failures:
        return GateResult(
            "non_degenerate", "fail",
            f"strict F1 floor not met (min={worst_f1:.3f} < {th.min_class_f1:.2f}); "
            f"imbalance-aware alt also fails: {'; '.join(failures)}",
            value=worst_f1, threshold=th.min_class_f1,
        )
    return GateResult(
        "non_degenerate", "pass",
        f"min per-class F1 = {worst_f1:.3f} (below strict floor "
        f"{th.min_class_f1:.2f}); imbalance-aware alt passes — "
        f"min precision_lift = {min(precision_lifts) if precision_lifts else float('inf'):.2f} "
        f"(≥ {th.min_class_precision_lift:.2f}), "
        f"min recall = {min(recalls):.3f} (≥ {th.min_class_recall:.2f})",
        value=worst_f1, threshold=th.min_class_f1,
    )


def _gate_beats_baseline(attribution: Any, th: GateThresholds) -> GateResult:
    if attribution is None or attribution.brier_lift is None:
        return GateResult(
            "beats_baseline", "insufficient_data",
            "no probability-calibrated live attribution (brier_lift) available"
            + ("" if th.require_beats_baseline
               else " — not required for a regime classifier (oos_edge carries it)"),
            required=th.require_beats_baseline,
        )
    ok = attribution.brier_lift > th.min_brier_lift
    return GateResult(
        "beats_baseline",
        "pass" if ok else "fail",
        f"brier_lift = {attribution.brier_lift:.5f} "
        f"(model brier {attribution.brier:.5f} vs base-rate {attribution.baseline_brier:.5f})",
        value=attribution.brier_lift, threshold=th.min_brier_lift,
        required=th.require_beats_baseline,
    )


def _gate_oos_edge(oos_edge: Any, th: GateThresholds) -> GateResult:
    """Offline champion-challenger gate: candidate beats baseline OOS.

    ``oos_edge`` is an ``ml.promotion.oos_edge.OOSEdgeResult`` (or ``None``
    when no purged WF-CV run was performed — e.g. ``gate-check`` invoked
    without ``--datasets-root``). The edge is pre-oriented so positive =
    candidate better; the model passes only when it beats the baseline by
    a strictly-positive margin over ``min_oos_edge``. Measured **only** on
    purged & embargoed walk-forward folds — never on a single holdout."""
    if oos_edge is None:
        return GateResult(
            "oos_edge", "insufficient_data",
            "no purged-WF-CV OOS edge computed (run gate-check with "
            "--datasets-root on the trainer VM to populate it)",
            threshold=th.min_oos_edge,
        )
    ok = oos_edge.edge > th.min_oos_edge
    return GateResult(
        "oos_edge",
        "pass" if ok else "fail",
        f"OOS edge on '{oos_edge.metric}' = {oos_edge.edge:+.5f} over "
        f"{oos_edge.n_folds} purged WF-CV folds "
        f"(candidate {oos_edge.candidate_score:.5f} vs baseline "
        f"{oos_edge.baseline_score:.5f}; {oos_edge.baseline_trainer})",
        value=oos_edge.edge, threshold=th.min_oos_edge,
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
            "need at least one live winning and one losing scored trade for AUC"
            + ("" if th.require_live_agreement
               else " — not required for a regime classifier "
                    "(live_regime_discrimination carries the live track record)"),
            required=th.require_live_agreement,
        )
    ok = attribution.auc >= th.min_auc
    return GateResult(
        "live_agreement",
        "pass" if ok else "fail",
        f"rank-AUC(score vs realized win) = {attribution.auc:.3f}",
        value=attribution.auc, threshold=th.min_auc,
        required=th.require_live_agreement,
    )


def _gate_live_regime_discrimination(
    live_regime_auc: float | None, th: GateThresholds,
) -> GateResult:
    """Live REGIME-discrimination gate (RG4) — the regime-head live signal.

    ``live_regime_auc`` is the AUC of the head's logged-live scores vs the
    realized forward-vol *regime* label, computed by re-scoring the EXACT
    feature rows the live runtime logged (``scripts/ml/replay_pregate_live``)
    against the candle-derived realized regime. This is the regime-appropriate
    replacement for ``live_agreement`` (which measures rank-AUC vs trade WIN —
    wrong for a head that predicts the regime, not trade outcome).

    M25 reframe (operator-approved 2026-07-19,
    docs/research/M25-promotion-consolidation-DESIGN.md § "The promotion gate
    — REFRAMED 2026-07-19"): ADVISORY under every stock profile
    (``required: false``) — still computed and reported, but no longer
    blocking. It is an outcome-statistics gate that takes weeks to power in
    calm regimes; the live soak's job is to prove serving MECHANICS
    (``live_parity`` + ``labels_accruing``), the edge being proven offline by
    ``oos_edge``. A custom ``GateThresholds`` can still opt it back in via
    ``require_live_regime_discrimination=True``."""
    if live_regime_auc is None:
        return GateResult(
            "live_regime_discrimination", "insufficient_data",
            "no RG4 live-row regime AUC computed (gate-check needs "
            "--shadow-log + a candle source for the realized-regime join)",
            threshold=th.min_live_regime_auc,
            required=th.require_live_regime_discrimination,
        )
    ok = live_regime_auc >= th.min_live_regime_auc
    return GateResult(
        "live_regime_discrimination",
        "pass" if ok else "fail",
        f"RG4 live regime-discrimination AUC = {live_regime_auc:.3f}",
        value=live_regime_auc, threshold=th.min_live_regime_auc,
        required=th.require_live_regime_discrimination,
    )


def _gate_live_parity(parity: Any, th: GateThresholds) -> GateResult:
    """Serving-mechanics parity gate (M25 reframe, operator-approved
    2026-07-19 — docs/research/M25-promotion-consolidation-DESIGN.md § "The
    promotion gate — REFRAMED 2026-07-19").

    ``parity`` is an ``ml.promotion.live_parity.LiveParityResult`` (or
    ``None``): (a) serving fidelity — the logged live scores must match a
    re-score by the registered artifact within tolerance; (b) dead-feature
    parity — a feature constant/all-zeros live-vs-train on one side only
    fails, named in the detail (the ETH-xa bug class). Any compute ERROR or
    a thin sample reports ``insufficient_data`` — mechanics unproven yet,
    never a silent pass."""
    required = th.require_live_parity
    name = "live_parity"
    if parity is None:
        return GateResult(
            name, "insufficient_data",
            "no live-parity check computed (run gate-check with --shadow-log "
            "+ --datasets-root so serving mechanics can be verified)",
            threshold=th.parity_max_mismatch_frac, required=required,
        )
    if getattr(parity, "error", None):
        return GateResult(
            name, "insufficient_data",
            f"live-parity check errored: {parity.error}",
            threshold=th.parity_max_mismatch_frac, required=required,
        )
    if parity.n_live_rows < th.parity_min_rows:
        return GateResult(
            name, "insufficient_data",
            f"only {parity.n_live_rows} live rows with feature_row; need "
            f"≥ {th.parity_min_rows} (serving mechanics unproven yet)",
            value=float(parity.n_live_rows),
            threshold=th.parity_max_mismatch_frac, required=required,
        )
    # Artifact-freshness scoping (2026-07-20): fidelity is only judged over
    # rows logged since the CURRENT artifact's training run — rows scored by
    # a previous nightly artifact mismatch by construction and carry no skew
    # information. Too few fresh rows → the current artifact's serving
    # fidelity is simply not measured yet (accrues in hours at bar cadence),
    # NOT a fail and NOT a silent pass. getattr-guarded so an older
    # LiveParityResult without the fields behaves as before.
    _artifact_at = getattr(parity, "artifact_at", None)
    _n_fresh = getattr(parity, "n_fresh_rows", None)
    if _artifact_at is not None and _n_fresh is not None \
            and _n_fresh < th.parity_min_rows:
        return GateResult(
            name, "insufficient_data",
            f"only {_n_fresh} live rows scored since the current artifact's "
            f"training run ({_artifact_at}); need ≥ {th.parity_min_rows} to "
            f"judge serving fidelity for THIS artifact — fresh rows accrue "
            f"at bar cadence (hours, not weeks)",
            value=float(_n_fresh),
            threshold=th.parity_max_mismatch_frac, required=required,
        )
    if not parity.train_available:
        return GateResult(
            name, "insufficient_data",
            "training dataset unavailable for the dead-feature parity check "
            "(pass --datasets-root on the trainer VM)",
            threshold=th.parity_max_mismatch_frac, required=required,
        )
    frac = parity.mismatch_fraction
    failures: list[str] = []
    if frac is not None and frac > th.parity_max_mismatch_frac:
        failures.append(
            f"serving-fidelity mismatch {parity.n_mismatched}/{parity.n_sampled} "
            f"({frac:.1%} > {th.parity_max_mismatch_frac:.1%}; "
            f"tol {parity.score_tol:g})"
        )
    if parity.dead_live_features:
        failures.append(
            "dead-on-LIVE features (constant/zero live, varying in training): "
            + ", ".join(parity.dead_live_features)
        )
    if parity.dead_train_features:
        failures.append(
            "dead-in-TRAINING features (constant/zero in training, varying live): "
            + ", ".join(parity.dead_train_features)
        )
    if failures:
        return GateResult(
            name, "fail", "; ".join(failures),
            value=frac, threshold=th.parity_max_mismatch_frac, required=required,
        )
    return GateResult(
        name, "pass",
        f"serving fidelity {parity.n_sampled - parity.n_mismatched}/"
        f"{parity.n_sampled} rows match within {parity.score_tol:g}; "
        f"no dead-feature divergence live-vs-train",
        value=frac, threshold=th.parity_max_mismatch_frac, required=required,
    )


def _gate_labels_accruing(labels: Any, th: GateThresholds) -> GateResult:
    """Label-pipeline health gate (M25 reframe, operator-approved 2026-07-19).

    ``labels`` is an ``ml.promotion.live_parity.LabelsAccruingResult`` (or
    ``None``). Once at least ``labels_min_rows`` live rows exist, the labeled
    fraction must reach ``labels_min_fraction`` — below it fails with the
    fraction in the detail (the stale-candle-base labeling blockage class,
    e.g. MES 1213/1861 unlabeled). Fewer rows → ``insufficient_data``."""
    required = th.require_labels_accruing
    name = "labels_accruing"
    if labels is None:
        return GateResult(
            name, "insufficient_data",
            "no label-accrual check computed (needs the live shadow log + a "
            "candle source for the realized-label join)",
            threshold=th.labels_min_fraction, required=required,
        )
    if getattr(labels, "error", None):
        return GateResult(
            name, "insufficient_data",
            f"label-accrual check errored: {labels.error}",
            threshold=th.labels_min_fraction, required=required,
        )
    if labels.n_live_rows < th.labels_min_rows:
        return GateResult(
            name, "insufficient_data",
            f"only {labels.n_live_rows} live rows; need ≥ {th.labels_min_rows} "
            f"before label accrual is judged",
            value=float(labels.n_live_rows),
            threshold=th.labels_min_fraction, required=required,
        )
    frac = labels.labeled_fraction or 0.0
    ok = frac >= th.labels_min_fraction
    detail = (
        f"labeled fraction {frac:.2f} ({labels.n_labeled}/{labels.n_live_rows} "
        f"live rows labeled)"
    )
    if not ok:
        detail += (
            f" < {th.labels_min_fraction:.2f} floor — labeling blocked? "
            f"(stale candle base / realized-label join failing)"
        )
    return GateResult(
        name, "pass" if ok else "fail", detail,
        value=frac, threshold=th.labels_min_fraction, required=required,
    )


def _gate_drift_clean(drift: Any, th: GateThresholds) -> GateResult:
    if drift is None:
        return GateResult(
            "drift_clean", "insufficient_data",
            "no drift report supplied (shadow-drift needs both windows populated)",
        )
    # Quantitative path: a real DriftReport carries the raw KS + PSI
    # statistics — gate on the pre-registered numeric ceilings directly so
    # the criterion is mechanical, not a verdict-bucket judgement call.
    ks = getattr(drift, "ks", None)
    psi = getattr(drift, "psi", None)
    if ks is not None and psi is not None:
        ok = float(ks) <= th.max_ks and float(psi) <= th.max_psi
        return GateResult(
            "drift_clean",
            "pass" if ok else "fail",
            f"score-distribution drift KS = {float(ks):.4f} (≤ {th.max_ks}), "
            f"PSI = {float(psi):.4f} (≤ {th.max_psi})",
            value=float(ks), threshold=th.max_ks,
        )
    # Fallback for the dict the shadow-drift CLI emits (verdict only).
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
    oos_edge: Any = None,
    live_regime_auc: float | None = None,
    live_parity: Any = None,
    labels_accruing: Any = None,
    thresholds: GateThresholds | None = None,
) -> GateReport:
    """Evaluate the shadow→advisory promotion gates for one model.

    ``entry`` is a ``ml.registry.model_registry.RegistryEntry``.
    ``attribution`` is the matching ``ModelAttribution`` (or ``None``).
    ``drift`` is a ``ml.shadow.drift.DriftReport`` or the dict the
    ``shadow-drift`` CLI emits (or ``None``).
    ``oos_edge`` is an ``ml.promotion.oos_edge.OOSEdgeResult`` (or ``None``)
    — the offline candidate-vs-baseline edge measured on purged WF-CV.
    ``live_regime_auc`` is the RG4 live-row regime-discrimination AUC (or
    ``None``) — advisory reporting since the M25 reframe (operator-approved
    2026-07-19): still computed + shown, no longer blocking.
    ``live_parity`` / ``labels_accruing`` are the serving-mechanics results
    (``ml.promotion.live_parity``) — the REQUIRED live gates under the regime
    profile since the M25 reframe (soak = mechanics, not edge; the edge is
    proven offline by ``oos_edge``). Non-regime profiles leave both
    non-required (unchanged behaviour) unless they opt in.

    ``report.ready`` is ``True`` only when every *required* gate clears its
    pre-registered threshold. A required gate with no evidence reports
    ``insufficient_data`` (treated as not-ready, never silently skipped).
    """
    th = thresholds or GateThresholds()
    results = (
        _gate_non_degenerate(entry, attribution, th),
        _gate_beats_baseline(attribution, th),
        _gate_oos_edge(oos_edge, th),
        _gate_sample_sufficiency(entry, attribution, th),
        _gate_cross_run_stability(entry, th),
        _gate_shadow_soak(entry, th),
        _gate_live_agreement(attribution, th),
        _gate_live_regime_discrimination(live_regime_auc, th),
        _gate_live_parity(live_parity, th),
        _gate_labels_accruing(labels_accruing, th),
        _gate_drift_clean(drift, th),
    )
    return GateReport(
        model_id=entry.model_id,
        current_stage=entry.target_deployment_stage,
        target_stage=target_stage,
        results=results,
    )
