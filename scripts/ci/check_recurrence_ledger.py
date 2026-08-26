#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (recurrence-ledger-guard)
"""Every repeated mistake CLASS must name an executable prevention, or say why none exists.

WHY THIS EXISTS
---------------
Operator, 2026-08-26: *"it doesn't address the broken mechanism for logging
mistakes and then reviewing them to come up with actual structural fixes during
review sessions, or actually preventing new sessions from making the same
mistakes twice."*

The backlogs record INSTANCES and close when each instance is fixed. Nothing in
them asks *"has this shape happened before, and what stops the next one?"* — so
the same class recurs under a new id indefinitely. Measured 2026-08-26: 1,164
backlog rows across three files, and the phrase that would end a class — *this
is the Nth time, here is the check that prevents the N+1th* — appears nowhere.

This guard is the missing half. It does not care how many rows exist; it cares
that a class which has happened **more than once** has an answer to *what stops
it*.

WHAT COUNTS AS A PREVENTION
---------------------------
An **executable** check: a CI guard, a test, a refusal inside a tool. The test
is mechanical — *if a future session makes this mistake, does something FAIL
before a human notices?* Prose does not qualify, and the guard rejects the
common prose forms outright, because "be more careful" is how a lesson gets
recorded and not learned.

`prevention: null` is allowed ONLY with `unpreventable_because` stating why no
check can catch it. That is a real category — some classes are judgement, not
mechanism — and forcing a fake prevention onto one would be worse than
admitting it. But it must be SAID, not left blank.

⚠️ **This guard cannot tell whether a prevention WORKS.** It checks that one is
named and that it is not prose. A named guard that never fires is the
`new-table-wiring-guard` failure — cheaper to lie to than to satisfy — so the
named prevention must also resolve to a file that exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_LEDGER = Path("docs/claude/RECURRENCE-LEDGER.json")

#: Phrases that describe intent rather than a mechanism. A prevention reading
#: like one of these is the exact non-fix this guard exists to reject.
_PROSE = (
    "be careful", "be more careful", "remember to", "make sure to", "should check",
    "always verify", "read the docs", "pay attention", "double check", "take care",
    "keep in mind", "don't forget", "review carefully", "more diligence",
)


def check(path: Path = _LEDGER) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"{path} is MISSING — the repeated-mistake register is how a class "
                f"stops recurring; without it every instance is filed fresh forever."]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"{path} did not parse: {exc}"]

    classes = data.get("classes")
    if not isinstance(classes, list):
        return [f"{path}: 'classes' is not a list"]

    seen: set[str] = set()
    for i, c in enumerate(classes):
        if not isinstance(c, dict):
            problems.append(f"classes[{i}] is not an object")
            continue
        cid = str(c.get("id") or f"<no id, index {i}>")
        if cid in seen:
            problems.append(f"{cid}: duplicate id")
        seen.add(cid)

        for f in ("title", "first_seen", "last_seen"):
            if not str(c.get(f) or "").strip():
                problems.append(f"{cid}: missing '{f}'")

        try:
            n = int(c.get("occurrences"))
        except (TypeError, ValueError):
            problems.append(f"{cid}: 'occurrences' must be a number — a class with an "
                            f"uncounted recurrence rate cannot be prioritised")
            n = 0

        ev = c.get("evidence")
        if not isinstance(ev, list) or len(ev) < 2:
            problems.append(f"{cid}: needs >= 2 'evidence' entries. ONE instance is not a "
                            f"class, and a class asserted without its instances cannot be "
                            f"checked by the next reader")
        elif n and len(ev) > n:
            problems.append(f"{cid}: {len(ev)} evidence entries but occurrences={n} — the "
                            f"count understates its own evidence")

        prev = c.get("prevention")
        why = c.get("unpreventable_because")
        if prev is None:
            if not str(why or "").strip():
                problems.append(
                    f"{cid}: no 'prevention' and no 'unpreventable_because'. This is a "
                    f"mistake that has happened {n or '?'} times with nothing stopping the "
                    f"next one — which is the exact state this register exists to make "
                    f"unignorable. Name an EXECUTABLE check (guard/test/tool refusal), or "
                    f"state honestly why none can exist.")
        else:
            text = str(prev)
            low = text.lower()
            hit = next((p for p in _PROSE if p in low), None)
            if hit:
                problems.append(
                    f"{cid}: prevention reads as intent, not mechanism (matched '{hit}'). "
                    f"A prevention must FAIL when the mistake recurs; 'be careful' is how a "
                    f"lesson gets recorded and never learned.")
            named = [t.strip(" `'\",()") for t in text.split()
                     if t.strip(" `'\",()").endswith((".py", ".sh"))]
            if named:
                missing = [t for t in named if not Path(t).exists()]
                if missing:
                    problems.append(
                        f"{cid}: prevention names {missing} which does/do not exist. A guard "
                        f"that cannot be found is cheaper to claim than to build — the "
                        f"new-table-wiring-guard failure.")
            elif len(text) < 25:
                problems.append(f"{cid}: prevention is too vague to check: {text!r}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=str(_LEDGER))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    problems = check(Path(a.path))
    if problems:
        print("::error::docs/claude/RECURRENCE-LEDGER.json — a repeated mistake with no "
              "prevention is a lesson nobody learned:")
        for p in problems:
            print(f"  - {p}")
        return 1
    data = json.loads(Path(a.path).read_text(encoding="utf-8"))
    cs = data["classes"]
    unp = sum(1 for c in cs if not c.get("prevention"))
    print(f"recurrence-ledger-guard: OK — {len(cs)} class(es), {unp} awaiting a prevention "
          f"(each rendered into CLAUDE.md so every session sees it).")
    return 0


def _self_test() -> int:
    import tempfile
    ok = True
    good = {"id": "RC-X", "title": "t", "first_seen": "2026-01-01", "last_seen": "2026-02-01",
            "occurrences": 2, "evidence": ["a", "b"],
            "prevention": "scripts/ci/check_recurrence_ledger.py rejects it"}
    with tempfile.TemporaryDirectory() as d:
        def run(classes):
            p = Path(d) / "r.json"
            p.write_text(json.dumps({"schema_version": 1, "classes": classes}))
            return check(p)
        cases = [
            ("a class with a real prevention passes", run([good]), False),
            ("no prevention and no reason is a finding",
             run([{**good, "prevention": None}]), True),
            ("no prevention WITH an honest reason passes",
             run([{**good, "prevention": None,
                   "unpreventable_because": "it is a judgement call, not a mechanism"}]), False),
            ("prose prevention is a finding",
             run([{**good, "prevention": "be more careful when reading fields"}]), True),
            ("a prevention naming a file that does not exist is a finding",
             run([{**good, "prevention": "scripts/ci/does_not_exist.py catches it"}]), True),
            ("one evidence entry is not a class",
             run([{**good, "evidence": ["only one"]}]), True),
            ("evidence exceeding the stated count is a finding",
             run([{**good, "occurrences": 1, "evidence": ["a", "b", "c"]}]), True),
            ("a missing ledger is a finding", check(Path(d) / "nope.json"), True),
        ]
        for label, got, want in cases:
            good_ = bool(got) == want
            ok &= good_
            print(f"  self-test ({label}): {'PASS' if good_ else 'FAIL'}"
                  + ("" if good_ else f" -- {got}"))
    print("recurrence-ledger-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
