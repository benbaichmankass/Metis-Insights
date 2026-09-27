#!/usr/bin/env python3
"""schedule-keeper: make the critical recurring validators run near their slot,
and make a validator that has STOPPED running loud.

WHY THIS EXISTS (W6-OPS-R, 2026-09-27 — measured, see
docs/research/recurring-validations-audit-2026-09-27.md):

  GitHub's cron scheduler stepped from ~30-60 min late to ~4-6 h late on
  2026-08-27 and has stayed there. Population: every `event=schedule` run of
  health-snapshot.yml and diag-relay-sweep.yml, 2026-07-29..2026-09-26 (read
  from the Actions API). The in-repo cron count was 12 on both sides of the
  step, so nothing in this repo caused it and nothing in YAML schedules fixes
  it. Over 2026-09-20..27 the hourly crons also DROPPED most slots
  (dashboard-edge-watch 15/168, main-tree-watch 39/168).

  Consequences the audit found: r4-demotion-gate (the armed MD-DEMOTE-S2-S1
  route) ran 12:25Z for a 07:41Z slot; replay-pregate ran at ~09:10Z instead of
  04:00Z; strategy-review-packets landed ~09:30Z instead of 04:40Z. And a slot
  GitHub drops entirely is invisible: `check_cadence_liveness.py` declares
  r4-demotion-gate `receipt: None` ("a dead run is caught by
  claude-run-failure-alert") — but a run that is never CREATED never fails.

WHAT IT DOES. Two modes, one cron grammar:

  keep    (the schedule-keeper workflow; triggers on push to main + cron, so it
          does not depend on the scheduler it is compensating for)
          For each TARGET: if its latest slot is more than GRACE old and no run
          has been created since that slot, `workflow_dispatch` it on main.
          Separately, if its last COMPLETED run is older than its stale window,
          report it STALE (the workflow pages on that).

  dedupe  (a tiny first job in each TARGET, on `schedule` events only)
          If the keeper already dispatched this slot, the late scheduled run
          would be a SECOND run of the same slot. Print skip=true so the real
          job is skipped. Only runs the keeper made (triggering actor
          github-actions[bot]) count: a human's manual dispatch — possibly a dry
          run with different inputs — never suppresses a scheduled run.

  It dispatches with each workflow's own DEFAULT inputs. It changes no
  decision logic, window or threshold of any target. Fails OPEN: an API error
  in `dedupe` means "run" (the caller treats a failed dedupe job as not-skip).

  --self-test runs the cron/slot/decision logic offline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from typing import Optional

UTC = dt.timezone.utc
REPO = os.environ.get("GITHUB_REPOSITORY", "benbaichmankass/Metis-Insights")
KEEPER_ACTOR = "github-actions[bot]"

#: The validators whose SLOT matters. stale_hours = "no completed run for this
#: long means it has stopped" (daily: one missed day + 2h; weekly: 7d + 2h).
#: Adding a target = add it here AND add the `dedupe` job to its workflow
#: (tests/test_schedule_keeper.py enforces both halves).
TARGETS: dict[str, dict] = {
    "r4-demotion-gate.yml": {"stale_hours": 26},
    "replay-pregate-nightly.yml": {"stale_hours": 26},
    "strategy-review-packets.yml": {"stale_hours": 26},
    "soak-book-grade-weekly.yml": {"stale_hours": 170},
}

#: A slot younger than this is left to GitHub's own scheduler.
GRACE_MIN = 30
#: Never catch up a slot older than this — the next slot is closer.
MAX_CATCHUP_H = 20
#: Completed conclusions that count as "it ran". failure counts: a failing
#: validator is claude-run-failure-alert's job; this one is about SILENCE.
RAN = {"success", "failure", "timed_out"}


# ── cron ────────────────────────────────────────────────────────────────────
def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        if a < lo or b > hi:
            raise ValueError(f"cron field {spec!r} out of range")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(expr: str):
    f = expr.split()
    if len(f) != 5:
        raise ValueError(f"not a 5-field cron: {expr!r}")
    mins, hours, dom, mon, dow = f
    if dom != "*" or mon != "*":
        raise ValueError(f"day-of-month/month fields unsupported: {expr!r}")
    dows = _field(dow, 0, 7)
    if 7 in dows:
        dows = (dows - {7}) | {0}
    return _field(mins, 0, 59), _field(hours, 0, 23), dows


def prev_slot(expr: str, at: dt.datetime) -> dt.datetime:
    """Latest time <= `at` matching `expr` (UTC, minute resolution)."""
    mins, hours, dows = parse_cron(expr)
    t = at.astimezone(UTC).replace(second=0, microsecond=0)
    for _ in range(8 * 24 * 60):
        # cron dow: 0=Sunday; python weekday(): 0=Monday
        if t.minute in mins and t.hour in hours and (t.weekday() + 1) % 7 in dows:
            return t
        t -= dt.timedelta(minutes=1)
    raise ValueError(f"no slot for {expr!r} within 8 days of {at}")


def latest_slot(crons: list[str], at: dt.datetime) -> dt.datetime:
    return max(prev_slot(c, at) for c in crons)


def workflow_crons(path: str) -> list[str]:
    import yaml  # the runner installs pyyaml; kept local so --self-test needs none
    d = yaml.safe_load(open(path))
    on = d.get(True) or d.get("on") or {}
    return [s["cron"] for s in (on.get("schedule") or [])]


# ── decisions (pure; the self-test drives these) ────────────────────────────
def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def counts_for_slot(run: dict) -> bool:
    """A run that occupies its slot: scheduled, or dispatched BY THE KEEPER."""
    if run.get("conclusion") in ("cancelled", "skipped"):
        return False
    ev = run.get("event")
    if ev == "schedule":
        return True
    if ev == "workflow_dispatch":
        return (run.get("triggering_actor") or {}).get("login") == KEEPER_ACTOR
    return False


def decide_keep(crons: list[str], runs: list[dict], now: dt.datetime,
                stale_hours: float) -> dict:
    slot = latest_slot(crons, now)
    age_min = (now - slot).total_seconds() / 60
    since = [r for r in runs if _ts(r["created_at"]) >= slot
             and (counts_for_slot(r) or r.get("event") == "workflow_dispatch")
             and r.get("conclusion") not in ("cancelled", "skipped")]
    dispatch = (not since and age_min >= GRACE_MIN
                and age_min <= MAX_CATCHUP_H * 60)
    ran = sorted((r for r in runs if r.get("status") == "completed"
                  and r.get("conclusion") in RAN
                  and r.get("event") in ("schedule", "workflow_dispatch")),
                 key=lambda r: r["created_at"])
    last = ran[-1]["created_at"] if ran else None
    last_age_h = (now - _ts(last)).total_seconds() / 3600 if last else None
    stale = last_age_h is None or last_age_h > stale_hours
    return {"slot": slot.isoformat(), "slot_age_min": round(age_min),
            "runs_since_slot": len(since), "dispatch": dispatch,
            "last_completed": last,
            "last_completed_age_h": None if last_age_h is None else round(last_age_h, 1),
            "stale_hours": stale_hours, "stale": stale}


def decide_dedupe(schedule_expr: str, me: dict, runs: list[dict]) -> dict:
    created = _ts(me["created_at"])
    slot = prev_slot(schedule_expr, created)
    others = [r for r in runs if r["id"] != me["id"] and _ts(r["created_at"]) >= slot
              and counts_for_slot(r)]
    return {"slot": slot.isoformat(), "skip": bool(others),
            "by": [r["id"] for r in others]}


# ── GitHub API ──────────────────────────────────────────────────────────────
def _api(path: str, method: str = "GET", body: Optional[dict] = None):
    tok = os.environ["GH_TOKEN"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{path}", method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {tok}",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _runs(workflow: str) -> list[dict]:
    out: list[dict] = []
    for ev in ("schedule", "workflow_dispatch"):
        out += _api(f"/actions/workflows/{workflow}/runs?event={ev}&per_page=30")["workflow_runs"]
    return out


def cmd_keep(args) -> int:
    now = dt.datetime.now(UTC)
    rows, stale = [], []
    for wf, spec in TARGETS.items():
        crons = workflow_crons(f".github/workflows/{wf}")
        d = decide_keep(crons, _runs(wf), now, spec["stale_hours"])
        d["workflow"] = wf
        if d["dispatch"] and not args.dry_run:
            _api(f"/actions/workflows/{wf}/dispatches", "POST", {"ref": "main"})
            d["dispatched"] = True
        if d["stale"]:
            stale.append(f"{wf} (last completed run: {d['last_completed'] or 'none'})")
        rows.append(d)
    print(json.dumps(rows, indent=1))
    summary = "; ".join(stale)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"stale={'true' if stale else 'false'}\n")
            f.write(f"summary={summary}\n")
    for r in rows:
        tag = "DISPATCHED" if r.get("dispatched") else ("would dispatch" if r["dispatch"] else "ok")
        print(f"{r['workflow']}: slot {r['slot']} ({r['slot_age_min']} min ago), "
              f"{r['runs_since_slot']} run(s) since -> {tag}; last completed "
              f"{r['last_completed_age_h']}h ago{' STALE' if r['stale'] else ''}")
    return 0


def cmd_dedupe(args) -> int:
    me = _api(f"/actions/runs/{args.run_id}")
    wf = os.path.basename(me["path"].split("@")[0])
    d = decide_dedupe(args.schedule, me, _runs(wf))
    print(json.dumps(d))
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"skip={'true' if d['skip'] else 'false'}\n")
    if d["skip"]:
        print(f"::notice::schedule-keeper already ran this slot ({d['slot']}) as run(s) "
              f"{d['by']}; this late scheduled run is skipped so the slot runs once.")
    return 0


# ── self-test ───────────────────────────────────────────────────────────────
def self_test() -> int:
    T = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=UTC)  # noqa: E731
    fails = 0

    def check(name, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": got {got!r} want {want!r}"))

    check("daily prev slot same day", prev_slot("41 7 * * *", T("2026-09-27T08:59")), T("2026-09-27T07:41"))
    check("daily prev slot before today's slot -> yesterday",
          prev_slot("41 7 * * *", T("2026-09-27T07:00")), T("2026-09-26T07:41"))
    check("weekly Sunday 03:00", prev_slot("0 3 * * 0", T("2026-09-27T09:00")), T("2026-09-27T03:00"))
    check("weekly on Saturday -> last Sunday", prev_slot("0 3 * * 0", T("2026-09-26T09:00")), T("2026-09-20T03:00"))
    check("step hours", prev_slot("23 */6 * * *", T("2026-09-27T13:00")), T("2026-09-27T12:23"))
    check("dow 1-5 on a Sunday -> Friday", prev_slot("30 14 * * 1-5", T("2026-09-27T09:00")), T("2026-09-25T14:30"))

    sched = lambda i, c, concl="success", st="completed": {  # noqa: E731
        "id": i, "event": "schedule", "created_at": c, "conclusion": concl, "status": st}
    keep = lambda i, c, concl=None, st="in_progress": {  # noqa: E731
        "id": i, "event": "workflow_dispatch", "created_at": c, "conclusion": concl,
        "status": st, "triggering_actor": {"login": KEEPER_ACTOR}}
    human = lambda i, c: {"id": i, "event": "workflow_dispatch", "created_at": c,  # noqa: E731
                          "conclusion": "success", "status": "completed",
                          "triggering_actor": {"login": "someone"}}
    now = T("2026-09-27T08:59")
    hist = [sched(1, "2026-09-26T12:25:09Z")]
    d = decide_keep(["41 7 * * *"], hist, now, 26)
    check("slot 78 min old, nothing since -> dispatch", d["dispatch"], True)
    check("last run 20.6h ago is not stale at 26h", d["stale"], False)
    d = decide_keep(["41 7 * * *"], hist, T("2026-09-27T07:55"), 26)
    check("slot inside grace -> leave it to GitHub", d["dispatch"], False)
    d = decide_keep(["41 7 * * *"], hist + [keep(2, "2026-09-27T08:20:00Z")], now, 26)
    check("keeper already dispatched this slot -> no second dispatch", d["dispatch"], False)
    d = decide_keep(["41 7 * * *"], hist + [human(3, "2026-09-27T08:10:00Z")], now, 26)
    check("a human dispatch since the slot also satisfies keep (no pile-on)", d["dispatch"], False)
    d = decide_keep(["41 7 * * *"], hist, T("2026-09-28T04:00"), 26)
    check("slot > MAX_CATCHUP old -> not caught up", d["dispatch"], False)
    check("no completed run for 39.6h -> STALE", d["stale"], True)
    d = decide_keep(["41 7 * * *"], [], now, 26)
    check("never ran -> STALE", d["stale"], True)
    d = decide_keep(["41 7 * * *"], [sched(4, "2026-09-26T12:25:09Z", "failure")], now, 26)
    check("a FAILED run still counts as having run (silence, not failure)", d["stale"], False)

    late = sched(10, "2026-09-27T12:25:00Z", None, "in_progress")
    d = decide_dedupe("41 7 * * *", late, [late, keep(2, "2026-09-27T08:20:00Z", "success", "completed")])
    check("late scheduled run after keeper dispatch -> skip", d["skip"], True)
    d = decide_dedupe("41 7 * * *", late, [late, human(3, "2026-09-27T08:10:00Z")])
    check("a HUMAN dispatch never suppresses the scheduled run", d["skip"], False)
    d = decide_dedupe("41 7 * * *", late, [late, keep(2, "2026-09-27T08:20:00Z", "cancelled", "completed")])
    check("a CANCELLED keeper run does not occupy the slot", d["skip"], False)
    d = decide_dedupe("41 7 * * *", late, [late, keep(2, "2026-09-26T08:20:00Z", "success", "completed")])
    check("yesterday's keeper run does not suppress today's", d["skip"], False)
    d = decide_dedupe("41 7 * * *", late, [late])
    check("alone in its slot -> run", d["skip"], False)
    print(f"schedule_keeper self-test: {'OK' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    k = sub.add_parser("keep")
    k.add_argument("--dry-run", action="store_true")
    dd = sub.add_parser("dedupe")
    dd.add_argument("--run-id", required=True)
    dd.add_argument("--schedule", required=True, help="github.event.schedule")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.cmd == "keep":
        return cmd_keep(a)
    if a.cmd == "dedupe":
        return cmd_dedupe(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
