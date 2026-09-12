#!/usr/bin/env python3
"""IS THE MANAGER-QUEUE WATCH ROUTINE ACTUALLY STILL FIRING?

THE FRAGILITY THIS CLOSES — AND IT IS NOT THE ROUTINE'S EXISTENCE
------------------------------------------------------------------
`trig_01TWdAvrwFLe6T9XFoNopTeo` ("Manager queue watch", cron `56 * * * *`)
spawns a FRESH session hourly to ask whether the manager is sitting on blocked
sub-sessions. It is a genuinely good mechanism — **a check not invoked by the
actor it checks** — and it fired successfully at 2026-09-03T04:56:36Z.

**The fragility is that if it stopped firing, nothing would notice.** Measured
2026-09-03 over all 25 Routines `list_triggers` returned for this account: it is
the ONLY cron-driven one; the other 24 are one-shot pokes with
`next_run_at: 0001-01-01`. So there is exactly one recurring watcher and, until
this guard, exactly zero things watching IT.

⚠️ **THE OBVIOUS RECEIPT WOULD NOT HAVE WORKED, AND THAT IS THE DESIGN POINT.**
`queue_latency.py` already writes a durable file — `QUEUE-WATCH-STATE.json` —
but it is a **page LATCH**, written only when a page or an unknown-report
actually fires. On a quiet queue it is never written, so its absence collapses
two opposite facts:

    "the Routine has never run"   ==   "the Routine ran hourly and had nothing
                                        to say"

That is the collapsed-state defect this repo has a canonical rule for. So the
watcher now also writes `docs/claude/work/MANAGER-QUEUE-WATCH.json` on EVERY
run, whatever the verdict, and this guard grades THAT file's age in
`run_guards.py` on every PR. A Routine that dies announces itself in everybody's
CI instead of going quiet.

WHAT THIS DOES NOT CLAIM
------------------------
⚠️ **IT GRADES THE ROUTINE'S LIVENESS, NEVER THE QUEUE'S HEALTH.** A
contributor's PR must not go red because the manager is sitting on four blocked
sub-sessions — that punishes the one actor who is not at fault and trains
everyone to ignore the guard, which is the desensitised-alarm failure this repo
has measured at 202 of 376 CRITICALs in one window being a single un-latched
alarm. `check_pr_queue_watch.py` draws the same line for the same reason. The
queue depth is REPORTED here and ESCALATED by the watcher's own run.

⚠️ **AND A FRESH RECEIPT IS NOT A DELIVERED PAGE.** The receipt attests that the
watcher RAN. Whether its digest reached the operator is a separate question this
guard cannot see, and it says so rather than implying coverage it does not have.

FIVE STATES, NEVER COLLAPSED
----------------------------
``fresh``       a run was recorded inside the window.                     PASS
``never_ran``   no receipt exists at all AND the watcher was armed less than
                `NEVER_RAN_GRACE_HOURS` ago (or the arming date could not be
                parsed — *we did not look*). ⚠️ **NOT A FAILURE, AND THAT IS
                CORRECT rather than lenient** — it is the accurate reading until
                the Routine first runs with `--write-receipt`, and failing on it
                would red every PR in the repo the day this merges, which is how
                a guard gets disabled instead of fixed. `check_pr_queue_watch.py`
                and `check_drain_liveness.py` both take this position. The guard
                ARMS ITSELF on the first receipt: once one exists, `stale`
                becomes reachable and there is no flag to unset.            PASS
``never_ran_overdue``
                no receipt exists and the watcher has been armed FAR longer than
                one could take. ⚠️ **THIS IS NOT A WIDENING OF `never_ran`, IT IS
                ITS OPPOSITE** — "has not fired yet" and "has fired on the order
                of 240 times and written nothing" are different facts, and until
                2026-09-12 they were one value, which is exactly how ten days of
                a SUCCEEDED-reporting watchdog writing nothing went unremarked.
                ⚠️ It PASSES, deliberately: only the Routine can clear it (a
                Routine-fired session holds no `mcp__*` tools), and a
                hand-written receipt would arm the freshness grading against a
                file nobody maintains — a healthy-looking watch forever. It is
                escalated where a session will MEET it, as a LOUD row from
                `render_due_list.py::src_manager_queue_watch`.               PASS
``stale``       the receipt exists and its newest run is older than the window.
                The Routine HAS run and has STOPPED — the failure this exists
                for.                                                       FAIL
``unreadable``  **WE COULD NOT LOOK.** The receipt is corrupt, or carries no
                parseable `generated_at`. Not evidence about the Routine at all,
                and a corrupt watchdog receipt is itself a defect, so it fails
                LOUDLY rather than passing quietly.                        FAIL

⚠️ `never_ran` and `stale` are distinct even though both mean "no recent run":
collapsing them would report *"it was never wired up"* as *"it broke"*, sending
a reader to investigate a regression that never happened. And `never_ran_overdue`
is distinct from BOTH: it is not "it broke" (it never worked) and not "give it
time" (the time was given and nothing came).

WHY A RECEIPT AND NOT `list_triggers`
--------------------------------------
`list_triggers` would answer this directly — and it is an `mcp__*` tool **CI does
not hold**, the same wall `queue_latency.py` reports `unknown` for permanently.
A guard cannot call it. The receipt is the repo-side shadow of a fact only the
live layer can observe, which is the same trade `PR-QUEUE-WATCH.json` makes.

⚠️ AND A ROUTINE BEING `enabled: true` IS NOT EVIDENCE IT FIRES. This repo holds
two Routines `enabled: true` at `next_run_at: 0001-01-01`, and `probes.yml`'s
first scheduled run fired ~4h50m late and once instead of daily. Read
`generated_at`, never the schedule.

EXIT: 0 pass · 1 fail.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
RECEIPT = REPO_ROOT / "docs" / "claude" / "work" / "MANAGER-QUEUE-WATCH.json"

FRESH, STALE, NEVER_RAN, UNREADABLE = "fresh", "stale", "never_ran", "unreadable"

#: A FIFTH state, added 2026-09-12. The row is
#: BL-20260912-THE-MANAGER-QUEUE-WATCH-ROUTINE-HAS-FIRED-HOURLY-FOR-TEN-DAYS-REPORTING-SUCCEEDED-AND-HAS-NEVER-WRITTEN-THE-RECEIPT-THAT-IS-ITS-OWN-STATED-POINT
#: — kept on ONE line deliberately: a tracking id wrapped across lines does not
#: resolve, and `check_backlog_refs.py` then reads it as a reference to a row
#: nobody filed. ``never_ran`` was UNCONDITIONALLY
#: benign, and that reading is correct on day one and FALSE on day ten: measured
#: 2026-09-12 against the live Routine list, `trig_01TWdAvrwFLe6T9XFoNopTeo` has
#: `last_run.status SUCCEEDED` at `last_fired_at 2026-09-12T16:56:08Z` and the
#: receipt has NEVER been committed once, by anybody. "has not fired yet" and
#: "has fired on the order of 240 times and written nothing" are opposite facts
#: and were one value.
NEVER_RAN_OVERDUE = "never_ran_overdue"

#: The Routine's declared cadence — cron `56 * * * *`, i.e. hourly.
#: ⚠️ RECORDED SO THE WINDOW BELOW HAS A BASIS, NOT SO IT CAN BE TRUSTED. The
#: cron is what was ASKED for; `generated_at` is what HAPPENED, and only the
#: second is evidence.
ROUTINE_CADENCE_HOURS = 1.0

#: How old the newest recorded run may be before the Routine counts as stopped.
#: CHOSEN against the cadence above rather than tuned: 6h tolerates FIVE missed
#: hourly firings, which is deliberately generous because a scheduled thing in
#: this environment is not punctual — `probes.yml`'s first scheduled run landed
#: ~4h50m late, and a window tighter than that lateness would red every PR over
#: a Routine that was merely slow. It still catches a genuinely dead Routine
#: within a quarter of a day.
#: ⚠️ If the Routine's cadence is ever slowed, this must be raised with it — the
#: self-test asserts the ordering so the pair cannot silently invert.
DEFAULT_WINDOW_HOURS = 6.0

#: WHEN THE RECEIPT FIRST BECAME OWED — the Routine's own `created_at`, MEASURED
#: 2026-09-12 via `list_triggers(recurring=true, limit=100)` (`has_more: false`,
#: so that was the COMPLETE set of recurring Routines on the account).
#:
#: ⚠️ IT IS A DECLARED CONSTANT AND DELIBERATELY NOT DERIVED FROM GIT. The
#: obvious alternative — the commit date of this file or of `queue_latency.py` —
#: RESETS every time anyone edits either, so the grace period would silently
#: re-arm on an unrelated typo fix and this guard would quietly disarm itself
#: forever while looking armed. A wrong constant is visible in a diff; a
#: self-resetting clock is not.
#:
#: ⚠️ AND IT IS THE ROUTINE'S ARMING DATE, NEVER "when we last looked". If the
#: Routine is ever retired and replaced, this must move with it — the self-test
#: asserts only that it PARSES, because no repo-side fact can confirm it.
RECEIPT_EXPECTED_SINCE = "2026-09-02T13:56:18Z"

#: How long after arming an ABSENT receipt still reads as "it has not fired
#: yet". CHOSEN as 24 firings at the declared cadence — generous by the same
#: argument as DEFAULT_WINDOW_HOURS (a scheduled thing here is not punctual),
#: and short enough that ten days cannot hide inside it. The self-test asserts
#: it stays wider than the cadence and wider than the freshness window, so the
#: three cannot silently invert.
NEVER_RAN_GRACE_HOURS = 24.0


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_receipt(path: Path = RECEIPT) -> Tuple[Optional[Dict[str, Any]], str]:
    """(receipt, read_state). ``absent`` and ``unreadable`` are opposite facts."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "absent"
    except OSError:
        return None, "unreadable"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "unreadable"
    return (data, "read") if isinstance(data, dict) else (None, "unreadable")


