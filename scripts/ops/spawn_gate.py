#!/usr/bin/env python3
"""SPAWN PRIORITY GATE — does this spawn serve the cycle's declared priority?

WHY A GATE AND NOT A REMINDER
-----------------------------
Sort every mechanism in this repo by **who invokes it**, and 2026-09-02 sorts
cleanly:

    refuses at a gate  ── check_backlog_criteria · open-items-guard ·
                          backlog_append.detect_format · run_guards ·
                          check_wip_ceiling            ── used EVERY time.
    must be remembered ── SESSIONS.json needs_action · CYCLE-PRIORITY.json ·
                          send-ping · handoff_check.py ── used NOT ONCE.

**Every mechanism the manager had to choose to run went unused; every mechanism
that stood in the way worked.** That is a property of where a mechanism sits, not
a discipline problem a better prompt fixes. So this refuses, at the one place a
sub-session comes into existence.

WHAT IT COST TO HAVE NO GATE, measured
---------------------------------------
The declared priority is rendered verbatim into `CLAUDE.md`'s session brief and
says *"if you are about to start something that is neither the current phase nor
pulled by a held-up stage, that is the thing to re-argue before starting it."*
It was never consulted once in a full day. Spend by what drove it:

    a conversational aside      MI-58 + MI-60 + MI-59      $193.04
    the declared priority       MI-63                       $16.19

**12:1 against the declared priority**, while MI-70 — an explicit handoff
blocker — sat unowned for a day. `OI-20260901-CYCLE-PRIORITY-IS-RENDERED-BUT-NO-SESSION-HAS-ACTED-ON-IT`
is open precisely because nobody acts on the rendered priority.

⚠️ THIS IS NOT A VETO ON THE OPERATOR
--------------------------------------
The operator's own framing: *"the idea isn't that there's too many things.
There's too many things and you're not focused."* An operator saying **do this
now** must still work, immediately. What this stops is a MANAGER silently
re-prioritising the fleet onto a passing remark. The escape is
`--exception`-backed and cheap, and `scripts/ops/capture_idea.py` makes the
capture path *cheaper than the build path* — which matters, because if turning
an idea into a work object costs more than dispatching a session, a manager
under pressure dispatches, and the gate converts a focus problem into a logging
problem with the idea lost entirely. That would be worse than no gate.

WHAT IT REFUSES ON
------------------
 1. no `--owns-object` at all — the orphan-task rule, the structure's first rule
 2. an object id with no file — a parent that does not exist is not a parent
 3. an object naming no `parent_intent`, or one with no intent file
 4. an intent that is not the cycle's, with no approved exception

⚠️ RULE 4 IS THE ONLY ONE AN EXCEPTION CAN CLEAR. The first three are structural
and no justification makes an orphan not an orphan.

THE EXCEPTION IS `wip-ceiling-exception.yaml`'s SHAPE, DELIBERATELY
-------------------------------------------------------------------
Same file discipline, same vocabulary, same failure modes — `decision: pending`
**still refuses** (filed is not granted), `approved` passes only for the object
ids it NAMES, and an approval with no `approved_by`/`approved_at` is a session
approving itself. Copying a mechanism the repo already runs beats inventing a
second one that drifts.

THREE STATES, NEVER COLLAPSED
-----------------------------
``permitted``  the spawn serves the cycle, or an approved exception covers it.
``refused``    a named, quotable reason. The refusal SAYS WHAT IT DISPLACES —
               a refusal that only says "no" teaches nothing and gets routed
               around.
``unknown``    WE COULD NOT LOOK (an unreadable priority file, an unlistable
               intents dir). ⚠️ It does **not** block, because a gate that fails
               closed on its own unreadable config would halt all spawning on a
               typo — but it is reported loudly and is never a `permitted`.

⚠️ AND THE HONEST LIMIT, MEASURED RATHER THAN ASSUMED
------------------------------------------------------
**A gate on `register` binds only spawns that go through `register`.** Measured
2026-09-02T13:55Z against one `list_sessions` page (60 rows, `mine=true` — ⚠️ A
PAGE IS NOT A POPULATION): **5 of 60 observed sessions are absent from
SESSIONS.json — 8.3% — and 4 of those 5 were RUNNING**, every one carrying the
manager's own id as parent. So the chokepoint is real but not total: it binds
about 92% of spawns today and is routed around by writing a `create_session`
prompt by hand. This is a large improvement on the 26-of-55 (47%) once recorded
in `docs/claude/work/README.md`, and it is still a hole. The repo does not own
the spawn — `create_session` is an MCP tool with no interposition point — so no
code here can close it; `session-registry-guard` and `handoff_check` are the two
detectors that catch the bypass after the fact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session_registry as sr  # noqa: E402

REPO_ROOT = sr.REPO_ROOT
WORK_DIR = REPO_ROOT / "docs" / "claude" / "work"
OBJECTS_DIR = WORK_DIR / "objects"
INTENTS_DIR = WORK_DIR / "intents"
PRIORITY_PATH = REPO_ROOT / "docs" / "claude" / "CYCLE-PRIORITY.json"
EXCEPTION_PATH = WORK_DIR / "spawn-priority-exception.yaml"

PERMITTED, REFUSED, UNKNOWN = "permitted", "refused", "unknown"


def _v(state: str, reason: str, **extra: Any) -> Dict[str, Any]:
    return dict(state=state, reason=reason, **extra)


def _read_yaml(path: Path) -> Tuple[Optional[Any], bool]:
    try:
        import yaml
    except ImportError:
        return None, False
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), True
    except FileNotFoundError:
        return None, True          # absent is a real reading
    except (OSError, yaml.YAMLError):
        return None, False         # we could not look


def cycle_intent(priority_doc: Optional[Any]) -> Tuple[Optional[str], Optional[str]]:
    """(intent_ref, cycle_id) from CYCLE-PRIORITY.json's `current`."""
    cur = (priority_doc or {}).get("current") if isinstance(priority_doc, dict) else None
    if not isinstance(cur, dict):
        return None, None
    ref = cur.get("intent_ref")
    return (ref.strip() if isinstance(ref, str) and ref.strip() else None,
            cur.get("cycle_id"))


