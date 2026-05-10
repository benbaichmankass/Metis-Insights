"""Umbrella CLI for the AI traders ML lifecycle (WS4).

Subcommands:
  build-dataset ...          — passthrough to ml.datasets `build`
  validate-dataset <path>    — passthrough to ml.datasets `validate`
  list-families              — passthrough to ml.datasets `list-families`
  train <manifest>           — run an experiment, register as candidate
  promote <id> <status>      — state transition with operator gates
  list-models [--status S]   — enumerate registry entries
  list-trainers              — introspection helper
  list-evaluators            — introspection helper
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .datasets.cli import main as datasets_main
from .experiments.runner import run_experiment
from .promotion import gates_for
from .registry.model_registry import ModelRegistry


def _cmd_train(args: argparse.Namespace) -> int:
    artifacts, entry = run_experiment(
        manifest_path=Path(args.manifest),
        datasets_root=Path(args.datasets_root),
        experiments_root=Path(args.experiments_root),
        registry_root=Path(args.registry_root),
        code_revision=args.commit_sha,
        register=not args.no_register,
    )
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
        args.model_id,
        args.new_status,
        by=args.by,
        reason=args.reason,
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
    return 0


def _cmd_list_evaluators(_args: argparse.Namespace) -> int:
    print("ml.evaluators.regression.RegressionEvaluator")
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
    }
    handler = dispatch.get(args.cmd)
    if handler is None:
        parser.error(f"unknown subcommand {args.cmd!r}")
        return 2
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
