#!/usr/bin/env python3
"""CI guard: every SQL ``json_extract`` must be ``json_valid``-guarded.

WHY (a bug class this repo has hit more than once)
--------------------------------------------------
SQLite's ``json_extract`` **RAISES** ``malformed JSON`` when its argument is not
parseable. It does NOT return NULL. Verified:

    json_extract(NULL,        '$.k')  -> NULL      (fine)
    json_extract('[1,2]',     '$.k')  -> NULL      (fine)
    json_extract('',          '$.k')  -> RAISES
    json_extract('not json',  '$.k')  -> RAISES

The live ``trade_journal.db`` contains rows with empty and malformed ``notes``
(that is why ``scripts/ops/repair_malformed_notes.py`` and the
``json-notes-cap-guard`` both exist). So ONE bad row makes an unguarded query
abort the WHOLE statement with an ``OperationalError`` — a query over a table is
all-or-nothing. The failure mode is therefore total, not partial: a data-quality
check becomes an outage, a report returns nothing instead of something.

This has bitten ``closed_flat_invariant`` before, and on 2026-07-30 it was very
nearly reintroduced in ``check_db_integrity``'s INV-2 predicate — caught only
because a test happened to feed it malformed notes. The pattern reads correct on
review (``COALESCE(json_extract(...), '')`` LOOKS null-safe, which is exactly the
trap), so review is not a reliable defence. A guard is.

THE FIX THIS ENFORCES
---------------------
Wrap the extract so a non-JSON value short-circuits before SQLite parses it::

    COALESCE(CASE WHEN json_valid(t.notes)
                  THEN json_extract(t.notes, '$.key') END, '')

A bad-notes row then reads as "key absent", which is the honest and safe
direction.

Exit 0 = every ``json_extract`` is guarded. Exit 1 = at least one is not.

Usage:
    python3 scripts/ci/check_json_extract_guarded.py [--verbose]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCAN_DIRS = ("src", "scripts", "ml")
_SELF = os.path.relpath(os.path.abspath(__file__), _REPO_ROOT)

# How many lines around the extract may carry the guard. A SQL string is often
# split across several adjacent Python string literals, so the guard legitimately
# lands a few lines away from the extract it protects.
_WINDOW = 8

_EXTRACT = re.compile(r"json_extract\s*\(")
_GUARD = re.compile(r"json_valid\s*\(")


def _prose_lines(path: str, source: str) -> set:
    """1-indexed line numbers that are pure PROSE, not code.

    Docstrings and comments routinely *discuss* ``json_extract`` — this guard's
    own module docstring does, as do the incident notes in ``json_notes.py`` and
    ``check_json_notes_cap.py``. Flagging those would make the guard cry wolf
    and get it waivered, which is how a guard dies.

    Detected precisely rather than by a leading-character heuristic: a
    line-prefix test only catches the FIRST line of a multi-line docstring, so
    continuation lines leak through and read as code. Comments come from
    ``tokenize``; docstrings from ``ast`` (module/class/function-level string
    expression statements). Falls back to an empty set on a syntax error, which
    fails LOUD (nothing suppressed) rather than silently allowing a real hit.
    """
    lines: set = set()
    if path.endswith(".sh"):
        for i, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                lines.add(i)
        return lines

    import ast
    import io
    import tokenize

    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for stmt in body:
            # A bare string expression statement is a docstring / block comment.
            if (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                end = getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno
                lines.update(range(stmt.lineno, end + 1))
    return lines


def _iter_files() -> List[str]:
    out: List[str] = []
    for d in _SCAN_DIRS:
        root = os.path.join(_REPO_ROOT, d)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in ("__pycache__", ".git")]
            for fn in filenames:
                if not fn.endswith((".py", ".sh")):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), _REPO_ROOT)
                if rel == _SELF:
                    continue
                out.append(rel)
    return sorted(out)


def scan_source(path: str, source: str) -> List[Tuple[int, str]]:
    """Unguarded ``json_extract`` sites in *source* as ``(lineno, line)``.

    Split out from :func:`_violations` so the guard's own detection can be
    unit-tested on synthetic input. A guard that is never proven to FIRE is
    indistinguishable from one that is broken — and this repo has now been
    burned twice by exactly that shape of false assurance.
    """
    lines = source.splitlines()
    prose = _prose_lines(path, source)
    out: List[Tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not _EXTRACT.search(line):
            continue
        # Prose mentioning the function (this guard's own docs, incident notes)
        # is not a call site.
        if (i + 1) in prose:
            continue
        lo = max(0, i - _WINDOW)
        hi = min(len(lines), i + _WINDOW + 1)
        if any(_GUARD.search(lines[j]) for j in range(lo, hi)):
            continue
        out.append((i + 1, line.strip()))
    return out


def _violations(path: str) -> List[Tuple[int, str]]:
    """Unguarded ``json_extract`` sites in the repo file at *path*."""
    full = os.path.join(_REPO_ROOT, path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except OSError:
        return []
    return scan_source(path, source)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    # Explicit argv (defaulting to sys.argv[1:]) so the guard is callable
    # in-process from a test without inheriting the caller's arguments.
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    findings: List[Tuple[str, int, str]] = []
    scanned = 0
    for path in _iter_files():
        scanned += 1
        for lineno, line in _violations(path):
            findings.append((path, lineno, line))

    if args.verbose:
        print(f"json-extract-guard: scanned {scanned} file(s)")

    if not findings:
        print("json-extract-guard: OK — every json_extract is json_valid-guarded.")
        return 0

    print("json-extract-guard: FAIL\n", file=sys.stderr)
    for path, lineno, line in findings:
        print(f"  {path}:{lineno}\n      {line}", file=sys.stderr)
    print(
        "\n  SQLite's json_extract RAISES 'malformed JSON' on an unparseable\n"
        "  argument — it does NOT return NULL. One bad row aborts the WHOLE\n"
        "  statement, so the failure is total, not partial. The live journal\n"
        "  DOES contain rows with empty/malformed notes.\n\n"
        "  COALESCE(json_extract(...), '') is NOT sufficient — it looks\n"
        "  null-safe and is not. That is the trap; review does not catch it.\n\n"
        "  Fix:\n"
        "    COALESCE(CASE WHEN json_valid(t.notes)\n"
        "                  THEN json_extract(t.notes, '$.key') END, '')\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