def exception_covers(exc: Optional[Any], object_id: str
                     ) -> Tuple[bool, str]:
    """`wip-ceiling-exception.yaml`'s rules, applied to a spawn.

    Deliberately the same vocabulary and the same failure modes, so a reader who
    knows one knows the other and neither drifts.
    """
    if not isinstance(exc, dict):
        return False, "no exception file"
    decision = str(exc.get("decision") or "").strip().lower()
    covers = [str(c) for c in (exc.get("covers") or []) if c]
    if decision == "refused":
        return False, "the exception was REFUSED by the operator"
    if decision == "pending":
        return False, ("the exception is FILED but not GRANTED (`decision: pending`). "
                       "Filing is not approval — the whole point of the gate is that "
                       "displacing the cycle priority needs a human to say yes.")
    if decision != "approved":
        return False, (f"the exception carries `decision: {decision!r}`, which is not "
                       f"one of pending/approved/refused.")
    if object_id not in covers:
        return False, (f"the approved exception names {covers or '[]'} and does NOT "
                       f"name `{object_id}`. A blanket exception naming nothing is a "
                       f"permanent cap-raise wearing an exception's clothes.")
    if not exc.get("approved_by") or not exc.get("approved_at"):
        return False, ("the exception says `decision: approved` but carries no "
                       "`approved_by` / `approved_at`. An approval with nobody's name "
                       "on it is a session approving itself.")
    return True, (f"approved by {exc.get('approved_by')} on {exc.get('approved_at')}, "
                  f"naming `{object_id}`")


