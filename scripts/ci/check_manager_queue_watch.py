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

FOUR STATES, NEVER COLLAPSED
----------------------------
``fresh``       a run was recorded inside the window.                     PASS
``never_ran``   no receipt exists at all. ⚠️ **NOT A FAILURE TODAY, AND THAT IS
                CORRECT rather than lenient** — it is the accurate reading until
                the Routine first runs with `--write-receipt`, and failing on it
                would red every PR in the repo the day this merges, which is how
                a guard gets disabled instead of fixed. `check_pr_queue_watch.py`
                and `check_drain_liveness.py` both take this position. The guard
                ARMS ITSELF on the first receipt: once one exists, `stale`
                becomes reachable and there is no flag to unset.            PASS
``stale``       the receipt exists and its newest run is older than the window.
                The Routine HAS run and has STOPPED — the failure this exists
                for.                                                       FAIL
``unreadable``  **WE COULD NOT LOOK.** The receipt is corrupt, or carries no
                parseable `generated_at`. Not evidence about the Routine at all,
                and a corrupt watchdog receipt is itself a defect, so it fails
                LOUDLY rather than passing quietly.                        FAIL

⚠️ `never_ran` and `stale` are distinct even though both mean "no recent run":
collapsing them would report *"it was never wired up"* as *"it broke"*, sending
a reader to investigate a regression that never happened.

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
          window_hours: float = DEFAULT_WINDOW_HOURS) -> Dict[str, Any]:
    """PURE, so the policy is arguable in tests rather than against a live Routine."""
    if read_state == "unreadable":
        return {"state": UNREADABLE, "age_hours": None, "ok": False,
                "why": ("the receipt exists and could not be parsed -- WE COULD NOT "
                        "LOOK. This is not evidence about the Routine, and a corrupt "
                        "watchdog receipt is itself a defect, so it fails loudly "
                        "rather than passing quietly.")}
    if read_state == "absent" or receipt is None:
        return {"state": NEVER_RAN, "age_hours": None, "ok": True,
                "why": ("no receipt exists, so the Manager Queue Watch Routine has "
                        "never run with --write-receipt. This is the accurate "
                        "reading until it next fires and is deliberately NOT a "
                        "failure -- failing here would red every PR in the repo on "
                        "the day this merges. The guard arms itself on the first "
                        "receipt.")}
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
