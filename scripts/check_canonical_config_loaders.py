"""CI guard: forbid new hand-rolled parsers for ``config/accounts.yaml``.

The 2026-05-16 backfill bug (orphan recovery returned 0 rows because
the script's hand-rolled YAML loader assumed a list-of-dicts shape
when the file is a dict-keyed-by-account-id) was the latest casualty
of having nine independent parsers for one config file. This guard
freezes the count: any new ``yaml.safe_load`` against
``accounts.yaml`` outside the canonical module fails CI.

The canonical readers (and their justifications):

* ``src/config/accounts_loader.py::load_accounts_dict`` — the
  dict-shape source of truth. Use this for read-only views.

* ``src/units/accounts/__init__.py::load_accounts`` — the production
  object-builder; wraps each cfg into a ``TradingAccount`` and
  applies executor-side validation. Predates the canonical reader
  and is retained because of its different return type.

If you need to read ``accounts.yaml`` from a new script / router /
unit, import ``load_accounts_dict`` from ``src.config.accounts_loader``.
Do not add a tenth hand-rolled parser. If your read-only consumer needs
a shape the canonical reader doesn't produce, extend the reader rather
than duplicating it — that's the entire point of putting it in
``src/config/``.

Detection strategy
------------------
AST walk over every ``.py`` file under ``src/`` and ``scripts/``.
For each function definition, the guard flags it iff:

  1. The function body contains a ``yaml.safe_load(...)`` call.
  2. The function body (or its parameter defaults) references the
     literal string ``accounts.yaml`` — i.e. the function is morally
     parsing that specific file.

Per-function scoping avoids the false positives a naïve text scan
hits (e.g. ``coordinator._load_units`` parses ``units.yaml`` but the
file mentions ``accounts.yaml`` in docstrings; the runtime-status
writer parses ``strategies.yaml`` in one function and ``accounts.yaml``
in another). The guard ignores cross-function references.

Usage
-----
::

    python scripts/check_canonical_config_loaders.py        # CI-style scan
    python scripts/check_canonical_config_loaders.py --list # show clean count

Exit code 0 → clean. Exit code 1 → at least one new parser found;
the script lists each offender's path + function name + line.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Tuple


_REPO_ROOT = Path(__file__).resolve().parents[1]

# Files allowed to parse accounts.yaml directly. Anything else is a
# new hand-rolled parser and the guard fails.
ALLOWLIST = frozenset({
    "src/config/accounts_loader.py",
    "src/units/accounts/__init__.py",
})

TARGET_FILENAME = "accounts.yaml"


def _is_yaml_safe_load(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "safe_load"
        and isinstance(func.value, ast.Name)
        and func.value.id == "yaml"
    )


def _contains_string_literal(node: ast.AST, needle: str) -> bool:
    """True iff any ``ast.Constant`` string in *node*'s subtree
    contains *needle* as a substring."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if needle in sub.value:
                return True
    return False


def _scan_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> List[int]:
    """Return list of line numbers of ``yaml.safe_load`` calls inside
    *func* iff the function also references ``accounts.yaml`` in its
    body (string literal) or in any parameter default. Otherwise
    return [] — out-of-function references don't count."""
    references_target = _contains_string_literal(func, TARGET_FILENAME)
    if not references_target:
        return []
    hits = [
        node.lineno
        for node in ast.walk(func)
        if _is_yaml_safe_load(node)
    ]
    return hits


def _gather_offenders() -> List[Tuple[Path, str, int]]:
    """Walk src/ + scripts/ and return per-function offenders.
    Each tuple is (file_path, function_name, line_of_offending_call)."""
    offenders: List[Tuple[Path, str, int]] = []
    for root in ("src", "scripts"):
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if TARGET_FILENAME not in source or "yaml.safe_load" not in source:
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for line in _scan_function(node):
                        offenders.append((path, node.name, line))
    return offenders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--list", action="store_true",
        help="Print scan summary even on clean runs.",
    )
    args = parser.parse_args()

    offenders = _gather_offenders()
    if not offenders:
        if args.list:
            print(
                "canonical-config-loaders: clean. "
                f"{len(ALLOWLIST)} allowlisted parser(s), "
                "0 hand-rolled offenders.",
            )
        return 0

    print(
        "canonical-config-loaders: hand-rolled accounts.yaml parser(s) found.",
        file=sys.stderr,
    )
    print(
        "Use `from src.config.accounts_loader import load_accounts_dict` "
        "instead.\n",
        file=sys.stderr,
    )
    for path, func_name, line in offenders:
        rel = path.relative_to(_REPO_ROOT).as_posix()
        print(f"  {rel}:{line}  in {func_name}()", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