def grade(receipt: Optional[Dict[str, Any]], read_state: str, now: datetime,
          window_hours: float = DEFAULT_WINDOW_HOURS,
          expected_since: Optional[str] = RECEIPT_EXPECTED_SINCE,
          grace_hours: float = NEVER_RAN_GRACE_HOURS) -> Dict[str, Any]:
    """PURE, so the policy is arguable in tests rather than against a live Routine."""
    if read_state == "unreadable":
        return {"state": UNREADABLE, "age_hours": None, "ok": False,
                "why": ("the receipt exists and could not be parsed -- WE COULD NOT "
                        "LOOK. This is not evidence about the Routine, and a corrupt "
                        "watchdog receipt is itself a defect, so it fails loudly "
                        "rather than passing quietly.")}
    if read_state == "absent" or receipt is None:
        armed = _parse_ts(expected_since)
        if armed is None:
            # WE COULD NOT LOOK at how long it has been owed. Deliberately NOT
            # folded into either side: reading it as `never_ran` would assert the
            # Routine is new, and as `never_ran_overdue` would assert it is late.
            # Neither was measured.
            return {"state": NEVER_RAN, "age_hours": None,
                    "owed_hours": None, "expected_firings": None, "ok": True,
                    "why": ("no receipt exists, so the Manager Queue Watch Routine "
                            "has never run with --write-receipt -- AND the arming "
                            "date could not be parsed, so HOW LONG it has been "
                            "owed is unknown. That is `we did not look`, not "
                            "evidence the Routine is new. Fix RECEIPT_EXPECTED_"
                            "SINCE so absence can be dated.")}
        owed = (now - armed).total_seconds() / 3600.0
        if owed < grace_hours:
            return {"state": NEVER_RAN, "age_hours": None,
                    "owed_hours": round(owed, 1), "expected_firings": None,
                    "ok": True,
                    "why": (f"no receipt exists and the watcher has only been armed "
                            f"{owed:.1f}h (grace {grace_hours}h). This is the "
                            f"accurate reading until it first fires and is "
                            f"deliberately NOT a failure -- failing here would red "
                            f"every PR in the repo on the day this merges.")}
        # ⚠️ STILL `ok: True`, AND THAT IS A DECISION WITH A REASON, NOT LENIENCY.
        # The remedy is UNREACHABLE from here: only the Routine can write this
        # receipt, a Routine-fired session holds no `mcp__*` tools, and a
        # hand-written receipt would ARM the freshness grading against a file no
        # Routine maintains -- reporting a healthy watch forever, which is worse
        # than the silence. A guard whose only available response is "disable
        # me" is how a guard gets disabled instead of fixed. The escalation is
        # carried where a session will MEET it -- `render_due_list.py`'s
        # `src_manager_queue_watch` raises a LOUD due-list row off this same
        # verdict -- rather than as a red no contributor can clear.
        firings = int(owed / ROUTINE_CADENCE_HOURS) if ROUTINE_CADENCE_HOURS else None
        return {"state": NEVER_RAN_OVERDUE, "age_hours": None,
                "owed_hours": round(owed, 1), "expected_firings": firings,
                "ok": True,
                "why": (f"NO receipt has EVER been written, and the watcher has "
                        f"been armed {owed:.1f}h -- on the order of {firings} "
                        f"firings at the declared {ROUTINE_CADENCE_HOURS}h cadence "
                        f"(DERIVED from the cadence, not counted from a run "
                        f"history this guard cannot read). So this is NOT `it has "
                        f"not fired yet`: the watchdog built because `nothing "
                        f"catches a manager doing nothing` is itself doing "
                        f"nothing, uncaught. It does not FAIL here because only "
                        f"the Routine can clear it and a hand-written receipt "
                        f"would arm the freshness grading against a file nobody "
                        f"maintains; it is raised as a LOUD due-list row instead.")}
    ts = _parse_ts(receipt.get("generated_at"))
    if ts is None:
        return {"state": UNREADABLE, "age_hours": None, "ok": False,
                "why": ("the receipt carries no parseable `generated_at`, so it "
                        "cannot be DATED. A record that cannot be dated cannot be "
                        "shown to be current, and the fail-safe reading of a "
                        "watchdog is stale.")}
    age = (now - ts).total_seconds() / 3600.0
    if age >= window_hours:
        return {"state": STALE, "age_hours": round(age, 1), "ok": False,
                "why": (f"the newest recorded run is {age:.1f}h old (window "
                        f"{window_hours}h, cadence {ROUTINE_CADENCE_HOURS}h). The "
                        f"Manager Queue Watch Routine HAS run before and has "
                        f"STOPPED -- check `list_triggers` for "
                        f"trig_01TWdAvrwFLe6T9XFoNopTeo and fire it with "
                        f"`fire_trigger` to confirm. ⚠️ `enabled: true` is not "
                        f"evidence it fires: this repo holds two Routines enabled "
                        f"at next_run_at 0001-01-01.")}
    return {"state": FRESH, "age_hours": round(age, 1), "ok": True,
            "why": (f"a run was recorded {age:.1f}h ago, inside the {window_hours}h "
                    f"window.")}


