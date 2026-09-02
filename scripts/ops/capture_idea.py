#!/usr/bin/env python3
"""CAPTURE AN IDEA AS A WORK OBJECT — in one command, in seconds.

WHY THIS SHIPS IN THE SAME CHANGE AS THE GATE
----------------------------------------------
`spawn_gate.py` refuses a spawn that names no parent object. **A gate without a
cheap capture path fails in the opposite direction:** if turning the operator's
idea into a work object costs more than just dispatching a session, a manager
under pressure dispatches anyway, and the gate converts a *focus* problem into a
*logging* problem with the idea lost entirely. That is strictly worse than no
gate. So the rule this file exists to satisfy is:

    THE CAPTURE PATH MUST BE CHEAPER THAN THE BUILD PATH.

One command, two required facts, and it prints the exact `register` line to run
next. The operator's own instruction is the target behaviour:

    "What I expect is for you to tell me where something is in the general work
     plan and the general road map, not to stop, drop everything else and get to
     it immediately."

⚠️ `--intent` IS REQUIRED AND DELIBERATELY NOT DEFAULTED TO THE CYCLE'S
------------------------------------------------------------------------
This is the single most important decision in the file, and it is the one that
keeps the gate from being theatre. If capture defaulted `--intent` to whatever
`CYCLE-PRIORITY.json` currently names, then **every captured idea would pass the
priority gate by construction** — the gate would compare a value against itself
and permit everything, forever, while looking like enforcement. Measured today
there is exactly ONE intent on disk (`IN-20260901-OPERATING-LAYER`), so that
failure would be total rather than occasional.

Naming the intent is therefore the manager's judgement and the file refuses to
make it for them. If an idea genuinely fits no existing intent, that is a real
finding — the roadmap's intent layer is meant to be short and hand-written, and
"this belongs to nothing we have committed to" is exactly the condition the
design says must be SEEN rather than accumulate silently.

WHAT IT WRITES, AND WHAT IT DOES NOT CLAIM
-------------------------------------------
* ``lifecycle: dormant`` — capturing is not starting. The WIP ceiling counts
  `in_flight`, so capture can never consume a slot, and an idea parked here
  costs nothing. Moving it to `in_flight` is a separate, deliberate act.
* ``blocked_on: []`` with ``blocked_on_basis: NOT_ASSESSED`` — an empty list here
  is **not** the claim that nothing blocks it. The work store's README is explicit
  that an invented edge is read by the constraint computation as a real blocker
  and that *a false blocker is worse than a missing one*, so capture asserts
  nothing it has not looked at.
* ``done_condition`` is REQUIRED. 51 of the 575 migrated rows carry none, and the
  store's own note says an object that cannot say what would end it can never be
  finished, only abandoned. Refusing here stops that population growing.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawn_gate  # noqa: E402

OBJECTS_DIR = spawn_gate.OBJECTS_DIR
INTENTS_DIR = spawn_gate.INTENTS_DIR
REPO_ROOT = spawn_gate.REPO_ROOT

STAGES = ("QUESTION", "EVIDENCE", "DECISION", "DEPLOYMENT", "OBSERVATION",
          "CAPABILITY", "INTEGRITY")


def slug(text: str, max_words: int = 7) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.upper())[:max_words]
    return "-".join(words) or "UNTITLED"


def object_id(title: str, today: Optional[str] = None) -> str:
    day = today or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    return f"WO-{day}-{slug(title)}"


def _yaml_block(text: str, indent: str = "  ") -> str:
    """Fold a value as a literal block, so a title containing `:` or `#` cannot
    produce a file that parses as something else."""
    body = "\n".join(indent + line for line in str(text).strip().splitlines())
    return ">-\n" + body


def render(oid: str, title: str, intent: str, done_when: str, stage: str,
           why: str, source: str, today: str) -> str:
    return f"""id: {oid}
type: question
parent_intent: {intent}
title: {_yaml_block(title)}
stage: {stage}
lifecycle: dormant
owner: null
opened_at: {today}
closed_at: null
review_trigger: >-
  Re-read at the next cycle. A captured idea is PARKED, not queued — it becomes
  work only when it is given an owner and moved out of `dormant`.
done_condition: {_yaml_block(done_when)}

why: {_yaml_block(why or "Captured without a stated rationale.")}

source: {_yaml_block(source)}

blocked_on: []
blocked_on_basis: >-
  NOT_ASSESSED. ⚠️ This empty list is NOT the claim that nothing blocks this
  object — nobody has looked. Write a TRUE edge before moving it out of
  `dormant`; an invented edge is read by the constraint computation as a real
  blocker, and a false blocker is worse than a missing one.

