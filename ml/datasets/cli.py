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
import inspect
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any

from .registry import get_builder, list_families
from .validate import validate_dataset

# Reserved kwargs of `DatasetBuilder.build()` — if a family-arg `key=value`
# pair collides with one of these, it's lifted out of `iter_kwargs` into the
# corresponding explicit argparse arg (but only when the explicit arg is
# unset). This prevents `TypeError: build() got multiple values for keyword
# argument 'timeframe'` when a caller redundantly provides both
# `--timeframe 1h` and `timeframe=1h` (as `scripts/ops/build_trainer_datasets.sh`
# does for `market_raw`).
_BUILDER_BUILD_RESERVED: frozenset[str] = frozenset({
    "output_dir",
    "version",
    "source",
    "symbol_scope",
    "timeframe",
    "timezone_name",
    "commit_sha",
    "notes",
    "overwrite",
})


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


def _coerce_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes", "y", "on"}:
        return True
    if v in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError(f"cannot coerce {value!r} to bool")


def _coerce_to_annotation(value: str, annotation: Any) -> Any:
    """Coerce a CLI string `key=value` value to the builder's declared type.

    Honours `inspect.signature(builder.iter_rows)` parameter annotations.
    Handles `Path`, `int`, `float`, `bool`, and `Optional[T]` / `T | None` /
    `T | U` unions — when a union has multiple non-None members we prefer
    `Path` over numerics over `bool` over `str` (a deliberate ordering: callers
    who declare e.g. `Path | str` typically want the Path form because that's
    where the `.is_file()` / `.is_dir()` predicates live).

    Anything we can't coerce (missing annotation, exotic type) passes through
    as the original string — keeps the historical behaviour intact for kwargs
    that have always been strings.
    """
    if annotation is inspect.Parameter.empty:
        return value

    origin = typing.get_origin(annotation)
    # Handle Union / Optional. `typing.get_origin(Optional[X])` returns
    # `typing.Union` on 3.8+; on 3.10+ `X | None` returns
    # `types.UnionType`. Both expose args via `typing.get_args`.
    if origin is typing.Union or origin is types.UnionType:
        candidates = [a for a in typing.get_args(annotation) if a is not type(None)]
        for preferred in (Path, int, float, bool, str):
            if preferred in candidates:
                return _coerce_to_annotation(value, preferred)
        if candidates:
            return _coerce_to_annotation(value, candidates[0])
        return value

    if annotation is Path:
        return Path(value)
    if annotation is bool:
        return _coerce_bool(value)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    return value


def _coerce_iter_kwargs(builder: Any, raw: dict[str, str]) -> dict[str, Any]:
    """Coerce every `key=value` CLI pair using the builder's iter_rows signature.

    Production builders use `from __future__ import annotations`, which makes
    every annotation a string at runtime. `typing.get_type_hints()` resolves
    those strings to real type objects so we can dispatch on `Path`, `int`,
    `bool`, etc. Falls back to the raw `inspect.signature(...)` annotations
    when type-hint resolution fails (e.g. a forward ref the test stub can't
    look up).
    """
    sig = inspect.signature(builder.iter_rows)
    try:
        hints = typing.get_type_hints(builder.iter_rows)
    except (NameError, TypeError):
        hints = {}
    coerced: dict[str, Any] = {}
    for key, value in raw.items():
        param = sig.parameters.get(key)
        annotation: Any = inspect.Parameter.empty
        if key in hints:
            annotation = hints[key]
        elif param is not None and param.annotation is not inspect.Parameter.empty:
            annotation = param.annotation
        coerced[key] = _coerce_to_annotation(value, annotation)
    return coerced


def _lift_reserved_into_args(
    iter_kwargs: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    """Move kvs that collide with `builder.build()` reserved kwargs into args.

    For each reserved kwarg:
      - If the corresponding `args.<name>` is already set (not None / empty),
        the CLI flag wins and we silently drop the kv.
      - Otherwise we copy the kv value into `args.<name>` (so the explicit
        `builder.build()` call carries it) and remove it from `iter_kwargs`.

    Either way the kv never reaches `**iter_kwargs` in the build() call, so
    Python can't raise `multiple values for keyword argument`.
    """
    remaining: dict[str, Any] = {}
    for key, value in iter_kwargs.items():
        if key in _BUILDER_BUILD_RESERVED:
            current = getattr(args, key, None)
            if current is None or current == "":
                setattr(args, key, value)
            continue
        remaining[key] = value
    return remaining


def _cmd_build(args: argparse.Namespace) -> int:
    builder = get_builder(args.family)
    raw_kwargs = _parse_kv_list(args.family_arg)
    iter_kwargs = _coerce_iter_kwargs(builder, raw_kwargs)
    iter_kwargs = _lift_reserved_into_args(iter_kwargs, args)
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