def grade(object_id: Optional[str], object_doc: Optional[Any], object_readable: bool,
          intent_ids: Optional[set], priority_doc: Optional[Any],
          priority_readable: bool, exc: Optional[Any]) -> Dict[str, Any]:
    """PURE, so the policy is arguable in tests rather than against a live spawn."""
    # 1 · the orphan-task rule
    if not object_id:
        return _v(REFUSED,
                  "this spawn names NO work object. The structure's first rule is "
                  "that nothing is worked that has no parent — a step has a MANDATORY "
                  "parent, and without one the work is an orphan no successor can "
                  "situate and the constraint readout cannot see. Pass "
                  "--owns-object <WO-id>; if no object exists yet, "
                  "`python3 scripts/ops/capture_idea.py` makes one in one command.")
    # 2 · the parent must exist
    if not object_readable:
        return _v(UNKNOWN,
                  f"`{object_id}` could not be read (unparseable, or PyYAML missing), "
                  f"so its intent could not be established. WE COULD NOT LOOK — this "
                  f"is not permission.")
    if object_doc is None:
        return _v(REFUSED,
                  f"`{object_id}` names no file under docs/claude/work/objects/. A "
                  f"parent that does not exist is not a parent. Author it first — "
                  f"`capture_idea.py` does it in one command.")
    # 3 · the parent must sit under an intent
    parent_intent = (object_doc.get("parent_intent")
                     if isinstance(object_doc, dict) else None)
    if not isinstance(parent_intent, str) or not parent_intent.strip():
        return _v(REFUSED,
                  f"`{object_id}` declares no `parent_intent`. The roadmap is two "
                  f"layers and an object with no intent is unattached — a condition "
                  f"the design says must be SEEN rather than accumulate silently.")
    parent_intent = parent_intent.strip()
    if intent_ids is None:
        return _v(UNKNOWN,
                  f"docs/claude/work/intents/ could not be listed, so `{parent_intent}` "
                  f"could not be resolved. WE COULD NOT LOOK.")
    if parent_intent not in intent_ids:
        return _v(REFUSED,
                  f"`{object_id}` names parent intent `{parent_intent}`, which has no "
                  f"file under docs/claude/work/intents/ (known: "
                  f"{sorted(intent_ids) or '[]'}). Free text is not an intent.")
    # 4 · …and that intent should be the cycle's
    if not priority_readable:
        return _v(UNKNOWN,
                  "CYCLE-PRIORITY.json could not be read, so the cycle's intent is "
                  "unknown and this spawn was NOT graded against it. WE COULD NOT "
                  "LOOK — proceeding, but ungraded.")
    want, cycle_id = cycle_intent(priority_doc)
    if want is None:
        return _v(UNKNOWN,
                  "CYCLE-PRIORITY.json declares no `current.intent_ref`, so there is "
                  "no cycle intent to grade against. ⚠️ NO PRIORITY SET and GATE "
                  "BROKEN must never render identically — this is the former, and it "
                  "is not permission, it is an ungraded spawn.")
    if parent_intent == want:
        return _v(PERMITTED,
                  f"`{object_id}` sits under `{parent_intent}`, which IS the cycle's "
                  f"declared intent ({cycle_id}).", intent=parent_intent,
                  cycle_id=cycle_id)
    covered, why = exception_covers(exc, object_id)
    if covered:
        return _v(PERMITTED,
                  f"`{object_id}` sits under `{parent_intent}`, NOT the cycle's "
                  f"`{want}` — permitted by an approved exception ({why}). Recorded, "
                  f"not silent.", intent=parent_intent, cycle_id=cycle_id,
                  exception=True)
    return _v(REFUSED,
              f"THIS SPAWN DISPLACES THE CYCLE PRIORITY.\n"
              f"    it serves : {parent_intent}\n"
              f"    the cycle : {want}  ({cycle_id})\n"
              f"    exception : {why}\n"
              f"  The priority is not a suggestion you are being reminded of — it was "
              f"set by the operator and is rendered into CLAUDE.md's session brief, "
              f"and it was consulted ZERO times in the day this gate was built, at a "
              f"measured 12:1 spend against it. If this work genuinely comes first, "
              f"that is a real and legitimate decision — make it VISIBLE: file "
              f"{EXCEPTION_PATH.relative_to(REPO_ROOT)} with `decision: approved`, "
              f"`covers: [{object_id}]`, `approved_by`, `approved_at` and the reason. "
              f"If the operator asked for it directly, say so there and it takes 30 "
              f"seconds.",
              intent=parent_intent, want=want, cycle_id=cycle_id)


def known_intent_ids() -> Optional[set]:
    try:
        return {p.stem for p in INTENTS_DIR.glob("*.yaml")}
    except OSError:
        return None


def grade_spawn(object_id: Optional[str]) -> Dict[str, Any]:
    """Read every input from disk and grade. The I/O half, kept out of `grade`."""
    obj_doc, obj_ok = (None, True)
    if object_id:
        obj_doc, obj_ok = _read_yaml(OBJECTS_DIR / f"{object_id}.yaml")
    prio, prio_ok = sr.read_json(PRIORITY_PATH)
    exc, _ = _read_yaml(EXCEPTION_PATH)
    return grade(object_id, obj_doc, obj_ok, known_intent_ids(), prio, prio_ok, exc)


_EXIT = {PERMITTED: 0, REFUSED: 3, UNKNOWN: 0}


