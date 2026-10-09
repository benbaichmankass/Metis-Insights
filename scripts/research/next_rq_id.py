#!/usr/bin/env python3
"""Allocate the next free research-queue id, looking at every place an id can already be claimed.

WHY (RQ-ID-ALLOCATOR, 2026-10-04; hash scheme 2026-10-09). A lane picks "the next number" by listing ``research/queue/`` on ITS
checkout, which cannot see an id another lane has only pushed to a branch. MEASURED: it happened three
times on 2026-09-29 (PRs #14103 / #14131 / #14049 -- VOLSKIP renumbered twice) and again on #15603. The
second PR then hits an add/add conflict at best and, at worst, two units share an id until a merge.

WHAT IT CLAIMS. An id is *claimed* when ``RQ-YYYYMMDD-NNN`` appears as a unit file in ``research/queue/``
(or ``blocked/``), as a result directory under ``research/results/``, in the working tree, on ``main`` or on
ANY ``origin/*`` branch. It prints the first number above the highest claimed for that day, IN THE HAND BAND 900-999
(queue_replenish mints 001-899, so the two can never meet).

WHAT IT DOES NOT CLAIM. Two lanes that run it in the same minute, before either pushes, still collide --
there is no lock here and none is possible without a service. The remedy is to push the unit file FIRST (a
branch push is the claim) and to ``--fetch`` immediately before allocating. ``queue_replenish`` is
deliberately NOT routed through this: its ids must be reproducible from the merge-base for the E58
``--verify`` check, and a ref-dependent id would break that. It mints 001-899 instead.

    python3 scripts/research/next_rq_id.py --fetch            # next id for today (UTC)
    python3 scripts/research/next_rq_id.py --day 2026-10-04 --count 3

DEFAULT SCHEME (2026-10-09): ``RQ-YYYYMMDD-<h4>-NN``. ``<h4>`` is 4 hex chars derived from the session
(``CLAUDE_CODE_REMOTE_SESSION_ID`` / ``CLAUDE_SESSION_ID`` / the current branch name, else random), ``NN`` a
per-session counter starting at 01. No shared counter, so two lanes cannot collide unless they hash to the
same h4 on the same day (1 in 65,536 per pair) AND pick the same NN; the allocator also skips any id already
claimed. Old ``RQ-YYYYMMDD-NNN`` ids stay valid; ``--legacy`` mints from the 900-999 band as before.
"""
# wiring: manual-only - a lane runs it by hand just before creating a research/queue unit; no workflow picks a lane's id
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ID_RE = re.compile(r"(RQ-(\d{8})-(\d{3}))")
#: Both spellings of a unit id (old ``-NNN``, new ``-<h4>-NN``). Keep equal to research_queue._ID_RE.
ANY_ID_RE = re.compile(r"(RQ-\d{8}-(?:\d{3}|[0-9a-f]{4}-\d{2}))")
#: Hand-authored units live at 900-999 of each day; queue_replenish.py mints 001-899
#: (PI-20261005-RQ-ID-RACE-0001). Keep equal to queue_replenish.HAND_FLOOR.
HAND_FLOOR = 900
_DIRS = ("research/queue", "research/queue/blocked", "research/results")


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def claimed_ids(root: Path) -> Dict[str, str]:
    """{id: first place it was seen} over the working tree, main and every origin/* branch."""
    seen: Dict[str, str] = {}

    def note(name: str, where: str) -> None:
        m = ANY_ID_RE.search(name)
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
    start = max(max(used) if used else 0, HAND_FLOOR - 1) + 1
    if start + count - 1 > 999:
        raise SystemExit(f"hand-authored band RQ-{compact}-900..999 is full; use another day")
    return [f"RQ-{compact}-{n:03d}" for n in range(start, start + count)]


def session_hash(seed: str = "") -> str:
    """4 hex chars naming this session: explicit seed, else session env, else the branch, else random."""
    src = seed or os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
    if not src:
        src = _git(Path(__file__).resolve().parents[2], "rev-parse", "--abbrev-ref", "HEAD").strip()
        if src in ("", "HEAD", "main"):
            src = os.urandom(8).hex()
    return hashlib.sha256(src.encode()).hexdigest()[:4]


def next_hashed_ids(claimed: Dict[str, str], day: str, h4: str, count: int = 1) -> List[str]:
    """``RQ-<day>-<h4>-NN`` ids, NN continuing after the highest already claimed for this day+h4."""
    compact = day.replace("-", "")
    pre = f"RQ-{compact}-{h4}-"
    used = [int(u[len(pre):]) for u in claimed if u.startswith(pre) and u[len(pre):].isdigit()]
    start = max(used) + 1 if used else 1
    if start + count - 1 > 99:
        raise SystemExit(f"{pre}01..99 is full for this session; pass a different --seed")
    return [f"{pre}{n:02d}" for n in range(start, start + count)]


def _self_test() -> int:
    c = {"RQ-20301001-001": "a", "RQ-20301001-914": "origin/branch", "RQ-20301002-099": "b",
         "RQ-20301004-653": "generated"}
    assert next_ids(c, "2030-10-01") == ["RQ-20301001-915"]
    assert next_ids(c, "2030-10-01", 2) == ["RQ-20301001-915", "RQ-20301001-916"]
    assert next_ids(c, "2030-10-03") == ["RQ-20301003-900"]
    # the 2026-10-05 collision: a generated id at 653 must not push a hand id into the generated band
    assert next_ids(c, "2030-10-04") == ["RQ-20301004-900"]
    assert ID_RE.search("RQ-20301001-014.yaml").group(1) == "RQ-20301001-014"
    h = session_hash("some-session")
    assert re.fullmatch(r"[0-9a-f]{4}", h) and h == session_hash("some-session")
    assert next_hashed_ids({}, "2030-10-01", "ab12", 2) == ["RQ-20301001-ab12-01", "RQ-20301001-ab12-02"]
    c2 = {"RQ-20301001-ab12-02": "origin/x", "RQ-20301001-cd34-05": "y"}
    assert next_hashed_ids(c2, "2030-10-01", "ab12") == ["RQ-20301001-ab12-03"]
    assert ANY_ID_RE.search("RQ-20301001-ab12-03.yaml").group(1) == "RQ-20301001-ab12-03"
    assert ANY_ID_RE.search("RQ-20301001-014.yaml").group(1) == "RQ-20301001-014"
    print("next_rq_id self-test OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--legacy", action="store_true", help="mint from the old 900-999 band instead")
    ap.add_argument("--seed", default="", help="override the session seed hashed into <h4>")
    ap.add_argument("--fetch", action="store_true", help="git fetch origin first (recommended)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    root = Path(__file__).resolve().parents[2]
    if a.fetch:
        subprocess.run(["git", "fetch", "--quiet", "origin"], cwd=str(root), check=False)
    claimed = claimed_ids(root)
    ids = (next_ids(claimed, a.day, a.count) if a.legacy
           else next_hashed_ids(claimed, a.day, session_hash(a.seed), a.count))
    for uid in ids:
        print(uid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
