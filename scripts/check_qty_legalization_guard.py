#!/usr/bin/env python3
"""qty-legalization guard — the machine that keeps the venue minimum single-homed.

Phase 4 of the sizing/qty-legalization consolidation
(``docs/sizing-legalization-DESIGN.md``). The recurring "a sub-lot qty reached
the order path" bug class (BL-20260611-005 / BL-20260619-ETHMIN /
BL-20260622-ALPACA-FRACTIONAL / BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR /
PR #5700) came from FOUR divergent copies of "the venue minimum / lot step". Phases
1-3 collapsed them onto ONE seam, ``src/units/accounts/qty_legalize.py``
(``legalize_qty`` / ``instrument_lot``), which is the only code that may resolve a
live venue lot rule (``precision.get_lot_rule``) or step-align a qty to it
(``precision.quantize_qty``).

This guard makes that invariant a merge gate, exactly the way the env-gate guard
killed the ``*_ENABLED`` class: it fails the build if any file **outside the seam**
*calls* ``get_lot_rule`` or ``quantize_qty`` — the precise pattern that would
seed a fifth copy of the minimum. The reason a fix at one site never reached its
siblings was that the invariant lived in scattered comments; a machine check does
not decay.

Detection is **AST-based**, so a comment / docstring / string literal that merely
*mentions* ``get_lot_rule`` (the seam's own docstring, execute.py's history
comments) is NOT a violation — only an actual call is. A genuine, reviewed
exception can carry an inline ``# qty-legalize-allow: <reason>`` comment on the
call line (mirrors the env-gate guard's ``# allow-silent``).

Usage::

    python scripts/check_qty_legalization_guard.py          # scan src/ (CI default)
    python scripts/check_qty_legalization_guard.py path ...  # scan explicit paths

Exit 0 = clean; exit 1 = at least one out-of-seam call (the offending
``file:line`` lines are printed).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# The ONLY files permitted to call the forbidden functions: the seam itself and
# the module that DEFINES them (its internal cache/live-lookup helpers). Paths
# are repo-root-relative, forward-slashed.
SEAM_ALLOWLIST = frozenset({
    "src/units/accounts/qty_legalize.py",  # the seam
    "src/units/accounts/precision.py",     # defines get_lot_rule / quantize_qty
})

# Calling either of these outside the seam re-introduces a private copy of the
# venue lot rule / step-alignment — the exact bug class the seam exists to end.
FORBIDDEN_CALLS = frozenset({"get_lot_rule", "quantize_qty"})

_ALLOW_MARKER = "# qty-legalize-allow"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _call_name(node: ast.Call) -> str | None:
    """The bare callable name for a Call node (``f(...)`` or ``mod.f(...)``)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def find_violations_in_source(source: str, rel_path: str) -> List[Tuple[int, str]]:
    """Return ``[(lineno, called_name), ...]`` for forbidden calls in *source*.

    A per-file helper (no filesystem) so the guard's self-test can feed it a
    planted-violation string directly. Files on the seam allowlist never report.
    """
    if rel_path in SEAM_ALLOWLIST:
        return []
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        # A file that doesn't parse isn't ours to police — the linters cover it.
        return []
    lines = source.splitlines()
    hits: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in FORBIDDEN_CALLS:
            continue
        # Honour an inline reviewed-exception marker on the call's own line.
        line_idx = node.lineno - 1
        if 0 <= line_idx < len(lines) and _ALLOW_MARKER in lines[line_idx]:
            continue
        hits.append((node.lineno, name))
    return hits


def scan_paths(paths: Iterable[Path], root: Path) -> List[str]:
    """Scan each *.py under *paths*; return human-readable violation strings."""
    violations: List[str] = []
    files: List[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        elif p.suffix == ".py":
            files.append(p)
    for f in files:
        rel = _rel(f, root)
        try:
            source = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, name in find_violations_in_source(source, rel):
            violations.append(f"{rel}:{lineno}: calls {name}() outside the qty_legalize seam")
    return violations


def main(argv: List[str]) -> int:
    root = _repo_root()
    if len(argv) > 1:
        targets = [Path(a) for a in argv[1:]]
    else:
        targets = [root / "src"]

    violations = scan_paths(targets, root)
    if not violations:
        print("qty-legalization guard: OK — venue lot rule / step-alignment is single-homed in the seam.")
        return 0

    print("qty-legalization guard: FAIL — the venue minimum must have ONE home.\n")
    print("These files call a venue-lot primitive outside the seam")
    print("(src/units/accounts/qty_legalize.py). Route the resolution through")
    print("legalize_qty() / instrument_lot() instead of re-deriving the minimum:\n")
    for v in violations:
        print(f"  {v}")
    print(
        "\nIf a call is a genuinely-reviewed exception, annotate its line with "
        f"'{_ALLOW_MARKER}: <reason>'.\n"
        "See docs/sizing-legalization-DESIGN.md."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