def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r} want {want!r}")
        if not quiet:
            print(f"  self-test ({label}): "
                  f"{'PASS' if got == want else f'FAIL got={got!r} want={want!r}'}")

    INT = {"IN-CYCLE", "IN-OTHER"}
    PRIO = {"current": {"intent_ref": "IN-CYCLE", "cycle_id": "CY-1"}}
    in_cycle = {"parent_intent": "IN-CYCLE"}
    off_cycle = {"parent_intent": "IN-OTHER"}
    ok_exc = {"decision": "approved", "covers": ["WO-X"],
              "approved_by": "operator", "approved_at": "2026-09-02"}

    # ⚠️ THE NEGATIVE CONTROL IN BOTH DIRECTIONS. One without the other proves
    # nothing: a gate that refuses everything and a gate that permits everything
    # are both "consistent".
    check("AN IN-PRIORITY SPAWN STILL SUCCEEDS (the gate is not a wall)",
          grade("WO-X", in_cycle, True, INT, PRIO, True, None)["state"], PERMITTED)
    check("AN OUT-OF-PRIORITY SPAWN WITH NO EXCEPTION REFUSES",
          grade("WO-X", off_cycle, True, INT, PRIO, True, None)["state"], REFUSED)

    # the refusal must teach
    r = grade("WO-X", off_cycle, True, INT, PRIO, True, None)
    check("...and the refusal NAMES WHAT IT DISPLACES",
          all(t in r["reason"] for t in ("IN-OTHER", "IN-CYCLE", "CY-1")), True)
    check("...and names the escape hatch", "spawn-priority-exception" in r["reason"], True)

    # the orphan rule
    check("NO OBJECT AT ALL REFUSES — the structure's first rule",
          grade(None, None, True, INT, PRIO, True, None)["state"], REFUSED)
    check("an object id with NO FILE refuses",
          grade("WO-GHOST", None, True, INT, PRIO, True, None)["state"], REFUSED)
    check("an object with NO parent_intent refuses",
          grade("WO-X", {"title": "t"}, True, INT, PRIO, True, None)["state"], REFUSED)
    check("a parent_intent that is FREE TEXT, not a real intent, refuses",
          grade("WO-X", {"parent_intent": "make money"}, True, INT, PRIO, True,
                None)["state"], REFUSED)

    # the exception, with wip-ceiling's exact rules
    check("an APPROVED exception naming this object PERMITS",
          grade("WO-X", off_cycle, True, INT, PRIO, True, ok_exc)["state"], PERMITTED)
    check("`decision: pending` STILL REFUSES — filed is not granted",
          grade("WO-X", off_cycle, True, INT, PRIO, True,
                dict(ok_exc, decision="pending"))["state"], REFUSED)
    check("`decision: refused` refuses",
          grade("WO-X", off_cycle, True, INT, PRIO, True,
                dict(ok_exc, decision="refused"))["state"], REFUSED)
    check("AN EXCEPTION NAMING A DIFFERENT OBJECT DOES NOT COVER THIS ONE",
          grade("WO-X", off_cycle, True, INT, PRIO, True,
                dict(ok_exc, covers=["WO-OTHER"]))["state"], REFUSED)
    check("an exception naming NOTHING is a blanket cap-raise and refuses",
          grade("WO-X", off_cycle, True, INT, PRIO, True,
                dict(ok_exc, covers=[]))["state"], REFUSED)
    check("an approval with NOBODY'S NAME on it refuses",
          grade("WO-X", off_cycle, True, INT, PRIO, True,
                {k: v for k, v in ok_exc.items() if k != "approved_by"})["state"],
          REFUSED)

    # unknown, and the deliberate choice not to block on it
    check("an UNREADABLE object is `unknown`, never permitted",
          grade("WO-X", None, False, INT, PRIO, True, None)["state"], UNKNOWN)
    check("an unlistable intents dir is `unknown`",
          grade("WO-X", in_cycle, True, None, PRIO, True, None)["state"], UNKNOWN)
    check("an UNREADABLE priority file is `unknown`, never permitted",
          grade("WO-X", in_cycle, True, INT, None, False, None)["state"], UNKNOWN)
    check("NO PRIORITY SET is `unknown`, and must not render as permission",
          grade("WO-X", in_cycle, True, INT, {"current": {}}, True, None)["state"],
          UNKNOWN)
    check("...and `unknown` does NOT block a spawn (a typo must not halt the fleet)",
          _EXIT[UNKNOWN], 0)
    check("...while `refused` DOES block", _EXIT[REFUSED], 3)

    check("cycle_intent reads `intent_ref`, the key the live file actually uses",
          cycle_intent(PRIO), ("IN-CYCLE", "CY-1"))
    check("a priority doc with no `current` yields no intent",
          cycle_intent({}), (None, None))

    if not quiet:
        print("spawn-gate self-test:", "PASS" if not failures else "FAIL")
    return (not failures), failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--owns-object", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1
    ok, failures = _self_test(quiet=True)
    if not ok:
        print(f"spawn-gate: REFUSING to grade — the planted-failure suite did not "
              f"pass ({len(failures)}): {'; '.join(failures[:3])}")
        return 4
    v = grade_spawn(a.owns_object)
    print(f"spawn-gate: [{v['state'].upper()}] {v['reason']}")
    if a.json:
        print(json.dumps(v, indent=2, ensure_ascii=False))
    return _EXIT[v["state"]]


if __name__ == "__main__":
    raise SystemExit(main())
