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

The cost, once measured (2026-07-30 — state the population): 206 of 829 closed
non-backtest rows carrying -$36,018.60 of
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
import shutil
import sys
import tempfile
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
        # classify(value, "<key>") — the value-level entry point, for a caller
        # that holds the source string rather than a whole row. Same canonical
        # module, same key-aware semantics; recognising it rewards routing
        # through the vocabulary instead of re-deriving buckets at the callsite.
        re.compile(rf'classify\([^)]*{k}'),
    ]


def self_test() -> int:
    """Plant a write-only provenance key and require main() to REFUSE it.

    WHY THIS EXISTS, AND WHY ITS ABSENCE WAS THE POINT
    --------------------------------------------------
    This guard was promoted into the REQUIRED `guards` merge context on
    2026-07-30 precisely because a provenance signal that is WRITTEN and never
    READ let a manufactured-PnL figure accumulate unnoticed. It was promoted
    with no self-test and no test file, so for the whole of that time its green
    had never been shown capable of turning red — a guard against write-only
    signals that was itself an unexercised signal (F-05 of the 2026-09-09
    full-system audit, the audit's own sharpest Phase-0 finding).

    The bar is NOT that these controls print. It is that `main()` RETURNS
    NON-ZERO on a violation planted here, and zero once that violation is
    removed. Both halves are asserted; control 1 alone would be satisfied by a
    guard that always fails.

    The plant is a throwaway tree in $TMPDIR, reached by pointing the module's
    own `_REPO_ROOT` at it for the duration. Nothing under the real `src/` is
    written, and the original root is restored in a `finally`.
    """
    global _REPO_ROOT
    fails: List[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f"  FAIL - {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS - {label}")

    try:
        from src.runtime.provenance import PROVENANCE_KEYS
    except Exception as exc:  # noqa: BLE001
        print(f"self-test: cannot import PROVENANCE_KEYS: {exc}", file=sys.stderr)
        return 2
    # Pick a key the guard has no generic-consumer escape hatch for, so the
    # plant tests the ordinary path rather than the exception.
    key = next((k for k in PROVENANCE_KEYS if k != "exit_price_source"), None)
    if key is None:
        print("self-test: PROVENANCE_KEYS holds no non-default key to plant with",
              file=sys.stderr)
        return 2

    real_root = _REPO_ROOT
    tmp = tempfile.mkdtemp(prefix="provenance_selftest_")
    try:
        src = os.path.join(tmp, "src")
        os.makedirs(src, exist_ok=True)
        planted = os.path.join(src, "planted_writer.py")
        _REPO_ROOT = tmp

        # 1. THE PLANT: a file that WRITES the key and nothing that reads it.
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write(f'def w(row):\n    row["{key}"] = "local_compute"\n')
        check(f"a write-only '{key}' makes main() return 1", main([]), 1)

        # 2. A CONSUMER RESCUES IT. Without this half, a guard that returned 1
        #    unconditionally would pass control 1 and look healthy.
        with open(os.path.join(src, "planted_reader.py"), "w", encoding="utf-8") as fh:
            fh.write(f'def r(row):\n    if row.get("{key}") == "local_compute":\n'
                     f'        return 1\n    return 0\n')
        check("...and adding a consumer makes main() return 0", main([]), 0)

        # 3. REMOVE THE WRITER: no writer, no claim, no violation. This is the
        #    control that separates "found the plant" from "trips on any tree".
        os.remove(planted)
        os.remove(os.path.join(src, "planted_reader.py"))
        check("an empty tree returns 0", main([]), 0)

        # 4. THE MARKER MUST NOT BE PRESENCE-ONLY. A file that merely MENTIONS
        #    the key in a comment is not a consumer -- that is the
        #    `new-table-wiring-guard` failure this repo already paid for, where
        #    the cheapest way to silence a real finding was a marker naming
        #    something that did not exist.
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write(f'def w(row):\n    row["{key}"] = "local_compute"\n')
        with open(os.path.join(src, "planted_mention.py"), "w", encoding="utf-8") as fh:
            fh.write(f'# we should really look at {key} one day\n')
        check("a bare MENTION of the key does not count as a consumer",
              main([]), 1)
    finally:
        _REPO_ROOT = real_root
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="run the planted-failure controls and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

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
        "  false assurance is exactly how 206 of 829 closed rows of mark-price PnL and a\n"
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
