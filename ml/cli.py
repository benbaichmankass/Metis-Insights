"""Umbrella CLI for the AI traders ML lifecycle (WS4 + WS4-FU + WS8).

Subcommands:
  build-dataset ...           — passthrough to ml.datasets `build`
  validate-dataset <path>     — passthrough to ml.datasets `validate`
  list-families               — passthrough to ml.datasets `list-families`
  train <manifest>            — run an experiment, register as candidate
  promote <id> <status>       — state transition with operator gates
  list-models [--status S]    — enumerate registry entries
  list-trainers               — introspection helper
  list-evaluators             — introspection helper
  compare <id-a> <id-b>       — side-by-side metric diff (WS4-FU)
  shadow-inspect              — tail shadow_predictions.jsonl with filters (WS8-PART-1)
  shadow-stats                — per-(model_id, stage) aggregate over the audit log (WS8-PART-1)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .datasets.cli import main as datasets_main
from .experiments.runner import (
    EMPTY_DATASET_EXIT_CODE,
    EmptyDatasetError,
    run_experiment,
)
from .promotion import gates_for
from .registry.model_registry import ModelRegistry
from .shadow.inspector import (
    aggregate,
    filter_records,
    format_inspect_table,
    format_stats_table,
    iter_records,
)


def _cmd_train(args: argparse.Namespace) -> int:
    try:
        artifacts, entry = run_experiment(
            manifest_path=Path(args.manifest),
            datasets_root=Path(args.datasets_root),
            experiments_root=Path(args.experiments_root),
            registry_root=Path(args.registry_root),
            code_revision=args.commit_sha,
            register=not args.no_register,
        )
    except EmptyDatasetError as exc:
        # 0-row dataset is "data not ready yet", not a training failure.
        # Emit a structured JSON line and exit 78 so run_training_cycle.sh
        # can surface this as `manifest_skipped` rather than failed.
        print(json.dumps({
            "skipped": True,
            "reason": "empty_dataset",
            "dataset_path": str(exc.data_path),
            "detail": str(exc),
        }, indent=2, sort_keys=True))
        return EMPTY_DATASET_EXIT_CODE
    print(json.dumps({
        "experiment_dir": str(artifacts.experiment_dir),
        "metrics": dict(artifacts.metrics),
        "registered": entry is not None,
        "model_id": entry.model_id if entry else None,
    }, indent=2, sort_keys=True))
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    registry = ModelRegistry(Path(args.registry_root))
    current = registry.get(args.model_id)
    gates = gates_for(current.status, args.new_status)
    if gates and not args.gates_acknowledged:
        sys.stderr.write(
            f"transition {current.status!r} -> {args.new_status!r} requires gates: "
            f"{gates}; pass --gates-acknowledged once they are documented in --reason.\n"
        )
        return 2
    updated = registry.promote(
        args.model_id, args.new_status, by=args.by, reason=args.reason,
    )
    print(json.dumps(updated.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_list_models(args: argparse.Namespace) -> int:
    registry = ModelRegistry(Path(args.registry_root))
    entries = registry.list(status=args.status)
    payload = [e.to_dict() for e in entries]
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_list_trainers(_args: argparse.Namespace) -> int:
    print("ml.trainers.constant_baseline.ConstantPredictionTrainer")
    print("ml.trainers.per_strategy_winrate.PerStrategyWinRateTrainer")
    return 0


def _cmd_list_evaluators(_args: argparse.Namespace) -> int:
    print("ml.evaluators.regression.RegressionEvaluator")
    print("ml.evaluators.classification.ClassificationEvaluator")
    return 0


_DEFAULT_SHADOW_LOG = Path("runtime_logs/shadow_predictions.jsonl")


def _parse_since(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(
            f"--since must be ISO-8601 (e.g. '2026-05-10' or "
            f"'2026-05-10T12:00:00+00:00'); got {raw!r} ({exc})"
        ) from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _cmd_shadow_inspect(args: argparse.Namespace) -> int:
    records = filter_records(
        iter_records(args.log),
        model_id=args.model_id,
        stage=args.stage,
        since=_parse_since(args.since),
    )
    table = format_inspect_table(records, limit=args.limit)
    if not table:
        print("(no shadow predictions matched)")
        return 0
    print(table)
    return 0


def _cmd_shadow_drift(args: argparse.Namespace) -> int:
    from datetime import timedelta

    from .shadow.drift import compute_drift

    # Cutoffs anchored to "now" — reference covers the OLDER
    # `reference_days` ending at the start of the current window;
    # current covers the most recent `current_days`. This avoids
    # overlap.
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=args.current_days)
    reference_start = current_start - timedelta(days=args.reference_days)
    all_records = list(filter_records(
        iter_records(args.log),
        model_id=args.model_id,
        stage=args.stage,
    ))
    reference_scores = [
        r.score for r in all_records
        if reference_start <= r.predicted_at_utc < current_start
    ]
    current_scores = [
        r.score for r in all_records
        if r.predicted_at_utc >= current_start
    ]
    if not reference_scores or not current_scores:
        print(json.dumps({
            "model_id": args.model_id,
            "stage": args.stage,
            "reference_count": len(reference_scores),
            "current_count": len(current_scores),
            "verdict": "insufficient_data",
            "reference_window_start": reference_start.isoformat(),
            "current_window_start": current_start.isoformat(),
        }, indent=2))
        return 0
    report = compute_drift(
        reference_scores, current_scores,
        bins=args.bins, score_min=args.score_min, score_max=args.score_max,
    )
    print(json.dumps({
        "model_id": args.model_id,
        "stage": args.stage,
        "reference_window_start": reference_start.isoformat(),
        "current_window_start": current_start.isoformat(),
        "reference_count": report.reference.count,
        "current_count": report.current.count,
        "reference_mean": report.reference.mean,
        "current_mean": report.current.mean,
        "reference_stdev": report.reference.stdev,
        "current_stdev": report.current.stdev,
        "ks": report.ks,
        "ks_verdict": report.ks_verdict,
        "psi": report.psi,
        "psi_verdict": report.psi_verdict,
        "overall_verdict": report.overall_verdict,
    }, indent=2))
    return 0


def _cmd_shadow_stats(args: argparse.Namespace) -> int:
    records = filter_records(
        iter_records(args.log),
        model_id=args.model_id,
        stage=args.stage,
        since=_parse_since(args.since),
    )
    stats = aggregate(records)
    table = format_stats_table(stats)
    if not table:
        print("(no shadow predictions matched)")
        return 0
    print(table)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    registry = ModelRegistry(Path(args.registry_root))
    a = registry.get(args.model_id_a)
    b = registry.get(args.model_id_b)
    common = sorted(set(a.metrics) & set(b.metrics))
    metric_diffs = []
    for metric in common:
        va = float(a.metrics[metric])
        vb = float(b.metrics[metric])
        metric_diffs.append({
            "metric": metric,
            "a": va,
            "b": vb,
            "delta": vb - va,
        })
    a_only = sorted(set(a.metrics) - set(b.metrics))
    b_only = sorted(set(b.metrics) - set(a.metrics))
    print(json.dumps({
        "model_a": {
            "id": a.model_id,
            "status": a.status,
            "code_revision": a.code_revision,
        },
        "model_b": {
            "id": b.model_id,
            "status": b.status,
            "code_revision": b.code_revision,
        },
        "metric_diffs": metric_diffs,
        "a_only_metrics": a_only,
        "b_only_metrics": b_only,
    }, indent=2, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train")
    p_train.add_argument("manifest")
    p_train.add_argument("--datasets-root", default="./datasets-out")
    p_train.add_argument("--experiments-root", default="./ml/experiments-runs")
    p_train.add_argument("--registry-root", default="./ml/registry-store")
    p_train.add_argument("--commit-sha", default=None)
    p_train.add_argument("--no-register", action="store_true")

    p_promote = sub.add_parser("promote")
    p_promote.add_argument("model_id")
    p_promote.add_argument("new_status")
    p_promote.add_argument("--registry-root", default="./ml/registry-store")
    p_promote.add_argument("--by", required=True)
    p_promote.add_argument("--reason", required=True)
    p_promote.add_argument("--gates-acknowledged", action="store_true")

    p_list = sub.add_parser("list-models")
    p_list.add_argument("--registry-root", default="./ml/registry-store")
    p_list.add_argument("--status", default=None)

    sub.add_parser("list-trainers")
    sub.add_parser("list-evaluators")

    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("model_id_a")
    p_cmp.add_argument("model_id_b")
    p_cmp.add_argument("--registry-root", default="./ml/registry-store")

    p_shi = sub.add_parser(
        "shadow-inspect",
        help="tail shadow_predictions.jsonl with filters (WS8-PART-1)",
    )
    p_shi.add_argument("--log", type=Path, default=_DEFAULT_SHADOW_LOG)
    p_shi.add_argument("--limit", type=int, default=50)
    p_shi.add_argument("--model-id", default=None)
    p_shi.add_argument("--stage", default=None)
    p_shi.add_argument(
        "--since", default=None,
        help="ISO-8601 timestamp; only show records at/after this UTC instant",
    )

    p_shs = sub.add_parser(
        "shadow-stats",
        help="per-(model_id, stage) aggregate over the audit log (WS8-PART-1)",
    )
    p_shs.add_argument("--log", type=Path, default=_DEFAULT_SHADOW_LOG)
    p_shs.add_argument("--model-id", default=None)
    p_shs.add_argument("--stage", default=None)
    p_shs.add_argument(
        "--since", default=None,
        help="ISO-8601 timestamp; only aggregate records at/after this UTC instant",
    )

    p_shd = sub.add_parser(
        "shadow-drift",
        help=(
            "window-over-window drift report for one model_id "
            "(WS8-PART-3): KS + PSI + summary stats"
        ),
    )
    p_shd.add_argument("--log", type=Path, default=_DEFAULT_SHADOW_LOG)
    p_shd.add_argument(
        "--model-id", required=True,
        help="model_id to slice on (drift is per-model)",
    )
    p_shd.add_argument("--stage", default=None)
    p_shd.add_argument(
        "--reference-days", type=float, default=30.0,
        help=(
            "size of the reference window in days, measured backwards "
            "from --reference-end (default 30)"
        ),
    )
    p_shd.add_argument(
        "--current-days", type=float, default=7.0,
        help=(
            "size of the current window in days, measured backwards "
            "from now (default 7)"
        ),
    )
    p_shd.add_argument(
        "--bins", type=int, default=10,
        help="histogram bins for PSI (default 10)",
    )
    p_shd.add_argument(
        "--score-min", type=float, default=0.0,
        help="lower bound for histogram clamp (default 0.0)",
    )
    p_shd.add_argument(
        "--score-max", type=float, default=1.0,
        help="upper bound for histogram clamp (default 1.0)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        sys.stderr.write("usage: python -m ml <subcommand> [...]\n")
        return 2
    cmd = argv[0]
    if cmd == "build-dataset":
        return datasets_main(["build", *argv[1:]])
    if cmd == "validate-dataset":
        return datasets_main(["validate", *argv[1:]])
    if cmd == "list-families":
        return datasets_main(["list-families"])

    parser = _build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "train": _cmd_train,
        "promote": _cmd_promote,
        "list-models": _cmd_list_models,
        "list-trainers": _cmd_list_trainers,
        "list-evaluators": _cmd_list_evaluators,
        "compare": _cmd_compare,
        "shadow-inspect": _cmd_shadow_inspect,
        "shadow-stats": _cmd_shadow_stats,
        "shadow-drift": _cmd_shadow_drift,
    }
    handler = dispatch.get(args.cmd)
    if handler is None:
        parser.error(f"unknown subcommand {args.cmd!r}")
        return 2
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
