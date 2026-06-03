"""Umbrella CLI for the AI traders ML lifecycle (WS4 + WS4-FU + WS8).

Subcommands:
  build-dataset ...           — passthrough to ml.datasets `build`
  validate-dataset <path>     — passthrough to ml.datasets `validate`
  list-families               — passthrough to ml.datasets `list-families`
  train <manifest>            — run an experiment, register as candidate
  promote <id> <status>       — legacy WS4 status transition with operator gates
  promote-stage <id> <stage>  — WS7 target_deployment_stage transition
  list-models [--status S]    — enumerate registry entries
  list-trainers               — introspection helper
  list-evaluators             — introspection helper
  compare <id-a> <id-b>       — side-by-side metric diff (WS4-FU)
  shadow-inspect              — tail shadow_predictions.jsonl with filters (WS8-PART-1)
  shadow-stats                — per-(model_id, stage) aggregate over the audit log (WS8-PART-1)
  shadow-drift                — window-over-window drift report for one model_id (WS8-PART-3)
  backfill-shadow-predictions — retroactive-decision replay of every historical trade (2026-05-19)
  model-attribution           — per-model live attribution: shadow scores vs realized outcomes (go-live)
  gate-check <id>             — computed shadow→advisory promotion gates (go/no-go packet; go-live)
  stage-guard                 — propose promote/demote/hold for every model (read-only; go-live)
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
from .registry.model_registry import ModelRegistry, RegistryError
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


def _cmd_promote_stage(args: argparse.Namespace) -> int:
    # WS7 deployment-stage transition (orthogonal to legacy WS4 status).
    # Bulk-friendly: if --all-pre-shadow is set, transition every model
    # whose current stage is in {research_only, candidate, backtest_approved}
    # to `shadow` in one invocation (legal one-hop per the 2026-05-19 graph).
    registry = ModelRegistry(Path(args.registry_root))
    if args.all_pre_shadow:
        if args.new_stage != "shadow":
            sys.stderr.write(
                "--all-pre-shadow only supports --new-stage=shadow; "
                f"got {args.new_stage!r}\n"
            )
            return 2
        pre_shadow = {"research_only", "candidate", "backtest_approved"}
        transitioned: list[dict] = []
        skipped: list[dict] = []
        for entry in registry.list():
            if entry.target_deployment_stage not in pre_shadow:
                skipped.append({
                    "model_id": entry.model_id,
                    "current_stage": entry.target_deployment_stage,
                })
                continue
            updated = registry.promote_stage(
                entry.model_id, "shadow", by=args.by, reason=args.reason,
            )
            transitioned.append({
                "model_id": updated.model_id,
                "from_stage": entry.target_deployment_stage,
                "to_stage": updated.target_deployment_stage,
            })
        print(json.dumps({
            "transitioned": transitioned,
            "skipped": skipped,
            "transitioned_count": len(transitioned),
            "skipped_count": len(skipped),
        }, indent=2, sort_keys=True))
        return 0
    if not args.model_id:
        sys.stderr.write(
            "promote-stage requires either <model_id> or --all-pre-shadow\n"
        )
        return 2
    try:
        updated = registry.promote_stage(
            args.model_id, args.new_stage, by=args.by, reason=args.reason,
        )
    except RegistryError as exc:
        sys.stderr.write(f"promote-stage failed: {exc}\n")
        return 1
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
_DEFAULT_BACKFILL_LOG = Path("runtime_logs/shadow_predictions_backfill.jsonl")


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


def _cmd_backfill_shadow_predictions(args: argparse.Namespace) -> int:
    # Retroactive-decision backfill: replay every historical trade
    # through every shadow-stage model and write the results to a
    # one-shot JSONL file. See `ml/shadow/backfill.py` for the
    # leakage/contract rules.
    from .shadow.backfill import run_backfill

    registry = ModelRegistry(Path(args.registry_root))
    summary = run_backfill(
        db_path=Path(args.db),
        registry=registry,
        output_path=Path(args.output),
        include_rejected=args.include_rejected,
        limit=args.limit if args.limit and args.limit > 0 else None,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _cmd_model_attribution(args: argparse.Namespace) -> int:
    # Per-model live attribution: join shadow scores to realized trade
    # outcomes. Decision-support only — never mutates anything.
    from .promotion.attribution import compute_attribution

    attrs = compute_attribution(
        db_path=args.db,
        shadow_log=args.shadow_log,
        backfill_log=args.backfill_log,
        include_demo=args.include_demo,
    )
    if args.model_id:
        attrs = [a for a in attrs if a.model_id == args.model_id]
    print(json.dumps([a.to_dict() for a in attrs], indent=2, sort_keys=True))
    return 0


def _cmd_gate_check(args: argparse.Namespace) -> int:
    # Computed promotion gates for one model + target stage. Reports a
    # go/no-go packet; never promotes.
    from .promotion.attribution import compute_attribution
    from .promotion.gates import evaluate_gates
    from .promotion.stage_guard import _drift_for_model

    registry = ModelRegistry(Path(args.registry_root))
    try:
        entry = registry.get(args.model_id)
    except RegistryError as exc:
        sys.stderr.write(f"gate-check failed: {exc}\n")
        return 1
    attr = None
    if args.db:
        attrs = compute_attribution(
            db_path=args.db, shadow_log=args.shadow_log,
            backfill_log=args.backfill_log, include_demo=args.include_demo,
        )
        attr = next((a for a in attrs if a.model_id == args.model_id), None)
    records = list(iter_records(args.shadow_log))
    drift = _drift_for_model(
        records, args.model_id,
        reference_days=args.reference_days, current_days=args.current_days,
    )
    # Offline champion-challenger edge under purged WF-CV. Only computed
    # when --datasets-root is supplied (the trainer VM, where datasets
    # live); otherwise the oos_edge gate reports insufficient_data.
    oos_edge = None
    if args.datasets_root:
        from .promotion.oos_edge import compute_oos_edge

        oos_edge = compute_oos_edge(
            entry,
            datasets_root=args.datasets_root,
            baseline_trainer=args.baseline_trainer,
            n_folds=args.n_folds,
            label_horizon=args.label_horizon,
            embargo_fraction=args.embargo_fraction,
        )
    report = evaluate_gates(
        entry, target_stage=args.target_stage, attribution=attr,
        drift=drift, oos_edge=oos_edge,
    )
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_stage_guard(args: argparse.Namespace) -> int:
    # Evaluate every model: propose promote / demote / hold. Read-only —
    # the operator runs `promote-stage` to act on a proposal.
    from .promotion.stage_guard import run_stage_guard

    proposals = run_stage_guard(
        registry_root=args.registry_root,
        db_path=args.db,
        shadow_log=args.shadow_log,
        backfill_log=args.backfill_log,
        reference_days=args.reference_days,
        current_days=args.current_days,
        include_demo=args.include_demo,
        datasets_root=args.datasets_root,
    )
    payload = [p.to_dict() for p in proposals]
    print(json.dumps({
        "proposals": payload,
        "summary": {
            "promote": [p.model_id for p in proposals if p.action == "promote"],
            "demote": [p.model_id for p in proposals if p.action == "demote"],
            "hold_count": sum(1 for p in proposals if p.action == "hold"),
            "total": len(proposals),
        },
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

    p_promote_stage = sub.add_parser(
        "promote-stage",
        help=(
            "WS7 target_deployment_stage transition. Pass --all-pre-shadow "
            "to bulk-migrate every research_only/candidate/backtest_approved "
            "entry into shadow in one go."
        ),
    )
    p_promote_stage.add_argument("model_id", nargs="?", default=None)
    p_promote_stage.add_argument(
        "--new-stage", required=True,
        help="target_deployment_stage to transition into",
    )
    p_promote_stage.add_argument(
        "--registry-root", default="./ml/registry-store",
    )
    p_promote_stage.add_argument("--by", required=True)
    p_promote_stage.add_argument("--reason", required=True)
    p_promote_stage.add_argument(
        "--all-pre-shadow", action="store_true",
        help=(
            "transition every entry whose target_deployment_stage is "
            "research_only/candidate/backtest_approved into shadow "
            "(only valid with --new-stage=shadow)"
        ),
    )

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

    p_bf = sub.add_parser(
        "backfill-shadow-predictions",
        help=(
            "Retroactive-decision backfill (2026-05-19): score every "
            "historical trade in `trade_journal.db` against every "
            "shadow-stage model in the registry and write the results "
            "to a JSONL file. Records carry `backfill_kind: "
            "retroactive_decision` + `trade_id` so the trades-scores "
            "endpoint joins them deterministically and the drift "
            "endpoint can filter them out."
        ),
    )
    p_bf.add_argument(
        "--db",
        required=True,
        help="Path to trade_journal.db (the synced one on the trainer VM)",
    )
    p_bf.add_argument(
        "--registry-root", default="./ml/registry-store",
    )
    p_bf.add_argument(
        "--output",
        default="./runtime_logs/shadow_predictions_backfill.jsonl",
        help="Where to write the backfill JSONL (truncated on every run)",
    )
    p_bf.add_argument(
        "--include-rejected", action="store_true", default=True,
        help=(
            "Score rejected + exchange_rejected signals too (default). "
            "Pass --no-include-rejected to score only "
            "open/closed/orphaned trades."
        ),
    )
    p_bf.add_argument(
        "--no-include-rejected", dest="include_rejected",
        action="store_false",
    )
    p_bf.add_argument(
        "--limit", type=int, default=0,
        help="Cap rows for testing; 0 (default) = no cap",
    )

    p_attr = sub.add_parser(
        "model-attribution",
        help=(
            "per-model live attribution: join shadow scores to realized "
            "trade outcomes (AUC + brier vs base-rate). Decision-support; "
            "never mutates."
        ),
    )
    p_attr.add_argument(
        "--db", required=True,
        help="Path to trade_journal.db (the synced copy on the trainer VM)",
    )
    p_attr.add_argument("--shadow-log", type=Path, default=_DEFAULT_SHADOW_LOG)
    p_attr.add_argument("--backfill-log", type=Path, default=_DEFAULT_BACKFILL_LOG)
    p_attr.add_argument("--model-id", default=None, help="optional filter")
    p_attr.add_argument("--include-demo", action="store_true", default=False)

    p_gate = sub.add_parser(
        "gate-check",
        help=(
            "computed shadow→advisory promotion gates for one model "
            "(go/no-go evidence packet). Reports only; never promotes."
        ),
    )
    p_gate.add_argument("model_id")
    p_gate.add_argument("--target-stage", default="advisory")
    p_gate.add_argument("--registry-root", default="./ml/registry-store")
    p_gate.add_argument(
        "--db", default=None,
        help="trade_journal.db for the live-attribution gates (optional)",
    )
    p_gate.add_argument("--shadow-log", type=Path, default=_DEFAULT_SHADOW_LOG)
    p_gate.add_argument("--backfill-log", type=Path, default=_DEFAULT_BACKFILL_LOG)
    p_gate.add_argument("--reference-days", type=float, default=30.0)
    p_gate.add_argument("--current-days", type=float, default=7.0)
    p_gate.add_argument("--include-demo", action="store_true", default=False)
    p_gate.add_argument(
        "--datasets-root", default=None,
        help=(
            "datasets-out root (trainer VM). When set, computes the offline "
            "OOS-edge-vs-baseline gate under purged WF-CV; omit and that "
            "gate reports insufficient_data."
        ),
    )
    p_gate.add_argument(
        "--baseline-trainer",
        default="ml.trainers.constant_baseline.ConstantPredictionTrainer",
        help="baseline trainer qualname for the OOS-edge comparison",
    )
    p_gate.add_argument(
        "--n-folds", type=int, default=5,
        help="purged WF-CV fold count for the OOS-edge gate",
    )
    p_gate.add_argument(
        "--label-horizon", type=int, default=1,
        help="purge width (rows each label spans forward) for the OOS-edge gate",
    )
    p_gate.add_argument(
        "--embargo-fraction", type=float, default=0.0,
        help="embargo buffer as a fraction of the dataset for the OOS-edge gate",
    )

    p_guard = sub.add_parser(
        "stage-guard",
        help=(
            "evaluate every model and propose promote / demote / hold. "
            "Read-only — the operator runs promote-stage to act."
        ),
    )
    p_guard.add_argument("--registry-root", default="./ml/registry-store")
    p_guard.add_argument(
        "--db", default=None,
        help="trade_journal.db for the live-attribution signals (optional)",
    )
    p_guard.add_argument("--shadow-log", type=Path, default=_DEFAULT_SHADOW_LOG)
    p_guard.add_argument("--backfill-log", type=Path, default=_DEFAULT_BACKFILL_LOG)
    p_guard.add_argument("--reference-days", type=float, default=30.0)
    p_guard.add_argument("--current-days", type=float, default=7.0)
    p_guard.add_argument("--include-demo", action="store_true", default=False)
    p_guard.add_argument(
        "--datasets-root", default=None,
        help=(
            "datasets-out root (trainer VM). When set, computes the offline "
            "OOS-edge gate for every shadow-stage model so promote proposals "
            "carry champion-challenger evidence."
        ),
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
        "promote-stage": _cmd_promote_stage,
        "list-models": _cmd_list_models,
        "list-trainers": _cmd_list_trainers,
        "list-evaluators": _cmd_list_evaluators,
        "compare": _cmd_compare,
        "shadow-inspect": _cmd_shadow_inspect,
        "shadow-stats": _cmd_shadow_stats,
        "shadow-drift": _cmd_shadow_drift,
        "backfill-shadow-predictions": _cmd_backfill_shadow_predictions,
        "model-attribution": _cmd_model_attribution,
        "gate-check": _cmd_gate_check,
        "stage-guard": _cmd_stage_guard,
    }
    handler = dispatch.get(args.cmd)
    if handler is None:
        parser.error(f"unknown subcommand {args.cmd!r}")
        return 2
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