def render(verdict: Dict[str, Any], receipt: Optional[Dict[str, Any]]) -> str:
    lines = [f"manager-queue-watch: {verdict['state'].upper()} -- {verdict['why']}"]
    if verdict.get("state") == NEVER_RAN_OVERDUE:
        # Printed on every PR, but NOT as a failure. See `grade`: the remedy is
        # unreachable from CI, so the row that a session acts on is the due-list
        # one — named here so a reader is not left looking for a fix they cannot
        # apply from a PR.
        lines.append(
            f"  armed {verdict.get('owed_hours')}h ago, ~{verdict.get('expected_firings')} "
            f"expected firings, ZERO receipts -- carried as a loud due-list row "
            f"(render_due_list.py::src_manager_queue_watch), not as a red here.")
    if receipt:
        # ⚠️ REPORTED, NEVER FAILED ON. See the module docstring: a contributor's
        # PR must not go red because the manager has a queue.
        read = receipt.get("read_state")
        waiting = receipt.get("waiting")
        worst = receipt.get("worst_min")
        if read and read != "measured":
            lines.append(
                f"  queue read: {str(read).upper()} -- the Routine ran and could NOT "
                f"grade the queue (it needs a `list_sessions` observation, an MCP "
                f"tool CI does not hold). This is not an empty queue.")
        elif waiting is not None:
            lines.append(
                f"  queue at last run: {waiting} sub-session(s) waiting on the manager"
                + (f", worst {worst} min" if worst is not None else "")
                + "  (reported, not enforced here)")
        lines.append("  ⚠️ a fresh receipt attests the Routine RAN. Whether its "
                     "digest reached the operator is a different question this "
                     "guard cannot see.")
    return "\n".join(lines)


