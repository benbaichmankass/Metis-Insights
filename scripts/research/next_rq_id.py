#!/usr/bin/env python3
"""Allocate the next free research-queue id, looking at every place an id can already be claimed.

WHY (RQ-ID-ALLOCATOR, 2026-10-04). A lane picks "the next number" by listing ``research/queue/`` on ITS
checkout, which cannot see an id another lane has only pushed to a branch. MEASURED: it happened three
times on 2026-09-29 (PRs #14103 / #14131 / #14049 -- VOLSKIP renumbered twice) and again on #15603. The
second PR then hits an add/add conflict at best and, at worst, two units share an id until a merge.

WHAT IT CLAIMS. An id is *claimed* when ``RQ-YYYYMMDD-NNN`` appears as a unit file in ``research/queue/``
(or ``blocked/``), as a result directory under ``research/results/``, in the working tree, on ``main`` or on
ANY ``origin/*`` branch. It prints the first number above the highest claimed for that day.

WHAT IT DOES NOT CLAIM. Two lanes that run it in the same minute, before either pushes, still collide --
there is no lock here and none is possible without a service. The remedy is to push the unit file FIRST (a
branch push is the claim) and to ``--fetch`` immediately before allocating. ``queue_replenish`` is
deliberately NOT routed through this: its ids must be reproducible from the merge-base for the E58
``--verify`` check, and a ref-dependent id would break that.

    python3 scripts/research/next_rq_id.py --fetch            # next id for today (UTC)
    python3 scripts/research/next_rq_id.py --day 2026-10-04 --count 3
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ID_RE = re.compile(r"(RQ-(\d{8})-(\d{3}))")
_DIRS = ("research/queue", "research/queue/blocked", "research/results")


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def claimed_ids(root: Path) -> Dict[str, str]:
    """{id: first place it was seen} over the working tree, main and every origin/* branch."""
    seen: Dict[str, str] = {}

    def note(name: str, where: str) -> None:
        m = ID_RE.search(name)
        if m:
            seen.setdefault(m.group(1), where)

    for d in _DIRS:
        base = root / d
        if base.is_dir():
            for p in base.iterdir():
                note(p.name, f"worktree:{d}")
    refs = [r for r in _git(root, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").split() if r]
    for ref in refs:
        short = ref.replace("refs/remotes/", "")
        for d in _DIRS:
            for line in _git(root, "ls-tree", "--name-only", ref, f"{d}/").splitlines():
                note(line.rsplit("/", 1)[-1], short)
    return seen


def next_ids(claimed: Dict[str, str], day: str, count: int = 1) -> List[str]:
    compact = day.replace("-", "")
    used = [int(m.group(3)) for uid in claimed if (m := ID_RE.fullmatch(uid)) and m.group(2) == compact]
    start = (max(used) if used else 0) + 1
    return [f"RQ-{compact}-{n:03d}" for n in range(start, start + count)]


def _self_test() -> int:
    c = {"RQ-20301001-001": "a", "RQ-20301001-014": "origin/branch", "RQ-20301002-099": "b"}
    assert next_ids(c, "2030-10-01") == ["RQ-20301001-015"]
    assert next_ids(c, "2030-10-01", 2) == ["RQ-20301001-015", "RQ-20301001-016"]
    assert next_ids(c, "2030-10-03") == ["RQ-20301003-001"]
    assert ID_RE.search("RQ-20301001-014.yaml").group(1) == "RQ-20301001-014"
    print("next_rq_id self-test OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--fetch", action="store_true", help="git fetch origin first (recommended)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    root = Path(__file__).resolve().parents[2]
    if a.fetch:
        subprocess.run(["git", "fetch", "--quiet", "origin"], cwd=str(root), check=False)
    for uid in next_ids(claimed_ids(root), a.day, a.count):
        print(uid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
