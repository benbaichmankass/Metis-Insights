#!/usr/bin/env python3
"""operator-owed guard — an item may not be CARRIED FORWARD forever.

⚠️ THIS IS PART (d), THE PART THAT STOPS THE REGRESSION.
`BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION`
says so in its own resolution criteria: *"Without (d) this closes and silently
regresses, which is the same failure the register exists to prevent."* A
register alone is a nicer-looking list; a list that only grows is the thing
being fixed.

WHAT IT MEASURES, and why it is a measurement rather than an assertion
=====================================================================
For each item, the number of commits to `docs/claude/operator-owed-register.json`
since that item's own content last changed — read from `git log`, not from
anybody's self-report. Every session that ends is meant to touch the register;
a register commit in which item X did not change IS one session carrying X
forward unmoved. That is the thing the filing row measured by hand across three
board comments, made mechanical.

    carries = (leading register commits whose content for this id == the
               working tree's) - 1, floored at 0

The `- 1` is the commit that MADE the current content: an item just edited and
committed reads 0 carries (`moved`), one later register commit that left it
alone reads 1 (`carried`), two reads 2 and escalates.

⚠️ THE COUNT UNDER-REPORTS AND CAN NEVER OVER-REPORT. A session that never
touches the register at all leaves no commit and is invisible here. That
residual is why `src/runtime/operator_owed.py` carries a SECOND, independent
age trip path that needs no session to touch anything — the
`silent_refusal_alert.CAUSE_MIN_ROWS` shape, where an additional path can only
ADD escalation, never suppress one.

⚠️ A GREEN WITH ZERO OBSERVED TRANSITIONS IS UNPROVEN, NOT SUCCESS — the
filing row's `verification_obligation`, in as many words. So this prints
`observed_transitions` (how many times an item's content has actually CHANGED
between two commits, over the register's whole history) beside the verdict, and
says UNPROVEN when that total is zero. It does not FAIL on it: the commit that
creates the register cannot have moved anything, and a guard that fails on its
own first commit gets switched off. Reading the green without reading that line
is the mistake this line exists to prevent.

STRUCTURE, not just staleness
=============================
It also refuses items that cannot be worked: no owner class (an unclassified
item silently reads as somebody else's problem), a `defaulted_to_human` item
with neither a wire nor a reason, and — the anti-pattern gate — a reason that
rests on a failed remediation without naming a tested decision function that
EXISTS on disk. Verified, not presence-only: the `new-table-wiring-guard`
lesson is that a guard cheaper to lie to than to satisfy is worse than none.

Usage::

    python3 scripts/ci/check_operator_owed.py              # the standing check
    python3 scripts/ci/check_operator_owed.py --self-test  # exercise the failure paths
    python3 scripts/ci/check_operator_owed.py --verbose    # per-item grades
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.runtime.operator_owed import (  # noqa: E402
    OWNER_DEFAULTED,
    STATE_CARRIED,
    STATE_MOVED,
    STATE_NOT_MEASURABLE,
    STATE_RESOLVED,
    STATE_SNOOZED,
    TERMINAL_STATUSES,
    grade_item,
    is_escalation,
    summarise,
    validate_item,
)

# collapsed-state: resolved — this file branches on ALL SEVEN states, but it
# does so through the imported CONSTANTS (STATE_CARRIED / STATE_SNOOZED /
# STATE_NOT_MEASURABLE / STATE_RESOLVED, plus `is_escalation` for the two
# escalations), and `_states_in` can only see quoted literals. The one literal
# it does find here is help text inside the escalation message, not a branch.
# Constants are the better practice — a typo'd attribute raises where a typo'd
# literal is silent — so the right reading is that the guard's evidence
# mechanism does not fit a constants-based API, exactly as the
# `ib_venue_session.state` contract records for the same reason. State coverage
# is carried by tests/test_operator_owed.py, which asserts each state is
# reached AND that the check's exit code differs between them.
REGISTER = "docs/claude/operator-owed-register.json"


def _git(*args: str, cwd: pathlib.Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def register_commits(repo: pathlib.Path, path: str) -> List[str]:
    """Commit shas that touched the register, newest first."""
    out = _git("log", "--format=%H", "--", path, cwd=repo)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _items_at(repo: pathlib.Path, sha: str, path: str) -> Optional[Dict[str, Any]]:
    """`{id: item}` as of one commit. ``None`` when it cannot be read.

    ``None`` is 'we could not look at this commit' and the caller stops
    counting there rather than treating it as a difference — an unreadable
    revision must not manufacture a carry.
    """
    blob = _git("show", f"{sha}:{path}", cwd=repo)
    if not blob.strip():
        return None
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    return {str(i.get("id")): i for i in items if isinstance(i, dict)}


def measure_carries(
    repo: pathlib.Path,
    path: str,
    current: Dict[str, Any],
    shas: List[str],
) -> Tuple[Dict[str, Optional[int]], Dict[str, int]]:
    """Carries per id, and observed transitions per id, measured from git.

    Carries is ``None`` for every id when the register has no history yet —
    no carry EXISTS to count, which is `not_measurable`, never zero.
    """
    carries: Dict[str, Optional[int]] = {}
    transitions: Dict[str, int] = {item_id: 0 for item_id in current}

    if not shas:
        return {item_id: None for item_id in current}, transitions

    # One pass over history, newest first, reused for both measurements.
    history: List[Optional[Dict[str, Any]]] = [
        _items_at(repo, sha, path) for sha in shas]

    for item_id, item in current.items():
        leading = 0
        for snapshot in history:
            if snapshot is None:
                break
            if snapshot.get(item_id) == item:
                leading += 1
                continue
            break
        carries[item_id] = max(0, leading - 1)

        # A transition is any adjacent pair of readable snapshots whose content
        # for this id differs — i.e. the item genuinely CHANGED at some point.
        previous: Any = None
        seen_any = False
        for snapshot in history:
            if snapshot is None:
                continue
            content = snapshot.get(item_id)
            if seen_any and content is not None and content != previous:
                transitions[item_id] = transitions.get(item_id, 0) + 1
            if content is not None:
                previous = content
                seen_any = True
    return carries, transitions


def check(
    repo: pathlib.Path,
    *,
    now: Optional[_dt.datetime] = None,
    verbose: bool = False,
    path: str = REGISTER,
) -> int:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    register_path = repo / path
    if not register_path.exists():
        print(f"operator-owed: FAIL — {path} does not exist")
        return 1

    try:
        data = json.loads(register_path.read_text())
    except (ValueError, OSError) as exc:
        print(f"operator-owed: FAIL — {path} is unreadable: {exc}")
        return 1

    items = data.get("items")
    if not isinstance(items, list):
        print(f"operator-owed: FAIL — {path} has no 'items' list")
        return 1

    carry_limit = int(data.get("carry_limit") or 2)
    current = {str(i.get("id")): i for i in items if isinstance(i, dict)}

    problems: List[str] = []
    seen: set = set()
    for item in items:
        if not isinstance(item, dict):
            problems.append("an entry in 'items' is not an object")
            continue
        item_id = str(item.get("id"))
        if item_id in seen:
            problems.append(f"{item_id}: duplicate id")
        seen.add(item_id)
        problems.extend(validate_item(item))
        # Verified, not presence-only: a named decision function must EXIST.
        named = item.get("tested_decision_function")
        if named and not (repo / str(named)).exists():
            problems.append(
                f"{item_id}: tested_decision_function {named!r} does not exist "
                f"on disk — this guard verifies the path rather than taking the "
                f"declaration on trust")
        wire = item.get("automation_path")
        if (item.get("owner_class") == OWNER_DEFAULTED and wire
                and str(item.get("status", "")).strip().casefold()
                not in TERMINAL_STATUSES):
            first = str(wire).strip().split()[0].rstrip(",;")
            if "/" in first and not (repo / first).exists():
                problems.append(
                    f"{item_id}: automation_path names {first!r}, which does not "
                    f"exist on disk")

    shas = register_commits(repo, path)
    carries, transitions = measure_carries(repo, path, current, shas)

    grades = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id"))
        grade = grade_item(
            item, carries_unchanged=carries.get(item_id), now=now,
            carry_limit=carry_limit)
        grade["observed_transitions"] = transitions.get(item_id, 0)
        grades.append(grade)

    counts = summarise(grades)
    open_items = [g for g in grades if g["state"] != STATE_RESOLVED]
    total_transitions = sum(
        g["observed_transitions"] for g in grades)

    print(f"operator-owed: {len(items)} item(s) in {path}")
    print(f"operator-owed: register commits measured = {len(shas)} "
          f"(the denominator — a carry count over a short history is a weak "
          f"reading, not a clean one)")
    print("operator-owed: states = " + " · ".join(
        f"{state}={count}" for state, count in counts.items()))

    if verbose or any(g["escalates"] for g in grades):
        for grade in grades:
            print(f"  - {grade['id']}: {grade['state']} "
                  f"(carries={grade['carries_unchanged']}, "
                  f"age_days={None if grade['age_days'] is None else round(grade['age_days'], 2)}, "
                  f"transitions={grade['observed_transitions']}) — {grade['reason']}")

    # Each state gets its OWN line. A state nothing branches on is already
    # collapsed (the `provenance-consumer-guard` insight applied to states), and
    # these four say genuinely different things about the register's health:
    # deferred-with-a-trigger, approaching-the-limit, ungradeable, and finished.
    not_measurable = [g for g in grades if g["state"] == STATE_NOT_MEASURABLE]
    if not_measurable:
        print(f"operator-owed: {len(not_measurable)} item(s) NOT MEASURABLE — the "
              f"register history does not yet cover them, so no carry EXISTS to "
              f"count. This is 'we did not look', NOT a pass: "
              + ", ".join(g["id"] for g in not_measurable))

    carried = [g for g in grades if g["state"] == STATE_CARRIED]
    if carried:
        print(f"operator-owed: {len(carried)} item(s) CARRIED and one register "
              f"commit from escalating — move, dispose or defer them now: "
              + ", ".join(f"{g['id']}({g['carries_unchanged']}/{carry_limit})"
                          for g in carried))

    snoozed = [g for g in grades if g["state"] == STATE_SNOOZED]
    if snoozed:
        print(f"operator-owed: {len(snoozed)} item(s) SNOOZED behind a named "
              f"trigger (a date alone is refused): "
              + ", ".join(f"{g['id']} — {g['reason']}" for g in snoozed))

    resolved = [g for g in grades if g["state"] == STATE_RESOLVED]
    if resolved:
        print(f"operator-owed: {len(resolved)} item(s) RESOLVED and out of the "
              f"open set: " + ", ".join(g["id"] for g in resolved))

    if total_transitions == 0:
        print("operator-owed: ⚠️ UNPROVEN — zero observed transitions across the "
              "whole register history. Nothing has yet been shown to MOVE "
              "because this register exists. Per the filing row's "
              "verification_obligation, read this as unproven, not as success.")
    else:
        moved_ids = [g["id"] for g in grades if g["observed_transitions"]]
        print(f"operator-owed: observed transitions = {total_transitions} "
              f"across {len(moved_ids)} item(s): {', '.join(sorted(moved_ids))}")

    escalated = [g for g in grades if is_escalation(g["state"])]
    if escalated:
        print()
        print("operator-owed: FAIL — an item has been carried without moving. "
              "This is the escalation; re-listing it is what it replaces.")
        for grade in escalated:
            print(f"  ✗ {grade['id']}: {grade['state']} — {grade['reason']}")
        print()
        print("  To clear it, do ONE of these — none of them is 'mention it "
              "again in a hand-off':")
        print("   1. ACT on it and record the outcome (status=resolved + a "
              "'resolution' saying what happened).")
        print("   2. MOVE it: if it is defaulted_to_human, build the wire and "
              "record automation_path; that is a real state change.")
        print("   3. DEFER it honestly: snoozed_until + a named snooze_trigger "
              "(a date alone is a mute button and is refused).")
        print("   4. WITHDRAW it: status=withdrawn + a 'resolution' saying why "
              "it is no longer owed.")

    if problems:
        print()
        print("operator-owed: FAIL — un-workable item(s):")
        for problem in sorted(set(problems)):
            print(f"  ✗ {problem}")

    if escalated or problems:
        return 1
    print(f"operator-owed: OK — {len(open_items)} open item(s), none carried "
          f"past the limit of {carry_limit}")
    return 0


# ---------------------------------------------------------------------------
# self-test — the failure paths, exercised
# ---------------------------------------------------------------------------

def _self_test() -> int:
    """Prove each failure path FAILS and the clean case passes.

    A guard whose failure path is never exercised is indistinguishable from one
    that always passes.
    """
    from src.runtime.operator_owed import (
        STATE_ESCALATE_AGED, STATE_ESCALATE_CARRIED,
    )

    now = _dt.datetime(2026, 8, 25, 19, 0, tzinfo=_dt.timezone.utc)
    failures: List[str] = []

    def base(**over: Any) -> Dict[str, Any]:
        item = {
            "id": "OO-TEST",
            "title": "a test item",
            "opened_at": "2026-08-25T18:00:00+00:00",
            "last_state_change_at": "2026-08-25T18:00:00+00:00",
            "severity": "high",
            "status": "open",
            "owner_class": "judgement",
            "owner_class_basis": (
                "a long enough basis string to clear the minimum length bar set "
                "by the module"),
        }
        item.update(over)
        return item

    def expect(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")

    # --- the carry axis, the (d) requirement ---
    expect("carry 0 -> moved",
           grade_item(base(), carries_unchanged=0, now=now)["state"],
           STATE_MOVED)
    expect("carry 1 -> carried",
           grade_item(base(), carries_unchanged=1, now=now)["state"],
           STATE_CARRIED)
    expect("carry 2 -> escalate",
           grade_item(base(), carries_unchanged=2, now=now)["state"],
           STATE_ESCALATE_CARRIED)
    expect("carry 2 escalates",
           grade_item(base(), carries_unchanged=2, now=now)["escalates"], True)

    # --- `not_measurable` is NOT `moved`: the collapse this exists to stop ---
    expect("carry None -> not_measurable",
           grade_item(base(), carries_unchanged=None, now=now)["state"],
           STATE_NOT_MEASURABLE)
    expect("not_measurable does not escalate",
           grade_item(base(), carries_unchanged=None, now=now)["escalates"], False)

    # --- the age axis, independent of carry ---
    old = base(last_state_change_at="2026-08-20T18:00:00+00:00")
    expect("aged out on severity=high (5d > 3d)",
           grade_item(old, carries_unchanged=0, now=now)["state"],
           STATE_ESCALATE_AGED)
    expect("age fires even when carry is unmeasurable",
           grade_item(old, carries_unchanged=None, now=now)["state"],
           STATE_ESCALATE_AGED)
    expect("critical ages out in a day",
           grade_item(base(severity="critical",
                           last_state_change_at="2026-08-24T00:00:00+00:00"),
                      carries_unchanged=0, now=now)["state"],
           STATE_ESCALATE_AGED)

    # --- terminal + defer ---
    expect("resolved is terminal",
           grade_item(base(status="resolved"), carries_unchanged=9, now=now)["state"],
           STATE_RESOLVED)
    expect("a snooze needs a trigger, not just a date",
           grade_item(base(snoozed_until="2026-09-30T00:00:00+00:00"),
                      carries_unchanged=2, now=now)["state"],
           STATE_ESCALATE_CARRIED)
    expect("a snooze with a named trigger defers",
           grade_item(base(snoozed_until="2026-09-30T00:00:00+00:00",
                           snooze_trigger="the next funded alpaca_live session"),
                      carries_unchanged=2, now=now)["state"],
           STATE_SNOOZED)

    # --- structural refusals ---
    if not validate_item(base(owner_class="unclassified")):
        failures.append("an unclassified item must be refused")
    if not validate_item(base(owner_class="defaulted_to_human",
                              automation_path=None,
                              cannot_automate_reason=None)):
        failures.append(
            "a defaulted_to_human item with neither wire nor reason must be refused")
    # THE ANTI-PATTERN GATE: a failed remediation is not a sufficient reason.
    anti = base(
        owner_class="defaulted_to_human",
        automation_path=None,
        cannot_automate_reason=(
            "an auto-remediation attempt cancelled the wrong leg once, so this "
            "is left to a human from now on"),
        tested_decision_function=None)
    if not validate_item(anti):
        failures.append(
            "'one remediation attempt failed' must be refused without a tested "
            "decision function — this is the anti-pattern the register is named "
            "after")
    if validate_item(dict(anti, tested_decision_function="src/runtime/x.py")):
        failures.append(
            "a failed-remediation reason WITH a tested decision function must pass")
    if validate_item(base()):
        failures.append("a well-formed item must produce no problems")
    if not validate_item(base(severity="P1")):
        failures.append("a non-canonical severity spelling must be refused")

    if failures:
        print("operator-owed self-test: FAIL")
        for failure in failures:
            print(f"  ✗ {failure}")
        return 1
    print("operator-owed self-test: OK — carry, age, defer, terminal, "
          "not_measurable and the anti-pattern gate all behave")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true",
                    help="exercise the failure paths and exit")
    ap.add_argument("--verbose", action="store_true",
                    help="print a grade line per item")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    return check(REPO, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
