#!/usr/bin/env python3
"""retired-backlog-paths — no live `.claude/` instruction may name a retired register.

FIX-SA-09 (audit docs/audits/system-audit-2026-09-29.md § 5). The four review
backlogs, OPEN-ITEMS.json, SESSIONS.json, MANAGER-LEASE.json, MERGE-QUEUE.json,
the session board and the board pointer were archived on 2026-09-21. On
2026-09-29 the SessionStart hook, three slash commands and four skills still told
a session to *drain* them — a doc describing a mechanism that does not exist,
read as one that does.

A mention is allowed only when a retirement marker (retired / archived / absent /
no longer / CORRECTED / MEASURED / …) appears within 8 lines either side: the
skills legitimately record the retirement itself. Anything else is a live
instruction and fails.

Scope is `.claude/**` (md, json, sh). `--self-test` plants a defect and a
clean line and requires the guard to see exactly one of them.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAT = re.compile(
    r"(?:health|performance|research|ml)-review-backlog|OPEN-ITEMS\.json|"
    r"SESSIONS\.json|MANAGER-LEASE|MERGE-QUEUE\.json|session-board\.json|"
    r"board-pointer\.json")
MARK = re.compile(
    r"retired|archived|absent|no longer|CORRECTED|does not exist|"
    r"do not (?:go looking|recreate)|until 2026|MEASURED|nothing reads|was `",
    re.I)
WINDOW = 8
SUFFIXES = {".md", ".json", ".sh"}


def scan_text(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if PAT.search(line):
            window = " ".join(lines[max(0, i - WINDOW): i + WINDOW + 1])
            if not MARK.search(window):
                out.append((i + 1, line.strip()[:120]))
    return out


def scan_tree(root: Path) -> tuple[int, list[str]]:
    findings, n = [], 0
    for p in sorted((root / ".claude").rglob("*")):
        if p.is_file() and p.suffix in SUFFIXES:
            n += 1
            for ln, txt in scan_text(p.read_text(errors="replace")):
                findings.append(f"{p.relative_to(root)}:{ln}: {txt}")
    return n, findings


def self_test() -> int:
    bad = "- Edit `docs/claude/ml-review-backlog.json` to drain.\n"
    ok = "The backlog `ml-review-backlog.json` was archived 2026-09-21.\n"
    a, b = scan_text(bad), scan_text(ok)
    if len(a) == 1 and not b:
        print("retired-backlog-paths self-test: PASS (planted defect caught, retirement note allowed)")
        return 0
    print(f"retired-backlog-paths self-test: FAIL planted={a} note={b}")
    return 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    n, findings = scan_tree(ROOT)
    print(f"retired-backlog-paths: scanned {n} files under .claude/, {len(findings)} live reference(s)")
    for f in findings:
        print("FAIL", f)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
