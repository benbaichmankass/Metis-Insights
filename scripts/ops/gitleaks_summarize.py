#!/usr/bin/env python3
"""Summarise a gitleaks JSON report WITHOUT ever emitting a matched value.

FIX-SA-13 / SA-AUD-7-secret-scan-head-only (audit 2026-09-29). The weekly
full-history scan runs on a PUBLIC repo, so its log and job summary are public.
A gitleaks report row carries ``Secret``, ``Match`` and ``Line`` (the matched
text) beside ``File``/``Commit``/``RuleID``. This script therefore builds its
output from an explicit ALLOWLIST of keys and touches nothing else; a key added
to a future gitleaks version cannot leak because it is never read.

Output per finding: rule, file, 12-char commit, start line. Then counts by rule.
Also writes the same text to ``$GITHUB_STEP_SUMMARY`` when set, and DELETES the
report file, so no artifact upload can ever pick it up.

Exit: 0 clean · 1 findings · 2 report missing/unreadable (a scan that did not run
must never read as a clean scan).
"""
from __future__ import annotations

import collections
import json
import os
import sys

_SHOW = ("RuleID", "File", "Commit", "StartLine")


def _clean(v: object, limit: int = 200) -> str:
    return str(v).replace("\n", " ").replace("\r", " ")[:limit]


def summarise(rows: list[dict]) -> str:
    lines = [f"gitleaks full-history scan: {len(rows)} finding(s)"]
    if rows:
        by_rule = collections.Counter(_clean(r.get("RuleID", "?")) for r in rows)
        lines.append("by rule: " + ", ".join(f"{k}={v}" for k, v in sorted(by_rule.items())))
        lines.append("")
        lines.append("rule | file | commit | line   (matched values are never printed)")
        for r in sorted(rows, key=lambda r: (str(r.get("File")), str(r.get("Commit")))):
            lines.append(
                " | ".join([
                    _clean(r.get("RuleID", "?")),
                    _clean(r.get("File", "?")),
                    _clean(r.get("Commit", "?"))[:12],
                    _clean(r.get("StartLine", "?")),
                ])
            )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gitleaks_summarize.py <report.json>", file=sys.stderr)
        return 2
    path = argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            raise ValueError("report is not a list")
    except (OSError, ValueError) as e:
        print(f"::error::gitleaks report unreadable ({type(e).__name__}) — the scan did not complete", file=sys.stderr)
        return 2
    text = summarise(rows)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("```\n" + text + "\n```\n")
    try:
        os.remove(path)
    except OSError:
        pass
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
