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


# --------------------------------------------------------------------------- #
# which headline a finding travels under
# --------------------------------------------------------------------------- #
#: This guard tests THREE different things and, until 2026-09-17, printed one
#: headline over all of them: *"a repeated mistake with no prevention is a
#: lesson nobody learned"*. REPRODUCED, not constructed — a ledger in which
#: EVERY class names an executable prevention, whose only fault is a duplicate
#: id:
#:
#:     ::error::docs/claude/RECURRENCE-LEDGER.json — a repeated mistake with no
#:       prevention is a lesson nobody learned:
#:       - RC-SAME: duplicate id
#:
#: Nothing about preventions was established on that run. UNPROVENANCED
#: DIAGNOSTIC OUTPUT sub-class A — a message naming a cause no code path
#: tested. **9 of the 13 rules below are not about preventions at all**, and
#: the worst is `UNREADABLE`: the file could not be read, so the run learned
#: nothing whatsoever, and said a class had no prevention.
#:
#: ⚠️ THE HARM IS THE REMEDY. `NO_PREVENTION` says *build a guard*; `MALFORMED`
#: says *fix the row's fields*; `UNREADABLE` says *restore the file — nothing
#: was checked*. One headline sent all three readers to build a guard.
UNREADABLE, MALFORMED, NO_PREVENTION = "unreadable", "malformed", "no_prevention"

#: ⚠️ THE PATH IS A PARAMETER IN EVERY HEADLINE, AND THAT IS A SECOND DEFECT
#: FIXED HERE. The old line hardcoded `docs/claude/RECURRENCE-LEDGER.json`
#: while `--path` pointed elsewhere — it named a file it had not read, in the
#: same sentence that named a cause it had not tested.
def _headlines(path: Path) -> dict[str, str]:
    return {
        UNREADABLE: (
            f"::error::{path} could NOT BE READ, so NOTHING was checked — this "
            f"is 'we did not look', not 'no class is missing a prevention'. "
            f"Restore or repair the file; the repeated-mistake register is how "
            f"a class stops recurring, and a register nobody can read prevents "
            f"nothing at all."),
        MALFORMED: (
            f"::error::{path}: a class ROW is malformed, so it cannot be "
            f"graded. ⚠️ NOTHING here says a prevention is missing — the "
            f"remedy is to fix the row's fields, NOT to go and build a guard."),
        NO_PREVENTION: (
            f"::error::{path} — a repeated mistake with no prevention is a "
            f"lesson nobody learned. Name an EXECUTABLE check (guard / test / "
            f"tool refusal) that FAILS when the mistake recurs, or state "
            f"honestly in 'unpreventable_because' why none can exist."),
    }


#: Fixed print order, so a run tripping several is stable and diffable.
_ORDER = (UNREADABLE, MALFORMED, NO_PREVENTION)


def group_by_headline(problems: list[tuple[str, str]],
                      path: Path) -> list[tuple[str, list[str]]]:
    """`[(headline, [message, …]), …]` for the causes that actually fired.

    ⚠️ A CAUSE THAT FOUND NOTHING CONTRIBUTES NO HEADLINE — a heading
    asserting a cause nothing established is the mirror of the defect above.

    ⚠️ AN UNKNOWN TAG IS A HARD ERROR, NEVER A SILENT FALLBACK. A rule added
    later whose findings quietly inherited a neighbouring cause is exactly how
    this defect arose; failing loudly makes adding a rule cost one line.

    ⚠️ THIS IS DELIBERATELY A SECOND COPY OF THE SHAPE `check_register_ids.py`
    GAINED THE SAME DAY, AND THE ARGUMENT IS RECORDED RATHER THAN ASSUMED.
    Extracting it was the obvious move and the repo's own precedent argues
    against it at this size: `scripts/ci/_git_base.py` — the ONE shared helper
    under `scripts/ci/` — was extracted off a MEASURED population of 12 scripts
    of which 5 were wrong. Here the measured population is TWO call sites with
    no observed divergence, so a shared module would be justified by symmetry
    rather than by evidence. **Extract it when a THIRD consumer appears, or the
    moment the two behave differently** — both are cheap to notice because each
    copy carries its own controls. Population behind that two: all 47 files
    under `scripts/ci/` + `scripts/ops/` that print an `::error::` headline, of
    which 14 print exactly one; those 14 read by hand.
    """
    buckets: dict[str, list[str]] = {}
    heads = _headlines(path)
    for tag, message in problems:
        if tag not in heads:
            raise KeyError(
                f"rule tag {tag!r} has no headline. Add one rather than letting "
                f"its findings inherit another rule's cause.")
        buckets.setdefault(tag, []).append(message)
    return [(heads[t], buckets[t]) for t in _ORDER if t in buckets]


