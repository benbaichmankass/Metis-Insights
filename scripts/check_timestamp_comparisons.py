#!/usr/bin/env python3
"""CI guard: no RAW string comparison of ``created_at`` / ``closed_at``.

WHY (the incident this guard exists to prevent recurring)
---------------------------------------------------------
``trades.created_at`` and ``trades.closed_at`` do NOT share one timestamp
encoding. A row can carry ``created_at = '2026-07-30 05:44:22'`` (SQLite
``CURRENT_TIMESTAMP`` — space-separated, no offset) while ``closed_at =
'2026-07-30T07:40:36.498032+00:00'`` (ISO ``T`` + micros + offset), and the
reconciler-filled close path historically wrote a raw epoch-milliseconds string
(``"1782128223798"``) into the same column. At least FOUR encodings are in the
wild across these two columns.

Because ``' '`` (0x20) sorts BELOW ``'T'`` (0x54), a raw SQL string comparison
of a space-separated column against a ``'T'`` literal silently EXCLUDES every
matching row:

    "2026-07-30 05:44:22" >= "2026-07-30T05:37:00"   -> FALSE   (as strings)

So ``WHERE created_at >= '2026-07-30T05:37'`` matched ZERO of a day's rows and a
read reported "0 opens since the flip" when there were five
(``BL-20260730-TRADES-TIMESTAMP-FORMAT-MIXED``). This is a silent-wrong-answer
generator, not a crash: the result looks like a small sample, never a
systematic miss.

THE FIX the column already has a helper for
-------------------------------------------
``src.utils.closed_at`` (re-exported by ``src/web/api/_closed_at.py``) is the
single source of truth. Wrap BOTH sides of any ordering comparison so SQLite
parses the value instead of byte-comparing the string:

    datetime(created_at) >= datetime(?)                    # never epoch-ms
    closed_at_norm_sql('closed_at') >= datetime(?)         # closed_at may be ms
    close_time_sql('t.closed_at','op.updated_at','t.timestamp') >= datetime(?)

``datetime('2026-07-30 05:44:22')`` and ``datetime('2026-07-30T07:40:36+00:00')``
both normalise to a uniform ``'2026-07-30 ...'`` value, so the comparison is
correct across all encodings; ``closed_at_norm_sql`` additionally converts the
raw epoch-ms encoding first.

WHAT IT CHECKS
--------------
A ``created_at`` / ``closed_at`` column reference (optionally table-qualified,
e.g. ``t.created_at``) directly followed by an ORDERING operator
(``>=`` ``<=`` ``>`` ``<`` ``BETWEEN``) is a RAW comparison — the column is not
wrapped in ``datetime(...)`` / ``date(...)`` / ``closed_at_norm_sql(...)`` /
``close_time_sql(...)`` (a wrapped column is followed by ``)``, not the
operator, so it never matches). Assignments (``SET closed_at = ?``), column
definitions, and equality are out of scope — only ordering comparisons are the
documented failure.

Escape hatch for a deliberate, proven-safe comparison: append
``# ts-compare-ok: <reason>`` on the same line. The reason is mandatory (a bare
marker is rejected) so the exemption records WHY it is safe, matching the
``# provenance:`` verified-override philosophy.

Exit 0 = clean. Exit 1 = at least one raw comparison.

Same shape as ``canonical-db-resolver`` / ``env-gate-guard`` /
``silent-empty-guard`` / ``diagnostic-provenance-guard``.

Usage:
    python3 scripts/check_timestamp_comparisons.py --all        # standing audit
    python3 scripts/check_timestamp_comparisons.py /tmp/pr.diff  # diff-scoped (CI)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCAN_DIRS = ("src", "scripts", "ml")
_SCAN_EXTS = (".py", ".sql")

# A raw ordering comparison: (optional `alias.`) column, then an ordering
# operator. A WRAPPED column is `datetime(created_at) >= ...` — the `)` sits
# between the column and the operator, so this never matches it.
_RAW_CMP = re.compile(
    r"(?<![\w.])(?:[A-Za-z_]\w*\.)?(created_at|closed_at)\s*"
    r"(?:>=|<=|<>|>|<|(?i:\bbetween\b))"
)
# The right-hand side of a REAL SQL comparison is a bound param / literal /
# date function — NOT a bareword. Requiring a value-shaped RHS excludes English
# prose in docstrings ("created_at >= since", "created_at <= bybit.createdTime")
# and stray operators in print strings ("closed_at  <-- estimator") while still
# catching every genuine `created_at >= ?` / `>= '2026-...'` / `> 1700000000`.
_RHS_VALUE = re.compile(r"""\s*(\?|:\w+|'|"|\d|%s|%\(|datetime\(|date\(|strftime\(|julianday\()""")
_OK_MARKER = re.compile(r"#\s*ts-compare-ok:\s*\S")

# Files that legitimately talk ABOUT the pattern (this guard, its test, the
# canonical normaliser docstrings) rather than emitting a raw comparison.
_SELF = os.path.basename(__file__)
_EXEMPT_BASENAMES = {_SELF}


def _scan_line(line: str) -> bool:
    """True if *line* contains a raw ordering comparison and NO valid ok-marker."""
    if line.lstrip().startswith("#"):
        return False
    if _OK_MARKER.search(line):
        return False
    m = _RAW_CMP.search(line)
    if not m:
        return False
    # Only flag when the RHS is a value (bound param / literal / date fn); prose
    # and print-string arrows have a bareword or punctuation RHS and are skipped.
    return bool(_RHS_VALUE.match(line[m.end():]))


def _scan_all() -> List[Tuple[str, int, str]]:
    hits: List[Tuple[str, int, str]] = []
    for d in _SCAN_DIRS:
        root = os.path.join(_REPO_ROOT, d)
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(_SCAN_EXTS) or fn in _EXEMPT_BASENAMES:
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, _REPO_ROOT)
                try:
                    with open(path, encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            if _scan_line(line):
                                hits.append((rel, i, line.rstrip("\n")))
                except (OSError, UnicodeDecodeError):
                    continue
    return hits


def _scan_diff(diff_path: str) -> List[Tuple[str, int, str]]:
    """Scan only ADDED lines of a unified diff (grandfathers existing sites)."""
    hits: List[Tuple[str, int, str]] = []
    cur = "?"
    try:
        with open(diff_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        print(f"ERROR: cannot read diff {diff_path}: {exc}", file=sys.stderr)
        return hits
    for line in lines:
        if line.startswith("+++ b/"):
            cur = line[6:].strip()
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if not line.startswith("+"):
            continue
        added = line[1:]
        if os.path.basename(cur) in _EXEMPT_BASENAMES:
            continue
        if not cur.endswith(_SCAN_EXTS):
            continue
        if _scan_line(added):
            hits.append((cur, 0, added.rstrip("\n")))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("diff", nargs="?", help="unified diff to scan (added lines only)")
    ap.add_argument("--all", action="store_true", help="scan the whole tree")
    args = ap.parse_args()

    if args.all:
        hits = _scan_all()
        mode = "whole-tree"
    elif args.diff:
        hits = _scan_diff(args.diff)
        mode = f"diff {args.diff}"
    else:
        ap.error("pass a diff file, or --all for the standing audit")
        return 2

    if not hits:
        print(f"timestamp-comparison guard: clean ({mode}).")
        return 0

    print(f"timestamp-comparison guard: {len(hits)} RAW comparison(s) ({mode}):\n")
    for rel, ln, text in hits:
        loc = f"{rel}:{ln}" if ln else rel
        print(f"  {loc}\n      {text.strip()}")
    print(
        "\nA raw string comparison of created_at/closed_at silently drops rows "
        "when the column's\nencoding (space-separated CURRENT_TIMESTAMP, ISO "
        "T+offset, epoch-ms) differs from the literal.\n"
        "Wrap BOTH sides so SQLite parses them:\n"
        "  datetime(created_at) >= datetime(?)\n"
        "  closed_at_norm_sql('closed_at') >= datetime(?)   # closed_at may be epoch-ms\n"
        "or append '# ts-compare-ok: <reason>' if the comparison is proven safe.\n"
        "See BL-20260730-TRADES-TIMESTAMP-FORMAT-MIXED + src/utils/closed_at.py."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
