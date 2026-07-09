#!/usr/bin/env python3
"""prop-identity guard — keep the "is this account prop?" predicate single-homed.

BL-20260628-PROP-ISPROP-PREDICATE-DRIFT. Three divergent copies of the
prop-account test had grown across ``src/prop/`` (account_rulesets,
telegram_report_handler, prop_journal), each recognizing a DIFFERENT subset of
the prop signals — the classic drift class where a fix at one site never reaches
the others (the same shape as the qty-legalization bug). They were consolidated
onto the ONE seam ``src/prop/prop_identity.py::is_prop_account`` (the union of
exchange / account_class / type / backtest_ruleset). This guard makes that a
merge gate so a fourth divergent copy can't re-appear.

It fails the build when code OUTSIDE the seam re-derives the prop
**funding-class classification** by string-comparison:

  * ANYWHERE in ``src/`` — comparing an account's ``account_class`` / ``type``
    field to the literal ``"prop"``. Those signals classify funding category and
    have exactly one legitimate home; a bare copy silently mis-buckets the very
    accounts (``account_class:prop`` without ``exchange:breakout``) the union was
    written to catch, leaking prop into the real-money/paper KPIs.
  * INSIDE ``src/prop/`` — comparing an account's ``exchange`` field to the
    literal ``"breakout"``. Within the prop package such a test is always a prop
    classification and must delegate to ``is_prop_account``.

**NOT forbidden — connector dispatch.** ``exchange == "breakout"`` in the
executor / coordinator (``src/units/accounts/execute.py``, ``src/core/coordinator.py``)
selects the manual-bridge *code path* (no broker socket → emit a prop ticket);
that is a broker-integration switch, not a funding classifier, and legitimately
lives outside ``src/prop/``. The guard's ``"breakout"`` rule is scoped to
``src/prop/`` precisely so it never touches those.

Detection is **AST-based** (a real comparison, not a mention in a comment /
docstring / default value). A reviewed exception carries an inline
``# prop-identity-allow: <reason>`` marker on the offending line.

Usage::

    python scripts/ci/check_prop_identity_single_home.py           # scan src/ (CI default)
    python scripts/ci/check_prop_identity_single_home.py path ...  # scan explicit paths

Exit 0 = clean; exit 1 = at least one out-of-seam classifier (file:line printed).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# The one file allowed to string-derive the prop signals — the seam itself.
SEAM = "src/prop/prop_identity.py"
# The prop package: inside here a ``exchange == "breakout"`` test is a prop
# classification and must delegate (connector dispatch lives elsewhere).
PROP_PKG = "src/prop/"

# Account-config field names whose comparison to a prop literal is a classifier.
_FUNDING_KEYS = frozenset({"account_class", "type"})   # → compared to "prop"
_CONNECTOR_KEYS = frozenset({"exchange"})              # → compared to "breakout"

_ALLOW_MARKER = "# prop-identity-allow"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _referenced_keys(node: ast.AST) -> set[str]:
    """Account-field key names an operand subtree references.

    Handles ``a.exchange`` / ``a["exchange"]`` / ``a.get("exchange", "")`` /
    ``str(a.get("account_class","")).strip().lower()`` / bare ``account_class``.
    """
    keys: set[str] = set()
    wanted = _FUNDING_KEYS | _CONNECTOR_KEYS
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in wanted:
            keys.add(n.attr)
        elif isinstance(n, ast.Name) and n.id in wanted:
            keys.add(n.id)
        elif isinstance(n, ast.Subscript):
            sl = n.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and sl.value in wanted:
                keys.add(sl.value)
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "get" and n.args:
                a0 = n.args[0]
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str) and a0.value in wanted:
                    keys.add(a0.value)
    return keys


def _string_literals(operands: list[ast.AST]) -> set[str]:
    out: set[str] = set()
    for op in operands:
        if isinstance(op, ast.Constant) and isinstance(op.value, str):
            out.add(op.value.strip().lower())
    return out


def find_violations_in_source(source: str, rel_path: str) -> List[Tuple[int, str]]:
    """Return ``[(lineno, message), ...]`` for out-of-seam prop classifiers.

    No filesystem — the guard's self-test feeds a planted string directly.
    """
    if rel_path == SEAM:
        return []
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return []
    lines = source.splitlines()
    in_prop_pkg = rel_path.startswith(PROP_PKG)
    hits: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        literals = _string_literals(operands)
        keys = set()
        for op in operands:
            keys |= _referenced_keys(op)
        msg = None
        if "prop" in literals and (keys & _FUNDING_KEYS):
            msg = ("account_class/type compared to \"prop\" — a re-inlined prop "
                   "classifier; call prop_identity.is_prop_account() instead")
        elif in_prop_pkg and "breakout" in literals and (keys & _CONNECTOR_KEYS):
            msg = ("exchange compared to \"breakout\" inside src/prop/ — a "
                   "re-inlined prop classifier; call is_prop_account() instead")
        if msg is None:
            continue
        idx = node.lineno - 1
        if 0 <= idx < len(lines) and _ALLOW_MARKER in lines[idx]:
            continue
        hits.append((node.lineno, msg))
    return hits


def scan_paths(paths: Iterable[Path], root: Path) -> List[str]:
    violations: List[str] = []
    files: List[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        elif p.suffix == ".py":
            files.append(p)
    for f in files:
        rel = _rel(f, root)
        if rel.startswith("tests/"):
            continue
        try:
            source = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, msg in find_violations_in_source(source, rel):
            violations.append(f"{rel}:{lineno}: {msg}")
    return violations


def main(argv: List[str]) -> int:
    root = _repo_root()
    targets = [Path(a) for a in argv[1:]] if len(argv) > 1 else [root / "src"]
    violations = scan_paths(targets, root)
    if not violations:
        print("prop-identity guard: OK — the prop predicate is single-homed in prop_identity.py.")
        return 0
    print("prop-identity guard: FAIL — the \"is this account prop?\" test must have ONE home.\n")
    print("These sites re-derive the prop funding-class classification outside")
    print(f"the seam ({SEAM}). Route through is_prop_account() instead:\n")
    for v in violations:
        print(f"  {v}")
    print(
        f"\nConnector dispatch (exchange == \"breakout\" in the executor/coordinator) is "
        f"NOT this — it's allowed outside src/prop/.\n"
        f"If a call is a genuinely-reviewed exception, annotate its line with "
        f"'{_ALLOW_MARKER}: <reason>'."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