def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    fails: List[str] = []
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

    def check(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok   {label}")

    def stamped(hours_ago: float, **kw):
        d = {"generated_at": (now - timedelta(hours=hours_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")}
        d.update(kw)
        return d

    # --- the four states are distinct, and each maps to the right consequence ---
    v = grade(None, "absent", now)
    check("no receipt at all grades never_ran", v["state"] == NEVER_RAN)
    check("never_ran PASSES -- it cannot red every PR on merge day", v["ok"])
    check("never_ran reports NO age rather than a fabricated zero",
          v["age_hours"] is None)

    # --- never_ran vs never_ran_overdue: opposite facts, not one value --------
    # ⚠️ `now` here is 2026-09-03T12:00Z and the shipped RECEIPT_EXPECTED_SINCE
    # is 2026-09-02T13:56Z, i.e. 22.1h -- INSIDE the 24h grace. That is why the
    # controls below pass an explicit `expected_since` instead of relying on the
    # default: a control whose verdict depends on how far the wall clock has
    # drifted from a constant is a control that changes answer over time.
    v = grade(None, "absent", now, expected_since="2026-09-03T10:00:00Z")
    check("an absent receipt INSIDE the grace grades never_ran",
          v["state"] == NEVER_RAN and v["ok"])
    check("...and reports how long it has been owed, so the reading is dateable",
          v["owed_hours"] == 2.0)
    check("...and claims NO firing count, because none is derivable yet",
          v["expected_firings"] is None)

    v = grade(None, "absent", now, expected_since="2026-08-24T12:00:00Z")
    check("an absent receipt LONG past the grace grades never_ran_overdue",
          v["state"] == NEVER_RAN_OVERDUE)
    check("...and is NOT reported as never_ran -- the two are opposite facts, "
          "and collapsing them is what let ten days of a SUCCEEDED-reporting "
          "watchdog writing nothing go unremarked",
          v["state"] != NEVER_RAN)
    check("...and states the expected firing count derived from the cadence",
          v["expected_firings"] == 240)
    check("...and says that count is DERIVED, not read off a run history",
          "DERIVED from the cadence" in v["why"])
    check("overdue PASSES the guard -- only the Routine can clear it, so a red "
          "here would be an unclearable red on every PR in the repo",
          v["ok"])
    check("...and it names the surface that DOES carry it, so a reader is not "
          "left hunting for a fix they cannot apply from a PR",
          "due-list row" in v["why"])
    txt = render(v, None)
    check("...and the rendered line carries the arithmetic, not just a label",
          "expected firings, ZERO receipts" in txt)

    v = grade(None, "absent", now, expected_since="not-a-date")
    check("an UNPARSEABLE arming date grades never_ran, never overdue -- "
          "`we did not look` must not be reported as `it is late`",
          v["state"] == NEVER_RAN)
    check("...and reports NO owed age rather than a fabricated zero",
          v["owed_hours"] is None)
    check("...and says plainly that absence could not be DATED",
          "could not be parsed" in v["why"])

    # A receipt that EXISTS is graded on its own age; the arming date is
    # irrelevant there and must not leak into the freshness verdict.
    check("an existing FRESH receipt is never re-graded as overdue, whatever "
          "the arming date says",
          grade(stamped(1), "read", now,
                expected_since="2020-01-01T00:00:00Z")["state"] == FRESH)
    check("...and an existing STALE receipt still grades stale, not overdue",
          grade(stamped(9), "read", now,
                expected_since="2020-01-01T00:00:00Z")["state"] == STALE)

    # The three time constants must not silently invert.
    check("the never-ran grace is wider than the Routine's cadence",
          NEVER_RAN_GRACE_HOURS > ROUTINE_CADENCE_HOURS)
    check("...and wider than the freshness window, so a receipt that exists is "
          "graded stale long before absence is called overdue",
          NEVER_RAN_GRACE_HOURS > DEFAULT_WINDOW_HOURS)
    check("the shipped arming constant PARSES -- a typo there would silently "
          "send every reading back to the undateable branch",
          _parse_ts(RECEIPT_EXPECTED_SINCE) is not None)

    v = grade(None, "unreadable", now)
    check("a corrupt receipt grades unreadable, NOT never_ran",
          v["state"] == UNREADABLE)
    check("unreadable FAILS -- a watchdog we cannot read is a defect", not v["ok"])

    v = grade(stamped(1), "read", now)
    check("a recent run grades fresh", v["state"] == FRESH and v["ok"])

    v = grade(stamped(9), "read", now)
    check("a run older than the window grades stale", v["state"] == STALE)
    check("stale FAILS -- this is the condition the guard exists for", not v["ok"])
    check("stale reports its age", v["age_hours"] == 9.0)

    v = grade({"generated_at": "not-a-date"}, "read", now)
    check("an UNDATEABLE receipt fails safe to unreadable, never to fresh",
          v["state"] == UNREADABLE and not v["ok"])

    # --- the boundary discriminates in BOTH directions --------------------------
    check("just inside the window passes", grade(stamped(5.9), "read", now)["ok"])
    check("just outside the window fails",
          not grade(stamped(6.1), "read", now)["ok"])

    # --- the window MUST stay wider than the Routine's own cadence ---------------
    # Otherwise a healthy Routine that merely ran late would fail this guard on
    # every PR -- and lateness is measured behaviour here, not a hypothetical.
    check("the guard window is wider than the Routine's cadence",
          DEFAULT_WINDOW_HOURS > ROUTINE_CADENCE_HOURS)
    check("and wide enough to tolerate several missed firings",
          DEFAULT_WINDOW_HOURS >= 3 * ROUTINE_CADENCE_HOURS)
    check("...and wider than the ~4h50m lateness probes.yml actually exhibited, "
          "so a merely-slow Routine cannot red every PR",
          DEFAULT_WINDOW_HOURS > 4.84)

    # --- THE WATCHER MUST ACTUALLY WRITE THE THING THIS GRADES -------------------
    # ⚠️ A guard whose producer never writes its artifact grades `never_ran`
    # forever and looks healthy doing it -- the "registered but never executed"
    # shape `check_selftest_wiring.py` exists for. So this asserts the producer
    # end to end rather than trusting that it was wired.
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))
    try:
        import queue_latency as ql
        check("the watcher targets the SAME receipt path this guard reads",
              ql.RECEIPT_PATH == RECEIPT)
        check("...and it is NOT the page latch, which is written only when a page "
              "fires and therefore cannot answer 'did the Routine run?'",
              ql.RECEIPT_PATH != ql.STATE_PATH)
        import inspect
        src = inspect.getsource(ql.main)
        check("the watcher DECLARES a --write-receipt flag for the Routine to pass",
              'add_argument("--write-receipt"' in src)
        check("...and calls write_receipt UNCONDITIONALLY on the flag, not only "
              "when a page is due (a conditional receipt is a second latch)",
              "if a.write_receipt:" in src)
        # Prove the producer really produces something this grader calls fresh.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "r.json"
            ql.write_receipt({"state": "measured", "waiting": 0, "worst_min": 0,
                              "population": "p"},
                             now, False, "M", rp)
            doc, st = read_receipt(rp)
            check("a receipt the watcher just wrote grades FRESH here -- producer "
                  "and grader agree end to end",
                  grade(doc, st, now)["state"] == FRESH)
    except ImportError:
        fails.append("could not import queue_latency to check the producer wiring")

    # --- the queue depth is REPORTED and never fails the guard -------------------
    hot = grade(stamped(1), "read", now)
    txt = render(hot, stamped(1, read_state="measured", waiting=9, worst_min=1345))
    check("a large queue is printed on every PR", "9 sub-session(s) waiting" in txt)
    check("...and does NOT fail the guard", hot["ok"])
    check("...and says plainly that it is not enforced here",
          "reported, not enforced here" in txt)
    txt2 = render(hot, stamped(1, read_state="no_observation"))
    check("a Routine that could not grade the queue says so, not 'empty'",
          "not an empty queue" in txt2)
    check("...and names WHY CI cannot substitute for it",
          "MCP tool CI does not hold" in txt2)
    check("a fresh receipt never implies the page was DELIVERED",
          "different question this guard cannot see" in txt2)

    if not quiet:
        print(f"\n{'FAIL' if fails else 'PASS'}: {len(fails)} failure(s)")
        for f in fails:
            print(f"  FAIL {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    args = ap.parse_args(argv)

    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    receipt, read_state = read_receipt()
    verdict = grade(receipt, read_state, datetime.now(timezone.utc),
                    args.window_hours)
    print(render(verdict, receipt))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
