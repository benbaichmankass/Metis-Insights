#!/usr/bin/env python3
"""Optuna hyperparameter search for a LightGBM manifest, over PURGED WF-CV folds.

S-MLOPT-S3 (M14 Session 0.3). Tier-1 trainer-side tooling — never edits a
manifest on disk, never registers a model, never touches a live-path file. It
emits the best `lgbm_params` (+ `n_iter`, optional `class_weight`) it finds as a
**proposal**; adopting it in a manifest is Tier-3 (operator-gated).

**No-leakage guardrail (the whole point):** the search is scored on the
S-MLOPT-S1 **purged & embargoed walk-forward CV**, NOT the optimistic holdout —
so HPO can't tune to leakage. The manifest's own `split_strategy` is ignored;
this tool forces `purged_walk_forward` with the CV knobs below.

The CV objective is a pure function (`cv_evaluate`) with no Optuna dependency,
so it is unit-testable without Optuna installed; the Optuna study is a thin
wrapper around it.

Run on the trainer VM (datasets live there; install optuna into the venv first):

    cd /home/ubuntu/ict-trading-bot && . .venv/bin/activate && pip install optuna
    python -m scripts.ml.hpo_sweep \
      --manifest ml/configs/btc-regime-1h-lgbm-v2.yaml \
      --datasets-root datasets-out --n-trials 40 \
      --metric-key f1_volatile --direction maximize \
      --n-folds 5 --label-horizon 20

Output is a single JSON object on stdout: baseline (current params) vs best,
the proposed `trainer_config` patch, and per-trial bookkeeping.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.experiments.runner import _aggregate_fold_metrics, _load_jsonl  # noqa: E402
from ml.experiments.splitters import iter_folds  # noqa: E402
from ml.manifest import TrainingManifest  # noqa: E402


def _resolve(qualname: str):
    mod, _, attr = qualname.rpartition(".")
    return getattr(importlib.import_module(mod), attr)


def cv_evaluate(
    *,
    trainer,
    evaluator,
    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    trainer_config: Mapping[str, Any],
    evaluator_config: Mapping[str, Any],
    report=None,
) -> dict[str, float]:
    """Fit + score each purged WF-CV fold and pool the metrics.

    Pure (no Optuna). `report(step, running_pooled_metric_dict)` is an optional
    callback invoked after each fold so a pruner can act on the running estimate.
    Returns the pooled metric dict (same pooling as the runner's CV path).
    """
    fold_metrics: list[Mapping[str, float]] = []
    for i, (train_rows, eval_rows) in enumerate(folds):
        state = dict(trainer.fit(train_rows, trainer_config))
        scored = dict(evaluator.score(state, eval_rows, evaluator_config))
        fold_metrics.append(scored)
        if report is not None:
            report(i, _aggregate_fold_metrics(fold_metrics))
    return _aggregate_fold_metrics(fold_metrics)


def _suggest_params(trial, base_lgbm: Mapping[str, Any]) -> dict[str, Any]:
    """LightGBM search space (TPE). Centred wide enough to move off the
    hard-coded defaults without going degenerate on small shards."""
    return {
        "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.2, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 7, 127),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 5, 200, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
        "bagging_freq": trial.suggest_int("bagging_freq", 0, 10),
        "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 5.0, log=True),
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 5.0, log=True),
    }


def _baseline_trial_params(
    base_lgbm: Mapping[str, Any], base_n_iter: int
) -> dict[str, Any]:
    """Enqueue the manifest's CURRENT config as trial 0 so best-vs-baseline is
    measured on the same folds. Missing keys fall back to the search-space
    midpoints Optuna would otherwise have to discover."""
    defaults = {
        "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 20,
        "feature_fraction": 0.9, "bagging_fraction": 0.9, "bagging_freq": 5,
        "lambda_l1": 1e-3, "lambda_l2": 1e-3,
    }
    out = {k: base_lgbm.get(k, defaults[k]) for k in defaults}
    out["n_iter"] = base_n_iter
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--datasets-root", default=Path("datasets-out"), type=Path)
    ap.add_argument("--n-trials", type=int, default=40)
    ap.add_argument("--metric-key", default="f1_volatile")
    ap.add_argument("--direction", choices=["maximize", "minimize"], default="maximize")
    ap.add_argument("--seed", type=int, default=42)
    # Purged WF-CV knobs (forced — manifest split_strategy is ignored).
    ap.add_argument("--n-folds", type=int, default=5)
    ap.add_argument("--min-train-fraction", type=float, default=0.5)
    ap.add_argument("--label-horizon", type=int, default=1)
    ap.add_argument("--embargo-fraction", type=float, default=0.0)
    ap.add_argument("--n-iter-min", type=int, default=50)
    ap.add_argument("--n-iter-max", type=int, default=500)
    ap.add_argument(
        "--tune-class-weight", default=None,
        help="Class label whose weight to also search (1..--class-weight-max). "
             "For imbalanced trade-outcome models that ship with no class_weight.",
    )
    ap.add_argument("--class-weight-max", type=float, default=50.0)
    args = ap.parse_args(argv)

    import optuna  # noqa: PLC0415 — lazy so the module imports without optuna

    manifest = TrainingManifest.from_yaml(args.manifest)
    dataset_dir = manifest.dataset.path_under(args.datasets_root)
    rows = _load_jsonl(dataset_dir / "data.jsonl")
    if not rows:
        sys.stderr.write("dataset is empty\n")
        return 2

    base_trainer_cfg = dict(manifest.trainer_config)
    base_lgbm = dict(base_trainer_cfg.get("lgbm_params") or {})
    base_n_iter = int(base_trainer_cfg.get("n_iter", 200))
    base_class_weight = base_trainer_cfg.get("class_weight")

    # Force purged WF-CV (the no-leakage guardrail) regardless of the manifest.
    cv_eval_cfg = dict(manifest.evaluator_config)
    cv_eval_cfg.update({
        "split_strategy": "purged_walk_forward",
        "n_folds": args.n_folds,
        "min_train_fraction": args.min_train_fraction,
        "label_horizon": args.label_horizon,
        "embargo_fraction": args.embargo_fraction,
    })
    folds = iter_folds(rows, cv_eval_cfg)  # deterministic; reused across trials

    trainer = _resolve(manifest.trainer)()
    evaluator = _resolve(manifest.evaluator)()
    score_cfg = dict(manifest.evaluator_config)  # score with the model's real eval cfg

    def _trainer_cfg_for(params: Mapping[str, Any]) -> dict[str, Any]:
        cfg = dict(base_trainer_cfg)
        lgbm = {**base_lgbm}
        for k, v in params.items():
            if k in ("n_iter", "_class_weight"):
                continue
            lgbm[k] = v
        cfg["lgbm_params"] = lgbm
        cfg["n_iter"] = int(params.get("n_iter", base_n_iter))
        if "_class_weight" in params and args.tune_class_weight:
            cw = dict(base_class_weight or {})
            cw.setdefault("range", 1.0)
            cw[args.tune_class_weight] = float(params["_class_weight"])
            cfg["class_weight"] = cw
        return cfg

    def objective(trial: "optuna.Trial") -> float:
        params = _suggest_params(trial, base_lgbm)
        params["n_iter"] = trial.suggest_int(
            "n_iter", args.n_iter_min, args.n_iter_max, step=25
        )
        if args.tune_class_weight:
            params["_class_weight"] = trial.suggest_float(
                "_class_weight", 1.0, args.class_weight_max, log=True
            )
        trainer_cfg = _trainer_cfg_for(params)

        def _report(step: int, pooled: dict[str, float]) -> None:
            if args.metric_key in pooled:
                trial.report(pooled[args.metric_key], step)
                if trial.should_prune():
                    raise optuna.TrialPruned()

        pooled = cv_evaluate(
            trainer=trainer, evaluator=evaluator, folds=folds,
            trainer_config=trainer_cfg, evaluator_config=score_cfg, report=_report,
        )
        if args.metric_key not in pooled:
            raise optuna.TrialPruned()
        return float(pooled[args.metric_key])

    # Baseline (current manifest params) under the same folds.
    baseline_cfg = _trainer_cfg_for(_baseline_trial_params(base_lgbm, base_n_iter))
    baseline_pooled = cv_evaluate(
        trainer=trainer, evaluator=evaluator, folds=folds,
        trainer_config=baseline_cfg, evaluator_config=score_cfg,
    )
    baseline_value = baseline_pooled.get(args.metric_key)

    study = optuna.create_study(
        direction=args.direction,
        sampler=optuna.samplers.TPESampler(seed=args.seed),
        pruner=optuna.pruners.MedianPruner(n_warmup_steps=1),
    )
    study.enqueue_trial(_baseline_trial_params(base_lgbm, base_n_iter))
    study.optimize(objective, n_trials=args.n_trials)

    best = study.best_trial
    proposed_lgbm = {**base_lgbm}
    for k, v in best.params.items():
        if k in ("n_iter", "_class_weight"):
            continue
        proposed_lgbm[k] = v
    proposed_tc: dict[str, Any] = {"lgbm_params": proposed_lgbm, "n_iter": best.params["n_iter"]}
    if args.tune_class_weight and "_class_weight" in best.params:
        cw = dict(base_class_weight or {})
        cw.setdefault("range", 1.0)
        cw[args.tune_class_weight] = best.params["_class_weight"]
        proposed_tc["class_weight"] = cw

    improvement = None
    if baseline_value is not None:
        improvement = best.value - baseline_value
        if args.direction == "minimize":
            improvement = baseline_value - best.value

    print(json.dumps({
        "model_id": manifest.model_id,
        "metric_key": args.metric_key,
        "direction": args.direction,
        "cv": {
            "split_strategy": "purged_walk_forward",
            "n_folds": len(folds), "label_horizon": args.label_horizon,
            "embargo_fraction": args.embargo_fraction,
            "min_train_fraction": args.min_train_fraction,
        },
        "n_trials": args.n_trials,
        "n_complete": len([t for t in study.trials if t.state.name == "COMPLETE"]),
        "n_pruned": len([t for t in study.trials if t.state.name == "PRUNED"]),
        "baseline_value": baseline_value,
        "best_value": best.value,
        "improvement_vs_baseline": improvement,
        "proposed_trainer_config": proposed_tc,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
