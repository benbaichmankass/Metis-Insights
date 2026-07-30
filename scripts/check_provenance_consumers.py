#!/usr/bin/env python3
"""CI guard: a provenance signal must have a CONSUMER, not just writers.

WHY (the incident this guard exists to prevent recurring)
---------------------------------------------------------
``notes.exit_price_source`` was written in 12 files and read in exactly one —
and that one read tested for an unrelated value. The whole ``ml/`` tree
referenced it zero times. So the journal faithfully recorded whether each PnL
was measured or manufactured, and every consumer — ``/performance``, the report
renderer, the three review skills, every ML label builder — treated the two as
identical.

The cost, once measured (2026-07-30): 226 closed rows carrying +$247,683.78 of
mark-price PnL, 247 more with no provenance at all, and a fabricated share of
closed trades rising 0.0% (May) -> 30.5% (June) -> 64.9% (July). It also
produced a "-$6,358 scalp exit leak" that did not exist, and a diagnostic
(``monitor_miss_analysis.py``) that confidently reported exits landing at
-3.94R and +6.31R — impossible for a bracket exit — because it classified the
substituted prices without checking where they came from.

A field that is written and never read is WORSE than a missing field: reviewers
see it and assume something acts on it. That false assurance is what let the
defect survive several thorough audits — every individual component was
correct, so line-by-line review kept coming back clean.

Rules and documentation did not prevent this (the canonical "green is not
evidence" rule was written the same day, after five prior instances). A guard
can. This is the same shape as ``canonical-db-resolver``, ``env-gate-guard``,
``silent-empty-guard`` and ``artifact-validity-guard``.

WHAT IT CHECKS
--------------
For every key declared in ``src.runtime.provenance.PROVENANCE_KEYS``: if the
repo WRITES the key anywhere, at least one CONSUMER must exist — code that
branches on its value or routes it through ``src.runtime.provenance``.

Exit 0 = every written key has a consumer. Exit 1 = at least one is write-only.

Usage:
    python3 scripts/check_provenance_consumers.py [--verbose]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_SCAN_DIRS = ("src", "scripts", "ml")
_SELF = os.path.relpath(os.path.abspath(__file__), _REPO_ROOT)
# The definition module and this guard naturally mention every key; neither is
# a consumer. Tests are excluded so a test can't satisfy the guard on behalf of
# production code (that would let the write-only pattern back in behind green CI).
_EXCLUDE_SUBSTRINGS = ("src/runtime/provenance.py", _SELF, "/tests/", "test_")


def _iter_py_files() -> List[str]:
    out: List[str] = []
    for d in _SCAN_DIRS:
        root = os.path.join(_REPO_ROOT, d)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames
                           if x not in ("__pycache__", ".git")]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), _REPO_ROOT)
                if any(s in rel for s in _EXCLUDE_SUBSTRINGS):
                    continue
                out.append(rel)
    return sorted(out)


def _writer_patterns(key: str) -> List[re.Pattern]:
    k = re.escape(key)
    return [
        re.compile(rf'["\']{k}["\']\s*:'),          # {"key": value}
        re.compile(rf'\[\s*["\']{k}["\']\s*\]\s*='),  # notes["key"] = value
        re.compile(rf'\.setdefault\(\s*["\']{k}["\']'),
    ]


def _consumer_patterns(key: str) -> List[re.Pattern]:
    k = re.escape(key)
    return [
        # Direct value branching: == / != / in (...) on the extracted value.
        re.compile(rf'["\']{k}["\']\s*\)?\s*(==|!=)'),
        re.compile(rf'(==|!=)\s*["\']{k}["\']'),
        re.compile(rf'\bif\b[^\n]*["\']{k}["\']'),
        re.compile(rf'["\']{k}["\'][^\n]*\bin\s*\('),
        # SQL-side filtering on the JSON key.
        re.compile(rf'json_extract\([^)]*{k}'),
        # Routed through the canonical module — the preferred consumer.
        re.compile(rf'classify_row\([^)]*{k}'),
        re.compile(rf'(is_measured|split_counts|require_measured)\([^)]*{k}'),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        from src.runtime.provenance import PROVENANCE_KEYS
    except Exception as exc:  # noqa: BLE001
        print(f"provenance-consumer-guard: cannot import PROVENANCE_KEYS: {exc}",
              file=sys.stderr)
        return 2

    files = _iter_py_files()
    # A file that uses the canonical module generically (is_measured(row) with a
    # default key) counts as a consumer for the default key.
    generic_consumer = any(
        re.search(r'from\s+src\.runtime\.provenance\s+import|'
                  r'from\s+src\.runtime\s+import\s+provenance|'
                  r'\bprovenance\.(is_measured|classify_row|split_counts|coverage|require_measured)\b',
                  open(os.path.join(_REPO_ROOT, f), encoding="utf-8",
                       errors="replace").read())
        for f in files
    )

    writers: Dict[str, List[str]] = {k: [] for k in PROVENANCE_KEYS}
    consumers: Dict[str, List[str]] = {k: [] for k in PROVENANCE_KEYS}

    for rel in files:
        try:
            text = open(os.path.join(_REPO_ROOT, rel), encoding="utf-8",
                        errors="replace").read()
        except OSError:
            continue
        for key in PROVENANCE_KEYS:
            if key not in text:
                continue
            if any(p.search(text) for p in _writer_patterns(key)):
                writers[key].append(rel)
            if any(p.search(text) for p in _consumer_patterns(key)):
                consumers[key].append(rel)

    violations: List[Tuple[str, List[str]]] = []
    for key in PROVENANCE_KEYS:
        w, c = writers[key], consumers[key]
        if args.verbose:
            print(f"  {key:22} writers={len(w):<3} consumers={len(c)}")
        if w and not c and not (generic_consumer and key == "exit_price_source"):
            violations.append((key, w))

    if not violations:
        print("provenance-consumer-guard: OK — every written provenance key "
              "has at least one consumer.")
        return 0

    print("provenance-consumer-guard: FAIL\n", file=sys.stderr)
    for key, w in violations:
        print(f"  '{key}' is WRITE-ONLY — written in {len(w)} file(s), "
              f"branched on in none:", file=sys.stderr)
        for f in w[:8]:
            print(f"      {f}", file=sys.stderr)
        if len(w) > 8:
            print(f"      ... and {len(w) - 8} more", file=sys.stderr)
    print(
        "\n  A provenance signal nothing reads is worse than one that does not\n"
        "  exist: reviewers see the field and assume something acts on it. That\n"
        "  false assurance is exactly how +$247,683.78 of mark-price PnL and a\n"
        "  non-existent '-$6,358 scalp exit leak' both survived repeated audits.\n"
        "\n  Fix by EITHER:\n"
        "    * consuming it — filter or report the split via\n"
        "      src.runtime.provenance (is_measured / split_counts / coverage);\n"
        "      report coverage the way /performance already does for rCoverage; or\n"
        "    * removing the write, if nothing should act on it.\n"
        "\n  Do NOT satisfy this guard with a test, and do NOT add another\n"
        "  bespoke exclude_* predicate — four already exist and collectively\n"
        "  still missed the general case.\n",
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
