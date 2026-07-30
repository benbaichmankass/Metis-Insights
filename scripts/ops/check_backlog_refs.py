#!/usr/bin/env python3
"""Fail when a change introduces a tracking reference that resolves to nothing.

WHY
---
Operator directive, 2026-07-30: *"make sure the tasks don't fall between the cracks so that
we think they're done, and then it turns out in two weeks that something has been failing
because we incorrectly thought that we finished building something that we hadn't."*

A doc or comment saying "tracked by `BL-X`" where `BL-X` was never filed is **worse than no
reference at all**: it reads as tracked, so nobody re-checks it. A self-audit that day found
four such ids — including **`BL-20260730-M1-PRICE-JOIN-DEAD`**, which is the canonical
example in the binding "Green is not evidence" rule and is cited from four workflows, and
which resolved to nothing.

Resolved rows are **kept** in the backlogs (295 of them), not archived — so a dangling
reference genuinely means *never filed*, never *filed and pruned*.

DIFF-SCOPED, DELIBERATELY
-------------------------
A repo-wide sweep finds ~109 pre-existing dangling refs (21% of all cited ids). Failing on
all of them would produce an alarm that every session walks past — and
`CLAUDE-RULES-CANONICAL.md` names the routinely-ignored alarm as **itself a P1 bug**. So this
guard checks only ids the diff *introduces*, the same scoping `silent-empty-guard` uses. The
pre-existing debt is measured and attributed in `BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS`
rather than hidden behind a suppression.

Stdlib-only.

Usage:
  python scripts/ops/check_backlog_refs.py --base origin/main   # diff-scoped (CI)
  python scripts/ops/check_backlog_refs.py --all                # full sweep, report only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import subprocess

REPO = pathlib.Path(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# A tracking id: BL-/MB-/FU- + YYYYMMDD + a SCREAMING-KEBAB slug. The trailing character
# class excludes a bare trailing '-' so a partial match inside prose can't masquerade as an
# id (that artefact produced two false "dangling" hits in the first measurement).
REF = re.compile(r'\b(?:BL|MB|FU)-\d{8}-[A-Z0-9]+(?:-[A-Z0-9]+)*\b')

SEARCH_DIRS = ("docs", "scripts", ".github", "config", "src")
BACKLOG_GLOB = "docs/claude/*backlog*.json"


def filed_ids(repo: pathlib.Path = REPO) -> set[str]:
    out: set[str] = set()
    for f in glob.glob(str(repo / BACKLOG_GLOB)):
        try:
            for it in json.load(open(f, encoding="utf-8")).get("items", []):
                if it.get("id"):
                    out.add(str(it["id"]))
        except Exception:  # noqa: BLE001 — a malformed backlog is another guard's problem
            continue
    return out


def _git(args: list[str], repo: pathlib.Path) -> str:
    return subprocess.run(["git", "-C", str(repo)] + args,
                          capture_output=True, text=True).stdout


def refs_in_added_lines(base: str, repo: pathlib.Path = REPO) -> dict[str, set[str]]:
    """Tracking ids appearing on ADDED diff lines, mapped id -> {files}."""
    diff = _git(["diff", "-U0", f"{base}...HEAD", "--"] + list(SEARCH_DIRS), repo)
    found: dict[str, set[str]] = {}
    current = "?"
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for m in REF.findall(line):
            found.setdefault(m, set()).add(current)
    return found


def refs_everywhere(repo: pathlib.Path = REPO) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for d in SEARCH_DIRS:
        root = repo / d
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            if p.suffix.lower() not in {".md", ".py", ".yml", ".yaml", ".json", ".sh"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            for m in REF.findall(text):
                found.setdefault(m, set()).add(str(p.relative_to(repo)))
    return found


def dangling(found: dict[str, set[str]], filed: set[str]) -> dict[str, set[str]]:
    # A backlog file citing an id in another row's `refs` is not a definition, so no
    # special-casing here: the id is either filed as a row or it dangles.
    return {k: v for k, v in sorted(found.items()) if k not in filed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO))
    ap.add_argument("--base", default=None,
                    help="git ref to diff against (diff-scoped mode)")
    ap.add_argument("--all", action="store_true",
                    help="full sweep; REPORTS the pre-existing debt, does not fail on it")
    args = ap.parse_args(argv)
    repo = pathlib.Path(args.repo_root)
    filed = filed_ids(repo)

    if args.all:
        bad = dangling(refs_everywhere(repo), filed)
        print(f"{len(filed)} filed ids; {len(bad)} dangling references repo-wide")
        for k, v in bad.items():
            print(f"  {k}  <- {sorted(v)[0]}"
                  + (f" (+{len(v) - 1} more)" if len(v) > 1 else ""))
        # Report-only by design: see the module docstring on alarm fatigue.
        return 0

    if not args.base:
        print("::error::--base <ref> or --all required")
        return 1

    bad = dangling(refs_in_added_lines(args.base, repo), filed)
    if not bad:
        print("OK — every tracking id this change introduces resolves to a filed "
              "backlog row.")
        return 0

    print("::error::this change introduces tracking reference(s) that resolve to NOTHING. "
          "A doc saying 'tracked by BL-X' where BL-X was never filed reads as tracked "
          "while being tracked by nobody — the exact crack the 2026-07-30 operator "
          "directive names.")
    for k, v in bad.items():
        for f in sorted(v):
            print(f"  {f}: {k}")
    print("")
    print("Fix: file the row in the right backlog (docs/claude/*-backlog.json) with honest "
          "severity and enough detail to act on, or correct the id if it is a typo/rename.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
