"""Out-of-sample edge vs a baseline under purged WF-CV (S-MLOPT-S4, M14 0.4).

The promotion question this answers: *does the candidate model actually
beat a naive baseline on honest, leakage-free out-of-sample folds?* —
the offline complement to the live-attribution gates in
``ml.promotion.attribution`` (which measure agreement against *realized*
trades).

It re-runs the candidate's own manifest through the **purged & embargoed
walk-forward CV** from S-MLOPT-S1 (``ml.experiments.splitters.iter_folds``
+ the runner's per-fold fit/score + ``_aggregate_fold_metrics`` pooling),
and runs a **baseline trainer** (default: the constant per-group-mean
predictor — the same baseline the decision models are measured against in
G4) through the *same folds*. The folds are deterministic for a fixed
dataset + CV config, so candidate and baseline are scored on identical
test blocks. The edge is the pooled-metric improvement of the candidate
over the baseline, oriented so **positive = candidate is better**.

This is the no-leakage guardrail the roadmap demands: the OOS edge is
**never** scored on a single holdout — only on purged WF-CV folds.

Pure decision-support: it reads a dataset off disk, trains throwaway
models in-process, and returns a result object. It never registers a
model, edits a manifest, or touches the order path. A missing dataset /
unreconstructable manifest / incompatible baseline returns ``None`` so
the caller's gate reports ``insufficient_data`` rather than crashing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..experiments.runner import (
    _aggregate_fold_metrics,
    _load_jsonl,
    _resolve_callable,
)
from ..experiments.splitters import iter_folds
from ..manifest import TrainingManifest

# The canonical naive baseline for the decision models (G4): the constant
# per-group-mean predictor. Overridable for classification heads, where a
# majority-class baseline is the right comparator.
DEFAULT_BASELINE_TRAINER = "ml.trainers.constant_baseline.ConstantPredictionTrainer"

# Metric orientation. Everything not listed here is treated as
# higher-is-better (f1 / accuracy / precision / recall / auc / …).
_LOWER_IS_BETTER: frozenset[str] = frozenset(
    {"mse", "mae", "rmse", "brier", "logloss", "log_loss"}
)

# Preference order when picking the single metric the edge is reported on.
# Mirrors ml.promotion.gates._PRIMARY_METRICS, extended with mse so the
# regression decision models (setup-quality / trade-outcome) resolve a
# primary metric.
_PRIMARY_METRIC_PREFERENCE: tuple[str, ...] = (
    "macro_f1",
    "weighted_f1",
    "accuracy",
    "brier",
    "mae",
    "mse",
)


def _is_higher_better(metric: str) -> bool:
    return metric not in _LOWER_IS_BETTER


def orient_edge(metric: str, candidate: float, baseline: float) -> float:
    """Signed edge oriented so a positive value always means the candidate
    is better than the baseline, regardless of metric direction."""
    if _is_higher_better(metric):
        return candidate - baseline
    return baseline - candidate


def _pick_primary_metric(
    candidate: Mapping[str, float], baseline: Mapping[str, float]
) -> str | None:
    """First preferred metric present (and comparable) in BOTH metric sets."""
    shared = set(candidate) & set(baseline)
    for key in _PRIMARY_METRIC_PREFERENCE:
        if key in shared:
            return key
    return None


@dataclass(frozen=True)
class OOSEdgeResult:
    model_id: str
    metric: str
    higher_is_better: bool
    candidate_score: float
    baseline_score: float
    edge: float  # oriented: > 0 means candidate beats baseline
    n_folds: int
    n_rows: int
    candidate_trainer: str
    baseline_trainer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "metric": self.metric,
            "higher_is_better": self.higher_is_better,
            "candidate_score": self.candidate_score,
            "baseline_score": self.baseline_score,
            "edge": self.edge,
            "n_folds": self.n_folds,
            "n_rows": self.n_rows,
            "candidate_trainer": self.candidate_trainer,
            "baseline_trainer": self.baseline_trainer,
        }


def _pooled_cv_metrics(
    rows: list[dict[str, Any]],
    trainer: Any,
    evaluator: Any,
    trainer_config: Mapping[str, Any],
    evaluator_config: Mapping[str, Any],
) -> tuple[dict[str, float], int]:
    """Fit + score one trainer over every purged WF-CV fold; pool metrics.

    Mirrors the multi-fold path in ``ml.experiments.runner.run_experiment``
    exactly (per-fold fit on the purged train block, score on the test
    block, ``_aggregate_fold_metrics`` pooling weighted by ``n_eval``) so
    the candidate's edge is measured the same way its registry metrics are
    produced. Raises if the CV config yields no folds (caught upstream).
    """
    folds = iter_folds(rows, evaluator_config)
    fold_metrics: list[Mapping[str, float]] = []
    for train_f, eval_f in folds:
        state = dict(trainer.fit(train_f, trainer_config))
        fold_metrics.append(dict(evaluator.score(state, eval_f, evaluator_config)))
    return _aggregate_fold_metrics(fold_metrics), len(folds)


def build_cv_config(
    base_evaluator_config: Mapping[str, Any],
    *,
    n_folds: int = 5,
    min_train_fraction: float = 0.5,
    label_horizon: int = 1,
    embargo_fraction: float = 0.0,
    embargo_n: int | None = None,
) -> dict[str, Any]:
    """Force a manifest's evaluator_config onto purged walk-forward CV.

    Keeps the authored ``target_column`` / ``metrics`` / ``time_column``
    (whatever the trainer + evaluator need) but overrides the split
    strategy and its knobs — exactly the override
    ``scripts/ml/eval_split_compare.py`` applies, so the no-leakage path
    is identical to the one S-MLOPT-S1 validated.
    """
    cfg = dict(base_evaluator_config)
    cfg["split_strategy"] = "purged_walk_forward"
    cfg["n_folds"] = n_folds
    cfg["min_train_fraction"] = min_train_fraction
    cfg["label_horizon"] = label_horizon
    if embargo_n is not None:
        cfg["embargo_n"] = embargo_n
    else:
        cfg["embargo_fraction"] = embargo_fraction
    return cfg


def compute_oos_edge(
    entry: Any,
    *,
    datasets_root: Path | str,
    baseline_trainer: str = DEFAULT_BASELINE_TRAINER,
    baseline_trainer_config: Mapping[str, Any] | None = None,
    n_folds: int = 5,
    min_train_fraction: float = 0.5,
    label_horizon: int = 1,
    embargo_fraction: float = 0.0,
    embargo_n: int | None = None,
) -> OOSEdgeResult | None:
    """Candidate-vs-baseline OOS edge for one registry ``entry`` under purged WF-CV.

    ``entry`` is a ``ml.registry.model_registry.RegistryEntry`` whose
    ``manifest`` is the full training manifest (as ``run_experiment``
    persists it). Returns ``None`` — so the gate is ``insufficient_data``
    — when the manifest can't be reconstructed, the dataset isn't on disk,
    there are too few rows for the requested folds, no shared primary
    metric resolves, or the baseline trainer is incompatible with the
    evaluator (e.g. a constant-mean baseline against a multiclass head).
    """
    try:
        manifest = TrainingManifest.from_dict(dict(entry.manifest))
    except (TypeError, ValueError):
        return None

    data_path = manifest.dataset.path_under(Path(datasets_root)) / "data.jsonl"
    if not data_path.is_file():
        return None
    try:
        rows = _load_jsonl(data_path)
    except (OSError, ValueError):
        return None
    if len(rows) < n_folds + 1:
        return None

    cv_cfg = build_cv_config(
        manifest.evaluator_config,
        n_folds=n_folds,
        min_train_fraction=min_train_fraction,
        label_horizon=label_horizon,
        embargo_fraction=embargo_fraction,
        embargo_n=embargo_n,
    )

    try:
        evaluator = _resolve_callable(manifest.evaluator)()
        candidate_trainer = _resolve_callable(manifest.trainer)()
        base_trainer = _resolve_callable(baseline_trainer)()
    except (ImportError, AttributeError, ValueError):
        return None

    base_cfg = baseline_trainer_config or manifest.trainer_config
    try:
        cand_metrics, n_run = _pooled_cv_metrics(
            rows, candidate_trainer, evaluator, manifest.trainer_config, cv_cfg
        )
        base_metrics, _ = _pooled_cv_metrics(
            rows, base_trainer, evaluator, base_cfg, cv_cfg
        )
    except Exception:  # noqa: BLE001 — any trainer/evaluator failure → no evidence
        return None

    metric = _pick_primary_metric(cand_metrics, base_metrics)
    if metric is None:
        return None
    candidate_score = float(cand_metrics[metric])
    baseline_score = float(base_metrics[metric])
    edge = orient_edge(metric, candidate_score, baseline_score)
    return OOSEdgeResult(
        model_id=entry.model_id,
        metric=metric,
        higher_is_better=_is_higher_better(metric),
        candidate_score=candidate_score,
        baseline_score=baseline_score,
        edge=edge,
        n_folds=n_run,
        n_rows=len(rows),
        candidate_trainer=manifest.trainer,
        baseline_trainer=baseline_trainer,
    )
