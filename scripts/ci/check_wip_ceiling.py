#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::wip-ceiling-guard (--self-test, then the scan)
"""A5 — the WIP ceiling of 8 work objects IN FLIGHT, enforced rather than advisory.

Phase C of the operating-layer build, and the half that makes the migration safe
to land. ``scripts/ops/migrate_backlog_to_work_objects.py`` carries 575 rows into
``docs/claude/work/``; without this guard the store would render as 575 things in
flight, which is the condition the redesign exists to end.

⚠️ TWO POPULATIONS. DO NOT CONFLATE THEM.
------------------------------------------
* **The REGISTER is UNCAPPED.** ``docs/claude/OPEN-ITEMS.json`` and the review
  backlogs may grow without limit. ``scripts/ci/check_open_items.py`` sets
  ``MAX_ITEMS = None`` **and that stays.** The operator reversed the old cap on
  2026-08-26: *"we don't want to cap the number of bugs we can track, we want to
  ensure that they are actually being tracked, fixed, and learned from"*. A cap
  on a register of KNOWN PROBLEMS just deletes knowledge — a session reading a
  cap either declines to file a row it should file, or evicts a live one.
* **The IN-FLIGHT SET is CAPPED at 8.** That is this file.

Carrying everything is not the same as everything being open. The registry may
hold hundreds of objects while at most 8 are being worked. A guard that confused
the two would re-introduce exactly the eviction rule that was reversed, so the
self-test below asserts ``check_open_items.MAX_ITEMS is None`` — if a future
change caps the register believing it is implementing "the ceiling", this fails
and says why.

WHAT COUNTS
-----------
Work OBJECTS with ``lifecycle: in_flight``. Not steps (unbounded and
session-sized by design), not intents, not register rows. ``dormant``, ``ready``,
``waiting``, ``done`` and ``accepted`` do not count — and ``waiting`` in
particular is *deliberately* free: a thing blocked on an operator decision or an
external event is not consuming the attention the ceiling rations. Collapsing
``waiting`` into ``in_flight`` would make the ceiling punish honesty about
blockage, which is the opposite of what the six-state vocabulary is for.

EXCEEDING IT PRODUCES AN OPERATOR DECISION
------------------------------------------
The ceiling is enforced, so a ninth in-flight object FAILS CI. It is not,
however, a wall with nothing behind it: the escape is a written justification at
``docs/claude/work/wip-ceiling-exception.yaml`` that names the exact object ids
it covers and carries an operator decision. That file is the mechanism by which
"we need a ninth" stops being a session's private call and becomes a decision
someone made on the record.

* ``decision: pending`` still FAILS — with a different message. A justification
  filed is not a justification granted, and rendering those two identically is
  the collapsed-state defect this repo has a guard for.
* ``decision: approved`` passes, loudly, and only for the ids it names. A blanket
  standing exemption is refused: an exception that does not name its objects is
  a permanent cap raise wearing an exception's clothes.
* ``decision: refused`` fails.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OBJECTS_DIR = REPO_ROOT / "docs" / "claude" / "work" / "objects"
EXCEPTION_FILE = REPO_ROOT / "docs" / "claude" / "work" / "wip-ceiling-exception.yaml"

#: The cap, from the build plan and the schema design. Chosen, not measured.
CEILING = 8

#: Only this one state consumes a ceiling slot. See the module docstring for why
#: `waiting` is deliberately excluded.
IN_FLIGHT = "in_flight"

#: Every legal lifecycle value. A file carrying something else is a defect worth
#: failing on: an unreadable state is not a safe state, and silently treating it
#: as "not in flight" is how a ceiling stops counting.
LIFECYCLES = {"dormant", "ready", "in_flight", "waiting", "done", "accepted"}


def _lifecycle(text: str) -> Optional[str]:
    """The object's lifecycle, or None if we could not read one.

    A line scan rather than a YAML parse, matching work_phase_ping.py: this runs
    over files that may predate a schema change, and a parse error must not turn
    a ceiling check into a crash. None means *we did not read one*, which the
    caller reports as unreadable rather than assuming a value.
    """
    for line in text.split("\n"):
        if line.startswith("lifecycle:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'") or None
    return None


def scan(objects_dir: Path) -> Tuple[List[str], List[str], Dict[str, int]]:
    """Returns (in_flight_ids, problem_messages, counts_by_lifecycle)."""
    in_flight: List[str] = []
    problems: List[str] = []
    counts: Dict[str, int] = {}
    if not objects_dir.is_dir():
        return in_flight, [f"{objects_dir} does not exist"], counts

    for path in sorted(objects_dir.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        life = _lifecycle(text)
        oid = path.stem
        if life is None:
            problems.append(f"{path.name}: no `lifecycle` field could be read")
            continue
        if life not in LIFECYCLES:
            problems.append(
                f"{path.name}: lifecycle {life!r} is not one of {sorted(LIFECYCLES)}")
            continue
        counts[life] = counts.get(life, 0) + 1
        if life == IN_FLIGHT:
            in_flight.append(oid)
    return in_flight, problems, counts


def load_exception(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {"_unparseable": True}
    return d if isinstance(d, dict) else {"_unparseable": True}


def evaluate(in_flight: List[str], exception: Optional[Dict[str, Any]],
             ceiling: int = CEILING) -> Tuple[str, str]:
    """Grade the in-flight set. Pure, so the policy is arguable in tests.

    Returns (verdict, message). Verdicts:
      ok · ok_under_approved_exception · over_no_exception ·
      over_exception_pending · over_exception_refused · over_exception_stale ·
      over_exception_unparseable
    """
    n = len(in_flight)
    if n <= ceiling:
        return "ok", f"{n} work object(s) in flight, ceiling {ceiling}."

    over = n - ceiling
    if exception is None:
        return "over_no_exception", (
            f"{n} work objects are in flight — {over} over the ceiling of {ceiling}, "
            f"and there is no justification on file.\n"
            f"In flight: {', '.join(in_flight)}\n"
            f"The ceiling is enforced, not advisory. Either move something out of "
            f"`in_flight` (`waiting` is free — a thing blocked on someone else is "
            f"not consuming attention), or file "
            f"docs/claude/work/wip-ceiling-exception.yaml naming these ids and the "
            f"reason, which makes it an operator decision instead of a private call.")

    if exception.get("_unparseable"):
        return "over_exception_unparseable", (
            "the WIP exception file exists but could not be parsed — so whether the "
            "excess is justified is UNKNOWN. We did not look, which is not the same "
            "as 'it is fine'. Failing closed.")

    decision = str(exception.get("decision") or "").strip().lower()
    covers = [str(x) for x in (exception.get("covers") or [])]

    if decision == "refused":
        return "over_exception_refused", (
            f"{n} in flight, {over} over the ceiling. The exception on file was "
            f"REFUSED — move something out of `in_flight`.")
    if decision == "pending":
        return "over_exception_pending", (
            f"{n} in flight, {over} over the ceiling of {ceiling}. A justification is "
            f"filed and is AWAITING AN OPERATOR DECISION.\n"
            f"⚠️ Filed is not granted. This still fails: the ceiling's whole function "
            f"is that a ninth parent requires a human to say yes, and passing on a "
            f"self-written justification would make it advisory again.")
    if decision != "approved":
        return "over_exception_pending", (
            f"{n} in flight, {over} over the ceiling. The exception file carries "
            f"decision={decision!r}, which is not one of pending/approved/refused.")

    # Approved — but only for what it actually names.
    uncovered = [i for i in in_flight if i not in covers]
    if len(in_flight) - len(uncovered) < 0 or uncovered and len(in_flight) > ceiling:
        # An approval must name enough objects to bring the uncovered set within
        # the ceiling; otherwise it is a blanket raise.
        if len(uncovered) > ceiling:
            return "over_exception_stale", (
                f"{n} in flight; the approved exception names {len(covers)} id(s), "
                f"leaving {len(uncovered)} uncovered — still over the ceiling of "
                f"{ceiling}.\nUncovered: {', '.join(uncovered)}\n"
                f"An exception covers the objects it NAMES. One that does not name "
                f"them is a standing cap raise, which is the thing a ceiling cannot "
                f"survive.")
    if not exception.get("approved_by") or not exception.get("approved_at"):
        return "over_exception_pending", (
            "the exception says decision: approved but carries no `approved_by` / "
            "`approved_at`. An approval with nobody's name on it is not a decision, "
            "it is a session approving itself.")

    return "ok_under_approved_exception", (
        f"{n} in flight, over the ceiling of {ceiling}, under an APPROVED exception "
        f"({exception.get('approved_by')}, {exception.get('approved_at')}) covering "
        f"{len(covers)} id(s). This is a deliberate, recorded state — not a clean one.")


_FAILING = {"over_no_exception", "over_exception_pending", "over_exception_refused",
            "over_exception_stale", "over_exception_unparseable"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    in_flight, problems, counts = scan(OBJECTS_DIR)
    total = sum(counts.values())
    print(f"wip-ceiling: {total} work object(s) in the store; "
          f"by lifecycle: {dict(sorted(counts.items()))}")
    print(f"wip-ceiling: the REGISTER is uncapped; only the IN-FLIGHT set is capped "
          f"(at {CEILING}). Different populations.")

    if problems:
        for p in problems:
            print(f"::error::wip-ceiling: {p}")
        print(f"::error::{len(problems)} object file(s) could not be graded. An "
              f"unreadable lifecycle is not a safe one — a ceiling that silently "
              f"skips what it cannot read has stopped counting.")
        return 1

    verdict, message = evaluate(in_flight, load_exception(EXCEPTION_FILE))
    if verdict in _FAILING:
        print(f"::error::wip-ceiling: {message}")
        print(f"wip-ceiling: verdict={verdict}")
        return 1
    if verdict == "ok_under_approved_exception":
        print(f"::warning::wip-ceiling: {message}")
    else:
        print(f"wip-ceiling: OK — {message}")
    print(f"wip-ceiling: verdict={verdict}")
    return 0


def _self_test() -> int:
    """A ceiling whose refusal path never runs is indistinguishable from no ceiling."""
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r}'}")

    ids = lambda n: [f"WO-{i}" for i in range(n)]

    check("8 in flight is allowed", evaluate(ids(8), None)[0], "ok")
    check("THE PHASE'S DONE-CONDITION: a NINTH is refused",
          evaluate(ids(9), None)[0], "over_no_exception")
    check("0 in flight is fine", evaluate([], None)[0], "ok")

    # A filed-but-undecided justification must NOT pass.
    check("a PENDING justification still fails — filed is not granted",
          evaluate(ids(9), {"decision": "pending", "covers": ids(9)})[0],
          "over_exception_pending")
    check("a REFUSED justification fails",
          evaluate(ids(9), {"decision": "refused", "covers": ids(9)})[0],
          "over_exception_refused")
    check("an unparseable exception fails CLOSED, not open",
          evaluate(ids(9), {"_unparseable": True})[0], "over_exception_unparseable")
    check("an approval with nobody's name on it is not a decision",
          evaluate(ids(9), {"decision": "approved", "covers": ids(9)})[0],
          "over_exception_pending")
    check("an APPROVED, signed, id-naming exception passes",
          evaluate(ids(9), {"decision": "approved", "covers": ids(9),
                            "approved_by": "operator", "approved_at": "2026-09-01"})[0],
          "ok_under_approved_exception")
    check("a blanket approval naming nothing is refused",
          evaluate(ids(20), {"decision": "approved", "covers": [],
                             "approved_by": "operator", "approved_at": "2026-09-01"})[0],
          "over_exception_stale")

    # Which states consume a slot.
    check("dormant does not consume a ceiling slot",
          _lifecycle("lifecycle: dormant\n") == IN_FLIGHT, False)
    check("waiting does not consume a slot — blockage must stay honest",
          _lifecycle("lifecycle: waiting\n") == IN_FLIGHT, False)
    check("in_flight does", _lifecycle("lifecycle: in_flight\n"), "in_flight")
    check("an absent lifecycle reads as None, never as a state",
          _lifecycle("id: X\n"), None)
    check("an empty lifecycle reads as None, never as ''",
          _lifecycle("lifecycle:\n"), None)

    # ⚠️ The conflation guard. This is the whole reason it is here.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_coi", REPO_ROOT / "scripts" / "ci" / "check_open_items.py")
        coi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(coi)
        good = coi.MAX_ITEMS is None
        ok &= good
        if good:
            outcome = "PASS"
        else:
            outcome = (
                f"FAIL got={coi.MAX_ITEMS!r} — the in-flight SET is capped at 8; "
                "the REGISTER is not. Capping the register re-introduces the "
                "eviction rule the operator reversed on 2026-08-26, which told "
                "sessions to delete knowledge to satisfy a rule nothing enforced.")
        print("  self-test (THE REGISTER STAYS UNCAPPED — "
              f"check_open_items.MAX_ITEMS is None): {outcome}")
    except Exception as e:  # pragma: no cover
        ok = False
        print(f"  self-test (register-stays-uncapped cross-check): FAIL {e}")

    print("wip-ceiling self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