captured_by: scripts/ops/capture_idea.py
"""


def known_intents() -> Optional[List[str]]:
    try:
        return sorted(p.stem for p in INTENTS_DIR.glob("*.yaml"))
    except OSError:
        return None


def capture(title: str, intent: str, done_when: str, stage: str, why: str,
            source: str, objects_dir: Path = OBJECTS_DIR,
            today: Optional[str] = None) -> Tuple[Path, str]:
    day = today or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    oid = object_id(title, day.replace("-", ""))
    path = objects_dir / f"{oid}.yaml"
    n = 2
    while path.exists():                      # never clobber a real object
        path = objects_dir / f"{oid}-{n}.yaml"
        oid = path.stem
        n += 1
    path.write_text(render(oid, title, intent, done_when, stage, why, source, day),
                    encoding="utf-8")
    return path, oid


def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    import tempfile
    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r} want {want!r}")
        if not quiet:
            print(f"  self-test ({label}): "
                  f"{'PASS' if got == want else f'FAIL got={got!r} want={want!r}'}")

    check("the id is derived from the title and the date",
          object_id("Make the exits better", "20260902"),
          "WO-20260902-MAKE-THE-EXITS-BETTER")
    check("punctuation is stripped rather than smuggled into the id",
          object_id("A: b/c #1", "20260902"), "WO-20260902-A-B-C-1")
    check("an unnameable title still yields an id",
          object_id("!!!", "20260902"), "WO-20260902-UNTITLED")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        p1, o1 = capture("Idea one", "IN-X", "done when X", "QUESTION", "", "op",
                         objects_dir=d, today="2026-09-02")
        check("the file is written", p1.exists(), True)
        p2, o2 = capture("Idea one", "IN-X", "done when X", "QUESTION", "", "op",
                         objects_dir=d, today="2026-09-02")
        check("A SECOND CAPTURE OF THE SAME TITLE NEVER CLOBBERS THE FIRST",
              (p1 != p2, o2.endswith("-2")), (True, True))

        try:
            import yaml
            doc = yaml.safe_load(p1.read_text(encoding="utf-8"))
            check("the written object PARSES as yaml", isinstance(doc, dict), True)
            check("...and lands DORMANT, so capture can never consume a WIP slot",
                  doc["lifecycle"], "dormant")
            check("...carrying the intent it was told, never a guessed one",
                  doc["parent_intent"], "IN-X")
            check("...and a done_condition", bool(doc["done_condition"]), True)
            check("...and blocked_on that ADMITS it was not assessed",
                  "NOT_ASSESSED" in doc["blocked_on_basis"], True)
            # ⚠️ the gate must actually accept what capture produces, or the
            # 'cheap path' dead-ends at the very gate it exists to satisfy.
            v = spawn_gate.grade(o1, doc, True, {"IN-X"},
                                 {"current": {"intent_ref": "IN-X",
                                              "cycle_id": "CY-1"}}, True, None)
            check("A CAPTURED OBJECT PASSES THE SPAWN GATE — the two halves connect",
                  v["state"], spawn_gate.PERMITTED)
            v_off = spawn_gate.grade(o1, doc, True, {"IN-X", "IN-Y"},
                                     {"current": {"intent_ref": "IN-Y",
                                                  "cycle_id": "CY-2"}}, True, None)
            check("...and is REFUSED when its intent is not the cycle's, as designed",
                  v_off["state"], spawn_gate.REFUSED)
        except ImportError:
            check("PyYAML absent — parse assertions SKIPPED, not silently passed",
                  "skipped", "skipped")

        title_hostile = "Fix: the thing # that broke\nand a second line"
        p3, _ = capture(title_hostile, "IN-X", "d", "QUESTION", "", "op",
                        objects_dir=d, today="2026-09-02")
        try:
            import yaml
            doc3 = yaml.safe_load(p3.read_text(encoding="utf-8"))
            check("A TITLE WITH `:` `#` AND A NEWLINE STILL PARSES",
                  isinstance(doc3, dict) and doc3["id"].startswith("WO-"), True)
        except ImportError:
            pass

    if not quiet:
        print("capture-idea self-test:", "PASS" if not failures else "FAIL")
    return (not failures), failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--title", help="one line: what the idea IS")
    ap.add_argument("--intent", help="the intent it serves. REQUIRED and never "
                                     "defaulted — see the module docstring.")
    ap.add_argument("--done-when", help="what must be true for this to be finished")
    ap.add_argument("--stage", default="QUESTION", choices=STAGES)
    ap.add_argument("--why", default="", help="optional: why it matters")
    ap.add_argument("--source", default="operator (conversational)",
                    help="where the idea came from")
    a = ap.parse_args(argv)
    if a.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1
    ok, failures = _self_test(quiet=True)
    if not ok:
        print(f"capture-idea: REFUSING — the planted-failure suite did not pass "
              f"({len(failures)}): {'; '.join(failures[:3])}")
        return 4

    missing = [f"--{n}" for n, v in (("title", a.title), ("intent", a.intent),
                                     ("done-when", a.done_when)) if not v]
    if missing:
        print(f"capture-idea: need {', '.join(missing)}.")
        if "--intent" in missing:
            known = known_intents()
            print(f"capture-idea: intents on disk: {known if known is not None else 'UNREADABLE'}")
            print("capture-idea: ⚠️ --intent is NOT defaulted to the cycle's, "
                  "deliberately. Defaulting it would make every captured idea pass "
                  "the priority gate by construction — the gate would compare a "
                  "value against itself and permit everything while looking like "
                  "enforcement. Naming the intent honestly is the judgement this "
                  "tool refuses to make for you.")
        return 2

    known = known_intents()
    if known is not None and a.intent not in known:
        print(f"capture-idea: `{a.intent}` is not an intent on disk ({known}). "
              f"Free text is not an intent — either name one of these, or author a "
              f"new intent file first. 'This belongs to nothing we have committed "
              f"to' is a real finding, not a formatting problem.")
        return 3

    path, oid = capture(a.title, a.intent, a.done_when, a.stage, a.why, a.source)
    rel = path.relative_to(REPO_ROOT)
    print(f"capture-idea: wrote {rel}")
    print(f"capture-idea: id = {oid}  (lifecycle: dormant — PARKED, not started, so "
          f"it consumes no WIP slot)")
    print()
    print("Tell the operator where it landed, then carry on with the cycle priority:")
    print(f"  \"Captured as {oid} under {a.intent}; it is parked, not started.\"")
    print()
    print("If it genuinely comes FIRST, spawn against it:")
    print(f"  python3 scripts/ops/session_registry.py register --owns-object {oid} \\")
    print("      --title \"...\" --why \"...\" --spawned-by \"$CLAUDE_SESSION_ID\"")
    print("…and if its intent is not the cycle's, the spawn gate will refuse until "
          "an approved exception names it. That refusal is the point.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
