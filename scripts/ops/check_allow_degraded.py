#!/usr/bin/env python3
"""Fail when a `# allow-degraded:` annotation lacks an owner + an unexpired expiry.

WHY
---
`# allow-degraded: <reason>` is the escape hatch that tells `artifact-validity-guard`
a producer/fetch step is ALLOWED to swallow its own failure (e.g. an off-VM candle
fetch that degrades to an honest n=0). Left unenforced it is exactly the **silence
list** that `KNOWN_VACUOUS` in `check_artifact_validity.py` was deliberately designed
NOT to be: an un-owned, never-expiring exception that sits forever
(`BL-20260730-ALLOW-DEGRADED-NEEDS-EXPIRY`). The four annotations in the macro
backfill/grade workflows carried a backlog id but no expiry, so nothing forced a
re-review of whether the degradation was still acceptable.

This guard mirrors `known_vacuous_problems()`: every real `# allow-degraded:`
annotation MUST carry
  (a) a backlog id (BL-/MB-/FU-…) that RESOLVES to a filed row in a review backlog, and
  (b) an `until:YYYY-MM-DD` date; the guard FAILS once that date has passed —
so a degradation exception can never quietly become permanent, and an unowned one
can never be added.

Detection is precise, not presence-only (the `new-table-wiring-guard` lesson — a guard
cheaper to lie to than to satisfy is worse than none): a marker counts only in its
COMMENT form `# allow-degraded: <payload>`, and a payload containing `<` is a syntax
placeholder (this module's own examples, the guard-workflow's docstring) and is skipped.
So the guard never flags the very text that documents it, without a file allow-list.

Stdlib-only.

Usage:
  python scripts/ops/check_allow_degraded.py                 # full scan (CI)
  python scripts/ops/check_allow_degraded.py --today 2026-12-01
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_backlog_refs import REF, filed_ids  # noqa: E402  (single source of id resolution)

REPO = pathlib.Path(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

SCAN_DIRS = (".github", "scripts", "src", "config")
SCAN_SUFFIXES = {".yml", ".yaml", ".py", ".sh"}

# The COMMENT form only: `# allow-degraded: <payload>`. A bare `allow-degraded:` inside
# a string literal (the guard-workflow's `'allow-degraded:' not in buf` detection) is
# NOT preceded by `# ` and so is correctly not a marker.
MARKER = re.compile(r"#\s*allow-degraded:\s*(?P<payload>.*)$")
UNTIL = re.compile(r"until:(\d{4}-\d{2}-\d{2})")


def marker_problems(payload: str, filed: set[str], today: str) -> list[str]:
    """Problems with ONE annotation payload ([] = a well-formed, unexpired exception)."""
    problems: list[str] = []
    ids = REF.findall(payload)
    if not ids:
        problems.append("names no backlog id — an un-owned degradation exception is "
                        "the silence list KNOWN_VACUOUS exists not to be")
    else:
        dangling = [i for i in ids if i not in filed]
        if dangling:
            problems.append(f"backlog id(s) resolve to NOTHING: {', '.join(dangling)} "
                            "(file the row, or fix a typo/rename)")
    m = UNTIL.search(payload)
    if not m:
        problems.append("no `until:YYYY-MM-DD` — a degradation exception must expire so it "
                        "cannot become permanent (mirrors KNOWN_VACUOUS's `until`)")
    elif today > m.group(1):
        problems.append(f"EXPIRED on {m.group(1)} — re-justify the degradation (is it still "
                        "acceptable?) and bump `until:`, or remove the annotation if the "
                        "underlying issue is fixed")
    return problems


# This module is the marker's DEFINITION site — its docstring necessarily quotes
# `# allow-degraded:` in the comment form to explain it. A guard does not police its
# own definition (the one legitimate self-exclusion; every other file is scanned).
SELF = pathlib.Path(__file__).resolve()


def scan(repo: pathlib.Path, today: str) -> tuple[list[dict], int]:
    """Return (findings, n_valid_markers) over the scanned tree."""
    filed = filed_ids(repo)
    findings: list[dict] = []
    n_valid = 0
    for d in SCAN_DIRS:
        root = repo / d
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            if p.suffix.lower() not in SCAN_SUFFIXES:
                continue
            if p.resolve() == SELF:
                continue  # the guard's own definition file (see SELF above)
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                m = MARKER.search(line)
                if not m:
                    continue
                payload = m.group("payload")
                if "<" in payload:
                    continue  # syntax placeholder (docs / this module's own examples)
                probs = marker_problems(payload, filed, today)
                if probs:
                    findings.append({"file": str(p.relative_to(repo)), "line": lineno,
                                     "payload": payload.strip()[:120], "problems": probs})
                else:
                    n_valid += 1
    return findings, n_valid


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO))
    ap.add_argument("--today", default=None, help="YYYY-MM-DD for expiry (default: today UTC)")
    args = ap.parse_args(argv)
    today = args.today or datetime.now(timezone.utc).date().isoformat()
    findings, n_valid = scan(pathlib.Path(args.repo_root), today)

    if not findings:
        print(f"OK — {n_valid} `# allow-degraded:` annotation(s), each with a resolvable "
              "backlog id and an unexpired `until:`.")
        return 0

    print("::error::`# allow-degraded:` annotation(s) missing an owner or an unexpired "
          "expiry. Unenforced, allow-degraded is the silence list KNOWN_VACUOUS was "
          "designed not to be (BL-20260730-ALLOW-DEGRADED-NEEDS-EXPIRY).")
    for f in findings:
        print(f"  {f['file']}:{f['line']}: `{f['payload']}`")
        for prob in f["problems"]:
            print(f"      - {prob}")
    print("")
    print("Fix: annotate as `# allow-degraded: <BL-id> until:YYYY-MM-DD <reason>`, with the "
          "id filed in a review backlog and a future expiry that forces re-review.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
