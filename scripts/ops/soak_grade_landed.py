#!/usr/bin/env python3
"""Did the weekly R5 soak grade actually LAND a usable record on ``main``?

WHY THIS EXISTS (MEASURED 2026-10-04)
-------------------------------------
``soak-book-grade-weekly`` showed ``conclusion: success`` for the 2026-10-04
slot and NO record landed. Two runs shared that slot:

* the schedule-keeper's ``workflow_dispatch`` run (37174464732) crashed inside
  ``r3_cost_fidelity`` (``ValueError: month must be in 1..12``), wrote no file,
  and failed;
* the late ``schedule`` run (37192722135) was deduped — ``grade`` SKIPPED — and
  so finished ``success`` having done nothing.

A reader checking the scheduled run saw green. And even a healthy run never
committed the per-leg record: the research-result action landed only its
pointer (``check_cadence_liveness.py`` recorded that gap 2026-09-28). The one
record on ``main`` (2026-09-26) arrived through a session's stray-file sweep.

This check is the workflow's last job, and it runs for the deduped run too: it
re-reads ``main`` and FAILS unless the newest dated record is recent AND
measured at least one dimension. A ``producer_failed`` record is a landed
record that could not look — it still fails here, so the run is red and a
session looks, instead of green over nothing.

Exit codes: 0 landed + measured · 1 missing / stale / producer_failed ·
2 could not check (git read failed).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

STORE = "comms/research/soak_book_grade"
_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def _git(root: Path, *args: str) -> Tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.returncode, p.stdout


def dated_records(names: List[str]) -> List[Tuple[date, str]]:
    out = []
    for n in names:
        m = _DATED.match(Path(n).name)
        if m:
            try:
                out.append((date.fromisoformat(m.group(1)), Path(n).name))
            except ValueError:
                continue
    return sorted(out)


def judge(newest: Optional[Tuple[date, dict]], today: date, max_age_days: int) -> Tuple[int, str]:
    """Pure verdict over (newest record date, its parsed body)."""
    if newest is None:
        return 1, f"NO dated record under {STORE}/ on the ref — nothing has ever landed"
    d, rep = newest
    age = (today - d).days
    if age > max_age_days:
        return 1, (f"newest record is {d.isoformat()} ({age}d old, limit {max_age_days}d) — "
                   f"this run did NOT land one")
    mech = rep.get("mechanics_read_state")
    cost = rep.get("cost_fidelity_read_state")
    if mech != "measured" and cost != "measured":
        return 1, (f"{d.isoformat()} landed but measured nothing (mechanics={mech}, "
                   f"cost_fidelity={cost}, error={rep.get('error')!r}) — could not look")
    n = (rep.get("population") or {}).get("n")
    return 0, (f"{d.isoformat()} landed: n={n} mechanics={mech} cost_fidelity={cost} "
               f"by_disposition={rep.get('by_disposition')}")


def check(root: Path, ref: str, today: date, max_age_days: int) -> Tuple[int, str]:
    rc, out = _git(root, "ls-tree", "--name-only", ref, f"{STORE}/")
    if rc != 0:
        return 2, f"could not list {STORE}/ on {ref}"
    recs = dated_records(out.split())
    if not recs:
        return judge(None, today, max_age_days)
    d, name = recs[-1]
    rc, body = _git(root, "show", f"{ref}:{STORE}/{name}")
    if rc != 0:
        return 2, f"could not read {STORE}/{name} on {ref}"
    try:
        rep = json.loads(body)
    except ValueError as exc:
        return 1, f"{STORE}/{name} is not valid JSON: {exc}"
    return judge((d, rep), today, max_age_days)


def _self_test() -> int:
    ok = True
    t = date(2026, 10, 4)

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    good = {"mechanics_read_state": "measured", "cost_fidelity_read_state": "producer_failed",
            "population": {"n": 45}, "by_disposition": {}}
    ck("nothing landed fails", judge(None, t, 1)[0] == 1)
    ck("the 2026-10-04 incident (newest 09-26) fails",
       judge((date(2026, 9, 26), good), t, 1)[0] == 1)
    ck("fresh record with one dimension measured passes", judge((t, good), t, 1)[0] == 0)
    ck("fresh producer_failed record fails (landed, could not look)",
       judge((t, {"mechanics_read_state": "producer_failed",
                  "cost_fidelity_read_state": "producer_failed"}), t, 1)[0] == 1)
    ck("dated_records ignores non-dated files and sorts",
       dated_records(["x/2026-09-26.json", "x/latest.json", "x/2026-10-04.json"])[-1][1]
       == "2026-10-04.json")
    print("soak_grade_landed self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--max-age-days", type=int, default=1,
                    help="newest record may be at most this many days older than today "
                         "(UTC). 1 covers a keeper run landing just before midnight.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    rc, msg = check(a.repo, a.ref, datetime.now(timezone.utc).date(), a.max_age_days)
    prefix = {0: "LANDED", 1: "::error::NOT LANDED", 2: "::error::COULD NOT CHECK"}[rc]
    print(f"{prefix}: soak grade — {msg}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