def check(path: Path = _LEDGER) -> list[tuple[str, str]]:
    """Return `(tag, message)` findings — see `group_by_headline`.

    ⚠️ **THE TAG IS PART OF THE FINDING, NOT DECORATION.** Until 2026-09-17
    these were bare strings and every one printed under the prevention
    headline, including the three below where the file was never read.
    """
    problems: list[tuple[str, str]] = []
    if not path.is_file():
        return [(UNREADABLE,
                 f"{path} is MISSING — the repeated-mistake register is how a class "
                 f"stops recurring; without it every instance is filed fresh forever.")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [(UNREADABLE, f"{path} did not parse: {exc}")]

    classes = data.get("classes")
    if not isinstance(classes, list):
        return [(UNREADABLE, f"{path}: 'classes' is not a list")]

    seen: set[str] = set()
    for i, c in enumerate(classes):
        if not isinstance(c, dict):
            problems.append((MALFORMED, f"classes[{i}] is not an object"))
            continue
        cid = str(c.get("id") or f"<no id, index {i}>")
        if cid in seen:
            problems.append((MALFORMED, f"{cid}: duplicate id"))
        seen.add(cid)

        for f in ("title", "first_seen", "last_seen"):
            if not str(c.get(f) or "").strip():
                problems.append((MALFORMED, f"{cid}: missing '{f}'"))

        try:
            n = int(c.get("occurrences"))
        except (TypeError, ValueError):
            problems.append((MALFORMED,
                             f"{cid}: 'occurrences' must be a number — a class with an "
                             f"uncounted recurrence rate cannot be prioritised"))
            n = 0

        ev = c.get("evidence")
        if not isinstance(ev, list) or len(ev) < 2:
            problems.append((MALFORMED,
                             f"{cid}: needs >= 2 'evidence' entries. ONE instance is not a "
                             f"class, and a class asserted without its instances cannot be "
                             f"checked by the next reader"))
        elif n and len(ev) > n:
            problems.append((MALFORMED,
                             f"{cid}: {len(ev)} evidence entries but occurrences={n} — the "
                             f"count understates its own evidence"))

        prev = c.get("prevention")
        why = c.get("unpreventable_because")
        if prev is None:
            if not str(why or "").strip():
                problems.append((NO_PREVENTION,
                    f"{cid}: no 'prevention' and no 'unpreventable_because'. This is a "
                    f"mistake that has happened {n or '?'} times with nothing stopping the "
                    f"next one — which is the exact state this register exists to make "
                    f"unignorable. Name an EXECUTABLE check (guard/test/tool refusal), or "
                    f"state honestly why none can exist."))
        else:
            text = str(prev)
            low = text.lower()
            hit = next((p for p in _PROSE if p in low), None)
            if hit:
                problems.append((NO_PREVENTION,
                    f"{cid}: prevention reads as intent, not mechanism (matched '{hit}'). "
                    f"A prevention must FAIL when the mistake recurs; 'be careful' is how a "
                    f"lesson gets recorded and never learned."))
            named = [t.strip(" `'\",()") for t in text.split()
                     if t.strip(" `'\",()").endswith((".py", ".sh"))]
            if named:
                missing = [t for t in named if not Path(t).exists()]
                if missing:
                    problems.append((NO_PREVENTION,
                        f"{cid}: prevention names {missing} which does/do not exist. A guard "
                        f"that cannot be found is cheaper to claim than to build — the "
                        f"new-table-wiring-guard failure."))
            elif len(text) < 25:
                problems.append((NO_PREVENTION,
                                 f"{cid}: prevention is too vague to check: {text!r}"))
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
        for headline, messages in group_by_headline(problems, Path(a.path)):
            print(headline)
            for message in messages:
                print(f"  - {message}")
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
        # --- WHICH CAUSE each finding is filed under ----------------------
        # The rules above prove a finding is MADE; these prove it is filed
        # under the cause that was actually tested, which is the whole defect.
        def tags(classes):
            return {t for t, _ in run(classes)}

        dup = tags([good, dict(good)])          # every class HAS a prevention
        cases += [
            ("tag: a duplicate id is MALFORMED, not a missing prevention",
             dup == {MALFORMED}, True),
            ("tag: a missing ledger is UNREADABLE — nothing was checked, so "
             "nothing can be said about preventions",
             {t for t, _ in check(Path(d) / "nope.json")} == {UNREADABLE}, True),
            ("tag: a prose prevention IS a missing prevention "
             "(the positive control)",
             tags([{**good, "prevention": "be more careful when reading fields"}])
             == {NO_PREVENTION}, True),
            ("tag: an undated, uncounted row is MALFORMED",
             tags([{**good, "occurrences": "many"}]) == {MALFORMED}, True),
        ]

        # --- the grouping ------------------------------------------------
        here = Path(d) / "r.json"
        only_mal = group_by_headline([(MALFORMED, "m")], here)
        cases += [
            ("headline: a MALFORMED-only run does NOT print the prevention "
             "headline",
             any("no prevention" in h for h, _ in only_mal), False),
            ("headline: ...and it DOES print the malformed one",
             any("class ROW is malformed" in h for h, _ in only_mal), True),
            # THE POSITIVE CONTROL. Without it, deleting the prevention
            # headline entirely would satisfy every negative assertion while
            # destroying the rule this guard exists for.
            ("headline: a NO_PREVENTION run still prints the prevention "
             "headline (the positive control)",
             any("no prevention is a lesson nobody learned" in h
                 for h, _ in group_by_headline([(NO_PREVENTION, "p")], here)), True),
            ("headline: no findings prints no headline at all",
             group_by_headline([], here) == [], True),
            # ⚠️ SHARPENED: my first version compared two ORDERINGS of the
            # same input for equality, which proves order-INDEPENDENCE and not
            # the order itself — it passes on any fixed permutation, including
            # a wrong one. It asserts the actual sequence now.
            ("headline: a run tripping all three prints all three, UNREADABLE "
             "first (nothing was checked) and the prevention cause last",
             [h for h, _ in group_by_headline(
                 [(NO_PREVENTION, "p"), (MALFORMED, "m"), (UNREADABLE, "u")], here)]
             == [_headlines(here)[t] for t in (UNREADABLE, MALFORMED, NO_PREVENTION)],
             True),
            ("headline: every message survives the grouping",
             sorted(m for _, ms in group_by_headline(
                 [(NO_PREVENTION, "p"), (MALFORMED, "m")], here) for m in ms)
             == ["m", "p"], True),
            # THE SECOND DEFECT: the old headline hardcoded the canonical path
            # while --path pointed elsewhere, naming a file it had not read.
            ("headline: names the path it ACTUALLY read, not the canonical one",
             all(str(here) in h for h, _ in group_by_headline(
                 [(UNREADABLE, "u"), (MALFORMED, "m"), (NO_PREVENTION, "p")], here)),
             True),
        ]
        try:
            group_by_headline([("no_such_rule", "x")], here)
            _raised = False
        except KeyError:
            _raised = True
        cases.append(("headline: an UNTAGGED rule is a hard error, never a "
                      "silent fallback to a neighbouring cause", _raised, True))

        for label, got, want in cases:
            good_ = bool(got) == want
            ok &= good_
            print(f"  self-test ({label}): {'PASS' if good_ else 'FAIL'}"
                  + ("" if good_ else f" -- {got}"))
    print("recurrence-ledger-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
