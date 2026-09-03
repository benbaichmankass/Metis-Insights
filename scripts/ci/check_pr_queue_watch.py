#!/usr/bin/env python3
"""IS THE PR-QUEUE WATCHER ACTUALLY RUNNING?

Operator directive, 2026-09-02:

    "I want actual guards and mechanisms in the repo/vm themselves that watch
     the manager and enforce the rules on him."

`scripts/ops/pr_queue_latency.py` is the watcher. This is the thing that makes
it a GUARD rather than a script somebody could have run, and the distinction is
the whole point:

    **Every mechanism the manager had to CHOOSE to run went unused. Every
    mechanism that STOOD IN THE WAY worked.**

So the watcher is not invokable from a session prompt, a skill, or a checklist
step -- it is a scheduled workflow -- and its DEADNESS fails this guard, which
`run_guards.py` runs on every PR. There is nothing to remember and nothing to
opt into. A manager who ignores the queue does not thereby silence the alarm;
a manager who stops the watcher reddens everybody's CI.

────────────────────────────────────────────────────────────────────────────
⚠️ IT GRADES LIVENESS ONLY. IT NEVER FAILS A PR FOR THE BACKLOG'S SIZE.
────────────────────────────────────────────────────────────────────────────

This is a deliberate line, not an omission. `run_guards.py` already records why
the open-PR COMPLETENESS check is kept out of per-PR CI:

    "the check would then be measuring a moving target that changes between the
     run and the merge, reddening PRs for a row nobody could have written yet"

The same objection applies with more force here: a contributor's PR must never
go red because the MANAGER has four other PRs unmerged. That would punish the
one actor who is not at fault and would train everyone to ignore the guard --
the desensitised-alarm failure this repo has measured at 202 of 376 CRITICALs in
one window being a single un-latched alarm.

So the backlog is REPORTED here (so it is visible on every PR) and ESCALATED by
the watcher's own workflow run, which fails only when the page is DUE under the
band/cooldown latch. Two different consequences for two different facts.

────────────────────────────────────────────────────────────────────────────
FOUR STATES, NEVER COLLAPSED
────────────────────────────────────────────────────────────────────────────

``fresh``       a run was recorded inside the window.                    PASS
``never_ran``   no receipt exists at all. ⚠️ **NOT A FAILURE TODAY, AND THAT IS
                CORRECT rather than lenient** -- it is the accurate reading until
                the workflow fires for the first time, and failing on it would
                red every PR in the repo the moment this merges, which is how a
                guard gets disabled instead of fixed. `check_drain_liveness.py`
                takes the same position for the same reason. The guard ARMS
                ITSELF on the first successful run: once a receipt exists,
                `stale` becomes reachable and there is no flag to unset.  PASS
``stale``       the receipt exists and its newest run is older than the window.
                The watcher HAS run and has STOPPED -- the failure this exists
                for.                                                     FAIL
``unreadable``  **WE COULD NOT LOOK.** The receipt is corrupt. Not evidence about
                the watcher at all, and a corrupt watchdog receipt is itself a
                defect, so it fails LOUDLY rather than passing quietly.  FAIL

⚠️ `never_ran` and `stale` are distinct even though both mean "no recent run":
collapsing them would report *"it was never wired up"* as *"it broke"*, which
sends a reader to investigate a regression that never happened.

────────────────────────────────────────────────────────────────────────────
WHY A RECEIPT AND NOT THE WORKFLOW'S RUN HISTORY
────────────────────────────────────────────────────────────────────────────

Reading `actions/runs` needs a network call and a token from inside a guard that
must be fast, offline and deterministic. More importantly, a run that STARTED is
not a run that GRADED -- `session-reaper.yml` had five runs before its first
success, every one of them a row in the run history. The receipt is written by
the watcher only after it has actually assessed the queue, so it attests the
thing that matters.

⚠️ AND A CRON IS NOT EVIDENCE OF A RUN. `probes.yml`'s first scheduled run in
this repo fired ~4h50m late and once instead of daily. Read `generated_at`,
never the cron expression.

EXIT: 0 pass - 1 fail.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
RECEIPT = REPO_ROOT / "docs" / "claude" / "work" / "PR-QUEUE-WATCH.json"

FRESH, STALE, NEVER_RAN, UNREADABLE = "fresh", "stale", "never_ran", "unreadable"

#: How old the newest recorded run may be before the watcher counts as stopped.
#: CHOSEN against the watcher's own 12h refresh floor
#: (`pr_queue_latency.DEFAULT_REFRESH_HOURS`): the window must be comfortably
#: WIDER than that floor, or a perfectly healthy quiet watcher would fail this
#: guard every time it correctly declined to rewrite an unchanged receipt.
#: 30h is 2.5x the floor, so ONE missed cadence is tolerated and two are not.
#: ⚠️ If the refresh floor is ever raised, this must be raised with it -- the
#: watcher's self-test asserts the ordering so the pair cannot silently invert.
DEFAULT_WINDOW_HOURS = 30.0


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
    """PURE, so the policy is arguable in tests rather than against a live repo."""
    if read_state == "unreadable":
        return {"state": UNREADABLE, "age_hours": None, "ok": False,
                "why": ("the receipt exists and could not be parsed -- WE COULD NOT "
                        "LOOK. This is not evidence about the watcher, and a corrupt "
                        "watchdog receipt is itself a defect, so it fails loudly "
                        "rather than passing quietly.")}
    if read_state == "absent" or receipt is None:
        return {"state": NEVER_RAN, "age_hours": None, "ok": True,
                "why": ("no receipt exists, so the watcher has NEVER run. This is the "
                        "accurate reading until `pr-queue-watch.yml` fires for the "
                        "first time and is deliberately NOT a failure -- failing here "
                        "would red every PR in the repo on the day this merges. The "
                        "guard arms itself on the first successful run.")}
    ts = _parse_ts(receipt.get("generated_at"))
    if ts is None:
        return {"state": UNREADABLE, "age_hours": None, "ok": False,
                "why": ("the receipt carries no parseable `generated_at`, so it cannot "
                        "be DATED. A record that cannot be dated cannot be shown to be "
                        "current, and the fail-safe reading of a watchdog is stale.")}
    age = (now - ts).total_seconds() / 3600.0
    if age >= window_hours:
        return {"state": STALE, "age_hours": round(age, 1), "ok": False,
                "why": (f"the newest recorded run is {age:.1f}h old (window "
                        f"{window_hours}h). The watcher HAS run before and has "
                        f"STOPPED -- dispatch `pr-queue-watch.yml` and find out why "
                        f"the cadence died. ⚠️ A scheduled workflow in this repo is "
                        f"not evidence it fires: read the run history, not the cron.")}
    return {"state": FRESH, "age_hours": round(age, 1), "ok": True,
            "why": f"a run was recorded {age:.1f}h ago, inside the {window_hours}h window."}


def render(verdict: Dict[str, Any], receipt: Optional[Dict[str, Any]]) -> str:
    lines = [f"pr-queue-watch: {verdict['state'].upper()} -- {verdict['why']}"]
    if receipt:
        # ⚠️ REPORTED, NEVER FAILED ON. See the module docstring: a contributor's
        # PR must not go red because the manager has a backlog.
        read = receipt.get("read_state")
        waiting = receipt.get("waiting")
        worst = receipt.get("worst_hours")
        if read and read != "measured":
            lines.append(
                f"  queue read: {str(read).upper()} -- the watcher ran and could NOT "
                f"grade the queue. This is not an empty queue.")
        elif waiting is not None:
            lines.append(
                f"  queue at last run: {waiting} PR(s) waiting on a merge decision"
                + (f", worst {worst}h" if worst is not None else "")
                + "  (reported, not enforced here)")
    return "\n".join(lines)


def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    fails: List[str] = []
    now = datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc)

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

    v = grade(stamped(40), "read", now)
    check("a run older than the window grades stale", v["state"] == STALE)
    check("stale FAILS -- this is the condition the guard exists for", not v["ok"])
    check("stale reports its age", v["age_hours"] == 40.0)

    v = grade({"generated_at": "not-a-date"}, "read", now)
    check("an UNDATEABLE receipt fails safe to unreadable, never to fresh",
          v["state"] == UNREADABLE and not v["ok"])

    # --- the boundary discriminates in BOTH directions --------------------------
    check("just inside the window passes", grade(stamped(29.9), "read", now)["ok"])
    check("just outside the window fails", not grade(stamped(30.1), "read", now)["ok"])

    # --- the window MUST stay wider than the watcher's refresh floor -------------
    # Otherwise a healthy quiet watcher, correctly declining to rewrite an
    # unchanged receipt, would fail this guard on every PR.
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))
    try:
        import pr_queue_latency as pql
        check("the guard window is wider than the watcher's refresh floor",
              DEFAULT_WINDOW_HOURS > pql.DEFAULT_REFRESH_HOURS)
        check("and wide enough to tolerate one missed cadence",
              DEFAULT_WINDOW_HOURS >= 2 * pql.DEFAULT_REFRESH_HOURS)
    except ImportError:
        fails.append("could not import pr_queue_latency to check the window ordering")

    # --- THE ESCALATION CHANNEL MUST ACTUALLY BE ABLE TO FIRE --------------------
    # ⚠️ A RECURRENCE PREVENTION, not a style check. The workflow captures the
    # watcher's exit code into `$GITHUB_OUTPUT` and branches on it; exit 3 is the
    # escalation and it FAILS the job, which is how `claude-run-failure-alert.yml`
    # reaches the operator. Shipped 2026-09-02 as `python3 ... | tee /tmp/digest.txt`
    # followed by `echo "code=$?"` -- and `$?` after a PIPELINE is the LAST
    # command's status, i.e. `tee`'s, which is ALWAYS 0. The first live run
    # (33666587546) measured 6 waiting PRs at 110.5h, stamped `last_paged_at` in
    # its own receipt, and concluded `success`. The watcher counted the CALL and
    # not its EFFECT.
    #
    # ⚠️ THE FAILURE WAS INVISIBLE FROM EVERY GREEN SIGNAL: 33 + 17 self-test
    # cases passed, `guards` passed, the receipt landed on main and read
    # `measured`. Only comparing the run's CONCLUSION against the receipt's own
    # `last_paged_at` exposed it -- which is why this assertion reads the YAML
    # rather than trusting any of them.
    wf = REPO_ROOT / ".github" / "workflows" / "pr-queue-watch.yml"
    try:
        text = wf.read_text(encoding="utf-8")
    except OSError:
        text = None
    if text is None:
        # ⚠️ `unreadable` is NOT `clean`. We could not look, so this is a finding.
        fails.append("could not read pr-queue-watch.yml to check the exit-code capture")
    else:
        capture = [ln for ln in text.splitlines() if 'code=$?' in ln]
        check("the workflow captures the watcher's exit code at all", len(capture) == 1)
        # The command whose status is captured must not be a pipeline. Look at the
        # lines between the assess `run:` and the capture.
        idx = text.index('code=$?')
        window = text[max(0, idx - 700):idx]
        check("the assessed command is NOT piped -- `$?` after a pipe is `tee`'s, "
              "and `tee` always exits 0", "| tee" not in window)
        check("the digest is still shown in the log (redirect, then cat)",
              "> /tmp/digest.txt" in window)
        check("exit 3 (escalate) still FAILS the job, which is the only route to "
              "the operator", "PR-QUEUE ESCALATION" in text and "exit 1" in text)

    # --- the backlog is REPORTED and never fails the guard -----------------------
    hot = grade(stamped(1), "read", now)
    txt = render(hot, stamped(1, read_state="measured", waiting=9, worst_hours=99.0))
    check("a large backlog is printed on every PR", "9 PR(s) waiting" in txt)
    check("...and does NOT fail the guard", hot["ok"])
    check("...and says plainly that it is not enforced here",
          "reported, not enforced here" in txt)
    txt2 = render(hot, stamped(1, read_state="no_observation"))
    check("a watcher that could not grade the queue says so, not 'empty'",
          "not an empty queue" in txt2)

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
    verdict = grade(receipt, read_state, datetime.now(timezone.utc), args.window_hours)
    print(render(verdict, receipt))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
