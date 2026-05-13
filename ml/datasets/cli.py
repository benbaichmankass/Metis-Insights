"""CLI entrypoint for the dataset framework (WS3).

Usage:
    python -m ml.datasets list-families
    python -m ml.datasets build <family> --output-dir <dir> --version v001 \\
        --source <text> [--symbol-scope ...] [--timeframe ...] \\
        [--commit-sha ...] [--notes ...] [--overwrite] \\
        [-- <family-specific args>]
    python -m ml.datasets validate <dataset-path>

Family-specific arguments are passed after `--` and dispatched as
kwargs into the builder's `iter_rows(**kwargs)`.

Only the validator and `list-families` are exercised by tests today;
the `build` subcommand requires a real or fixture-built SQLite file
for the `backtest_results` family.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .registry import get_builder, list_families
from .validate import validate_dataset


def _cmd_list_families(_args: argparse.Namespace) -> int:
    for name in list_families():
        print(name)
    return 0


def _parse_kv_list(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in values or []:
        if "=" not in v:
            raise SystemExit(f"family arg {v!r} must be key=value")
        k, val = v.split("=", 1)
        out[k] = val
    return out


def _cmd_build(args: argparse.Namespace) -> int:
    builder = get_builder(args.family)
    iter_kwargs: dict[str, object] = dict(_parse_kv_list(args.family_arg))
    if "db_path" in iter_kwargs:
        iter_kwargs["db_path"] = Path(str(iter_kwargs["db_path"]))
    paths = builder.build(
        output_dir=Path(args.output_dir),
        version=args.version,
        source=args.source,
        symbol_scope=args.symbol_scope,
        timeframe=args.timeframe,
        timezone_name=args.timezone_name,
        commit_sha=args.commit_sha,
        notes=args.notes,
        overwrite=args.overwrite,
        **iter_kwargs,
    )
    print(f"wrote dataset under {paths.root}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_dataset(Path(args.dataset_path))
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ml.datasets")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-families")

    p_build = sub.add_parser("build")
    p_build.add_argument("family")
    p_build.add_argument("--output-dir", required=True)
    p_build.add_argument("--version", required=True)
    p_build.add_argument("--source", required=True)
    p_build.add_argument("--symbol-scope", default=None)
    p_build.add_argument("--timeframe", default=None)
    p_build.add_argument("--timezone-name", default="UTC")
    p_build.add_argument("--commit-sha", default=None)
    p_build.add_argument("--notes", default="")
    p_build.add_argument("--overwrite", action="store_true")
    p_build.add_argument(
        "family_arg",
        nargs="*",
        help="family-specific args as key=value (after `--`)",
    )

    p_val = sub.add_parser("validate")
    p_val.add_argument("dataset_path")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # parse_known_args lets family key=value kwargs appear anywhere in the
    # token stream (before or after --flags, with or without a -- separator).
    # parse_args rejects them when they trail optional flags due to a known
    # argparse limitation with nargs="*" positionals.
    args, extras = parser.parse_known_args(argv)
    extras = [e for e in extras if e != "--"]  # strip bare separator
    bad_flags = [e for e in extras if e.startswith("-")]
    if bad_flags:
        parser.error(f"unrecognized arguments: {' '.join(bad_flags)}")
    if args.cmd == "build":
        args.family_arg = list(args.family_arg or []) + extras
    elif extras:
        parser.error(f"unrecognized arguments: {' '.join(extras)}")
    if args.cmd == "list-families":
        return _cmd_list_families(args)
    if args.cmd == "build":
        return _cmd_build(args)
    if args.cmd == "validate":
        return _cmd_validate(args)
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
