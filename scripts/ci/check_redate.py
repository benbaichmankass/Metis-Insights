#!/usr/bin/env python3
"""redate-guard -- a re-dated pipeline item must carry a NEW OBSERVATION.

PI-20261005-4GA8WQPA-0002 (the lapse wave): BACKLOG-BURNDOWN-2 took the pipeline
from 358 due to 19 by re-reading items and moving their dates, not closing them;
553 items filed >=14 days earlier were still open and their cadences expired
together. A date is free to move and looks like work. This guard makes the move
carry its reason: a pipeline record that pushes an OPEN item's next-due date
LATER, with its state unchanged, must hold `redate: {observed_at, observation}`
-- what the writer actually read -- and the text must not repeat the previous
record's. Closing, killing and routing (a state change) are dispositions, not
re-dates, and need nothing. Use `python3 scripts/ops/pipeline.py --redate ID
--observation TEXT --next-due DATE`, which also sets cadence, due_date and
observation.due_by together.

DIFF-SCOPED to records ADDED against the base: the existing store predates the
rule and is not retro-graded. Exit: 0 clean / 1 refused / 2 could not check.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import pipeline as pl  # noqa: E402

PIPE_DIR = "docs/claude/work/pipeline/"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _latest_at_base(repo: Path, base: str, item_id: str) -> Optional[dict]:
    hit = _git(repo, "grep", "-l", f'"id": "{item_id}"', base, "--", PIPE_DIR)
    files = sorted(l.split(":", 1)[1] for l in hit.stdout.splitlines() if ":" in l)
    for f in reversed(files):
        try:
            rec = json.loads(_git(repo, "show", f"{base}:{f}").stdout)
        except ValueError:
            continue
        if rec.get("id") == item_id:
            return rec
    return None


def findings(repo: Path, base: str) -> List[str]:
    added = sorted(_git(repo, "diff", "--name-only", "--diff-filter=A",
                        f"{base}...HEAD", "--", PIPE_DIR).stdout.split())
    out: List[str] = []
    latest: dict = {}
    for name in added:
        try:
            rec = json.loads((repo / name).read_text())
        except (OSError, ValueError):
            continue
        iid = rec.get("id")
        if not isinstance(iid, str):
            continue
        prior = latest.get(iid) or _latest_at_base(repo, base, iid)
        latest[iid] = rec
        if prior is None:
            continue  # a new item: filing, not re-dating
        out += [f"{iid} ({name}): {p}" for p in pl.redate_problems(prior, rec)]
    return out


def _self_test() -> int:
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    def item(**o):
        d = {"id": "X", "what": "w", "state": "queued", "next_action": "dispatch_lane",
             "origin": {"kind": "session", "ref": "r", "rerun": "x"},
             "due_when": {"kind": "observation", "clears_when": "c", "check_every_days": 7,
                          "last_checked": "2026-10-01"}}
        d.update(o)
        return d

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)

        def sh(*a):
            subprocess.run(a, cwd=td, capture_output=True, text=True, check=True)
        sh("git", "init", "-q", "-b", "main"); sh("git", "config", "user.email", "t@t"); sh("git", "config", "user.name", "t")
        (repo / PIPE_DIR).mkdir(parents=True)
        (repo / PIPE_DIR / "20261001T000000000000Z-aaaaaaaa.json").write_text(json.dumps(item()))
        sh("git", "add", "-A"); sh("git", "commit", "-qm", "base"); sh("git", "branch", "base")
        sh("git", "checkout", "-q", "-b", "work")

        def land(n, rec):
            (repo / PIPE_DIR / f"2026100{n}T000000000000Z-bbbbbbbb.json").write_text(json.dumps(rec))
            sh("git", "add", "-A"); sh("git", "commit", "-qm", f"c{n}")
        bare = item(due_when={"kind": "observation", "clears_when": "c", "check_every_days": 14,
                              "last_checked": "2026-10-07"})
        land(2, bare)
        ck("a bare re-date is refused", any("no `redate` block" in f for f in findings(repo, "base")))
        good = dict(bare, redate={"observed_at": "2026-10-07",
                                  "observation": "re-ran origin.rerun: still reproduces on 3 of 5 rows"})
        land(3, good)
        res = findings(repo, "base")
        ck("the earlier bare record in the same diff is still refused", len(res) == 1)
        sh("git", "checkout", "-q", "base"); sh("git", "checkout", "-q", "-b", "w2")
        land(4, good)
        ck("a re-date WITH a real observation passes (positive control)", findings(repo, "base") == [])
        land(5, item(due_when={"kind": "observation", "clears_when": "c", "check_every_days": 30,
                               "last_checked": "2026-10-20"}, redate=good["redate"]))
        ck("repeating the same observation for a second re-date is refused",
           any("repeats" in f for f in findings(repo, "base")))
        sh("git", "checkout", "-q", "base"); sh("git", "checkout", "-q", "-b", "w3")
        land(6, item(state="killed", terminal_reason="no longer applies"))
        ck("closing an item needs no observation", findings(repo, "base") == [])
    print("redate-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if _git(REPO, "rev-parse", "--verify", a.base).returncode != 0:
        print(f"redate-guard: base {a.base} not found -- COULD NOT CHECK (not 'clean')")
        return 2
    f = findings(REPO, a.base)
    if f:
        print("::error::a pipeline item re-dated without a new observation:")
        for x in f:
            print(f"  ✗ {x}")
        return 1
    print("redate-guard: no re-date in this diff lacks a new observation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
