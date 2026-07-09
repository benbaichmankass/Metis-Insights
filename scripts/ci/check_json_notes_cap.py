#!/usr/bin/env python3
"""json-notes-cap guard — forbid the char-slice truncation of json.dumps().

BL-20260618-CLOSEDFLAT-MALFORMED-JSON. The footgun: ``json.dumps(payload)[:N]``
— serialize to JSON, then slice the resulting STRING by character count. The
slice cuts mid-token the instant the payload exceeds ``N`` (a dangling key, an
unterminated string, a missing brace), persisting **invalid JSON** into a
``trades.notes`` / ``order_packages.signal_logic`` blob. Downstream
``json_extract`` / ``json.loads`` then choke — and in ``closed_flat_invariant``
one malformed row made ``json_extract(notes,'$.closed_at')`` raise "malformed
JSON", aborting the WHOLE query and silently disabling a safety invariant on
every tick.

The write-side was migrated to ``src/utils/json_notes.py::dump_capped`` (trims
*values*, guarantees valid JSON <= max_len, protects load-bearing keys). This
guard keeps the footgun from returning: it fails the build on a
``json.dumps(...)[: N]`` slice anywhere in ``src/`` — route through
``dump_capped(obj, max_len)`` instead.

Detection is **AST-based**: a ``Subscript`` whose value is a ``json.dumps(...)``
(or bare ``dumps(...)``) call and whose slice is a ``[:N]`` / ``[a:b]`` slice.
A plain index (``[0]``) or a mention in a comment/string is NOT flagged. A
genuinely-reviewed exception carries an inline ``# json-cap-allow: <reason>``
marker on the offending line.

Usage::

    python scripts/ci/check_json_notes_cap.py            # scan src/ (CI default)
    python scripts/ci/check_json_notes_cap.py path ...   # scan explicit paths

Exit 0 = clean; exit 1 = at least one char-slice truncation (file:line printed).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# The seam that DEFINES the safe replacement — it may name the footgun in its
# own docstring, and its self-tests may construct the bad pattern to prove the
# fix. Never report these.
ALLOWLIST = frozenset({
    "src/utils/json_notes.py",
})

_ALLOW_MARKER = "# json-cap-allow"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_json_dumps_call(node: ast.AST) -> bool:
    """True if *node* is a ``json.dumps(...)`` or bare ``dumps(...)`` call."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr == "dumps":
        return True  # json.dumps(...) / simplejson.dumps(...)
    if isinstance(f, ast.Name) and f.id == "dumps":
        return True  # from json import dumps; dumps(...)
    return False


def find_violations_in_source(source: str, rel_path: str) -> List[Tuple[int, str]]:
    """Return ``[(lineno, message), ...]`` for char-slice-truncated json.dumps."""
    if rel_path in ALLOWLIST:
        return []
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return []
    lines = source.splitlines()
    hits: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not isinstance(node.slice, ast.Slice):
            continue  # a plain index ([0]) is not truncation
        if not _is_json_dumps_call(node.value):
            continue
        idx = node.lineno - 1
        if 0 <= idx < len(lines) and _ALLOW_MARKER in lines[idx]:
            continue
        hits.append((
            node.lineno,
            "char-slice truncation of json.dumps(...) — cuts mid-token into "
            "invalid JSON; use src/utils/json_notes.dump_capped(obj, max_len)",
        ))
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
        print("json-notes-cap guard: OK — no char-slice truncation of json.dumps().")
        return 0
    print("json-notes-cap guard: FAIL — json.dumps(...)[:N] persists INVALID JSON.\n")
    print("Character-slicing a JSON string cuts mid-token (BL-20260618: one")
    print("malformed trades.notes blob aborted the closed_flat_invariant query).")
    print("Use src/utils/json_notes.dump_capped(obj, max_len) — it trims values,")
    print("keeps the result valid + <= max_len, and protects load-bearing keys:\n")
    for v in violations:
        print(f"  {v}")
    print(
        f"\nIf a slice is a genuinely-reviewed exception, annotate its line with "
        f"'{_ALLOW_MARKER}: <reason>'."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
