#!/usr/bin/env python3
"""`blocked_on` kinds must be in the declared vocabulary, or the edge is untyped.

`docs/claude/work/README.md` says the edge is TYPED — *"Each entry is
`{kind, ref, since}` with `kind` ∈ `object` · `operator_decision` ·
`external_event` · `data_accrual` · `capability`. This is what lets the
constraint be **computed** rather than judged."* Nothing enforced that, so an
undeclared kind was silently accepted and the set kept growing.

⚠️ **THE CONSEQUENCE IS NOT TIDINESS, AND IT IS LIVE.**
`scripts/ops/constraint_readout.py::grade_edge` grades `ref_state` **only** for
`kind == "object"` and calls every other kind `not_in_store_by_design` — which is
right for a kind that genuinely names something outside the store, and wrong for
a MISSPELLING of one that does not. Measured 2026-09-12:
`WO-20260903-EVERY-PR-APPROVAL-REACHES-THE-OPERATOR-AS-AN` carries
`kind: work_object` pointing at
`WO-20260903-THE-REFUSAL-HALF-OF-MANAGER-CONTROL-BLOCK`, **which IS in the
store** — so a real in-store reference is graded "outside the store by design"
and no consumer ever checks it resolves.

⚠️ **AND THE UNDECLARED SET IS GROWING, WHICH IS WHY A REMINDER IS NOT THE FIX.**
`BL-20260911-FIVE-BLOCKED-ON-KINDS-IN-LIVE-USE-ARE-OUTSIDE-THE-DECLARED-VOCABULARY`
counted FIVE on 2026-09-11. Re-measured 2026-09-12 over 185 YAML files under
`docs/claude/work/` (180 objects + 2 intents + 3 others; 0 unparseable, stated
as the positive control) there are **SIX**: `pr` 2 · `backlog_row` 2 ·
`work_object` 1 · `review` 1 · `artifact` 1 · **`observation` 1, which is new
since the row was filed**. Nothing validates the field, so the vocabulary drifts
by one entry every time somebody needs to say something it cannot express.

⚠️ **THIS GUARD DOES NOT DECIDE THE VOCABULARY, DELIBERATELY.** Whether `pr` and
`backlog_row` should be declared kinds is a schema question for the store's
owner — and there is already evidence the answer is yes for `pr`, which
`scripts/ops/manager_preflight.py::_INTERNAL_KINDS` has treated as internal all
along while the README omits it. That is itself a second-vocabulary drift, and
it is REPORTED here rather than resolved.

⚠️ **SO THE SIX EXISTING KINDS ARE GRANDFATHERED, and a SEVENTH fails.** A guard
that failed on today's residue would red every PR in the repo on the day it
merged, which is how a guard gets switched off — the lesson
`workflow-push-target-guard` records beside its own `conditional_default` rows,
and the same call `check_decision_answers.py::_spent_edges` makes. The
grandfather list is DATED and its entries are printed on every run, so it reads
as a debt rather than as an approval.

Run:  python3 scripts/ci/check_edge_kind_vocabulary.py
      python3 scripts/ci/check_edge_kind_vocabulary.py --self-test
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "docs" / "claude" / "work"
README = WORK / "README.md"

#: The declared vocabulary. READ FROM THE README rather than restated, so this
#: guard cannot enforce a set the document does not say — which is the drift it
#: exists to catch, one level up. A README that no longer states one FAILS.
_DECLARED_MARKER = "`blocked_on` is a typed edge"

#: Kinds that are in live use, undeclared, and predate this guard. DATED, and
#: printed on every run so the list reads as a DEBT rather than an approval.
#: ⚠️ Adding to it is how this guard gets hollowed out — a SEVENTH undeclared
#: kind must fail, and the remedy is to declare it in the README or to re-point
#: the edge, never to widen this.
GRANDFATHERED_2026_09_12 = {
    # `manager_preflight._INTERNAL_KINDS` already treats `pr` as internal while
    # the README omits it — a second vocabulary, reported not resolved.
    "pr": "names a PR number; already internal to manager_preflight, undeclared in the README",
    "backlog_row": "names a backlog id; one of the two live refs is TRUNCATED and resolves to nothing",
    # THE ONE THAT IS A DEFECT RATHER THAN A GAP.
    "work_object": "a MISSPELLING of `object`; its ref IS in the store and grade_edge calls it not_in_store_by_design",
    "review": "ref is prose, not an id",
    "artifact": "ref is prose, not an id",
    "observation": "ref is prose, not an id; NEW since the backlog row was filed",
}


#: Objects carrying a `blocked_on` ENTRY THAT IS NOT A TYPED EDGE AT ALL, dated
#: and named. A different class from an undeclared kind and listed separately
#: on purpose: an undeclared kind is a vocabulary gap, while this one is an
#: entry no consumer can read.
#:
#: ⚠️ THE SUBSTANTIVE REPAIR IS NOT HERE. `constraint_readout.grade_blocked_on`
#: used to DROP a non-dict entry, leave an empty list, and fall through to the
#: basis check — so this object, whose own `blocked_on_basis` reads "This is a
#: TRUE edge, not a placeholder", graded `declared_none`: **a CLAIM that nothing
#: blocks it**, on an object that is `lifecycle: ready`. That grader now returns
#: `malformed`, so the false all-clear is gone whether or not the file is fixed.
#:
#: ⚠️ FIXING THE FILE IS THE OBJECT OWNER'S OR THE MANAGER'S CALL, which is why
#: this list exists instead of an edit: re-shaping the entry would move the
#: object from `malformed` to `blocked` and change what the WIP ceiling and the
#: constraint readout compute over.
KNOWN_MALFORMED_2026_09_12 = {
    "WO-20260908-RE-DISPATCH-THE-MGC-REMEDIATION-AGAINST-THE.yaml":
        "blocked_on is ['WO-20260907-ROOT-CAUSE-THE-43-PHANTOM-MGC-LOTS'] — a bare "
        "string, not {kind, ref, since}. The basis says it is a TRUE edge.",
}


def declared_kinds(readme: Path = README) -> set[str] | None:
    """The vocabulary the README states, or None when it states none.

    None is *the document no longer declares one*, never *the set is empty* —
    returning an empty set would silently pass every kind, which is the failure
    shape this file's own header warns about.
    """
    if not readme.is_file():
        return None
    for line in readme.read_text(encoding="utf-8").splitlines():
        pass
    text = readme.read_text(encoding="utf-8")
    i = text.find(_DECLARED_MARKER)
    if i == -1:
        return None
    window = text[i:i + 400]
    j = window.find("∈")
    if j == -1:
        return None
    import re
    found = set(re.findall(r"`([a-z_]+)`", window[j:j + 200]))
    return found or None


def scan(root: Path = WORK, readme: Path = README) -> dict:
    """Every `blocked_on` kind in use, graded against the declared set."""
    declared = declared_kinds(readme)
    files = sorted(root.rglob("*.yaml"))
    parsed = 0
    unparseable: list[str] = []
    rows: list[dict] = []
    counts: dict[str, int] = {}
    for p in files:
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            unparseable.append(f"{p.name}: {exc}")
            continue
        if not isinstance(doc, dict):
            unparseable.append(f"{p.name}: not a mapping")
            continue
        parsed += 1
        for e in (doc.get("blocked_on") or []):
            if not isinstance(e, dict):
                rows.append({"file": p.name, "kind": None, "ref": None,
                             "state": "malformed_edge"})
                continue
            kind = e.get("kind")
            counts[str(kind)] = counts.get(str(kind), 0) + 1
            if declared is None:
                state = "vocabulary_unreadable"
            elif kind in declared:
                state = "declared"
            elif kind in GRANDFATHERED_2026_09_12:
                state = "grandfathered"
            else:
                state = "undeclared"
            rows.append({"file": p.name, "kind": kind, "ref": e.get("ref"),
                         "state": state})
    return {
        "declared": sorted(declared) if declared else None,
        "population": {"files": len(files), "parsed": parsed,
                       "unparseable": unparseable, "edges": len(rows)},
        "counts": counts,
        "rows": rows,
    }


def findings(s: dict) -> list[str]:
    """What FAILS. Grandfathered kinds are reported by `main`, never here."""
    out: list[str] = []
    if s["declared"] is None:
        out.append(
            f"{README.relative_to(REPO)}: no `blocked_on` kind vocabulary could be "
            f"read (marker {_DECLARED_MARKER!r}). This check is silently disabled "
            f"without it — fix the document, do not ignore this.")
        return out
    for f in s["population"]["unparseable"]:
        out.append(f"{f} — could not parse, so its edges were NOT graded. This is "
                   f"an ABSENT reading, never a clean one.")
    for r in s["rows"]:
        if r["state"] == "malformed_edge":
            if r["file"] in KNOWN_MALFORMED_2026_09_12:
                continue  # reported by `main` as dated debt, never as a pass
            out.append(
                f"{r['file']}: a `blocked_on` entry is not a mapping, so it carries "
                f"no kind and no ref that any consumer can read. "
                f"constraint_readout.grade_blocked_on now grades the whole object "
                f"`malformed` rather than silently dropping the entry — which is "
                f"correct and is NOT a substitute for writing {{kind, ref, since}}.")
        elif r["state"] == "undeclared":
            out.append(
                f"{r['file']}: `blocked_on` kind {r['kind']!r} is not in the declared "
                f"vocabulary {sorted(s['declared'])} and is not grandfathered. "
                f"An undeclared kind is graded `not_in_store_by_design` by "
                f"constraint_readout.grade_edge, so its ref is never checked — "
                f"declare it in {README.relative_to(REPO)} or re-point the edge.")
    return out


def _self_test() -> int:
    """Both verdicts, and the two ways this check could stop looking."""
    import tempfile
    failures: list[str] = []

    readme = ("**`blocked_on` is a typed edge, not a flag.** Each entry is "
              "`{kind, ref, since}` with `kind`\n∈ `object` · `operator_decision` · "
              "`external_event` · `data_accrual` · `capability`. This is\n")
    obj = ("id: X\nlifecycle: waiting\nblocked_on:\n"
           "  - {kind: %s, ref: R, since: '2026-01-01'}\n")

    def run(kind: str, readme_text: str = readme):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text(readme_text, encoding="utf-8")
            (root / "a.yaml").write_text(obj % kind, encoding="utf-8")
            s = scan(root, root / "README.md")
            return s, findings(s)

    s, f = run("object")
    if f:
        failures.append(f"a DECLARED kind was reported: {f}")
    if s["rows"][0]["state"] != "declared":
        failures.append(f"`object` graded {s['rows'][0]['state']}")

    s, f = run("never_seen_before")
    if not f or "not in the declared vocabulary" not in f[0]:
        failures.append("an undeclared kind was NOT reported — the guard has no teeth")

    # A GRANDFATHERED KIND IS REPORTED AS DEBT, NEVER AS A FINDING.
    s, f = run("work_object")
    if f:
        failures.append(f"a grandfathered kind failed the guard: {f}")
    if s["rows"][0]["state"] != "grandfathered":
        failures.append(f"`work_object` graded {s['rows'][0]['state']}")

    # THE TWO WAYS THIS COULD STOP LOOKING, both of which must FAIL LOUD.
    s, f = run("object", readme_text="a README that declares nothing at all\n")
    if not f or "silently disabled" not in f[0]:
        failures.append("an unreadable vocabulary passed as clean")
    if s["declared"] is not None:
        failures.append("declared_kinds invented a vocabulary from a silent README")
    # ⚠️ GRADE `declared_kinds` DIRECTLY, not only through `scan`. `scan`
    # normalises a falsy vocabulary to None, so a planted `return found` (an
    # EMPTY SET where None is the contract) is absorbed there and the control
    # above stays green — a defect hidden by a second safety net is still a
    # defect, and the next caller may not have the net. An empty set would mean
    # "we read a vocabulary and it has no members", which passes every kind.
    with tempfile.TemporaryDirectory() as td:
        _silent = Path(td) / "README.md"
        _silent.write_text("a README that declares nothing at all\n", encoding="utf-8")
        if declared_kinds(_silent) is not None:
            failures.append("declared_kinds returned a non-None empty vocabulary — "
                            "`we could not read one` must not render as `it has "
                            "no members`")
        _absent = Path(td) / "nope.md"
        if declared_kinds(_absent) is not None:
            failures.append("declared_kinds invented a vocabulary from a missing file")
        # …and the case the two above CANNOT reach: the marker and the `∈` are
        # both present and NO kinds follow. A silent README returns early at the
        # `∈` test, so only this fixture exercises the final `or None` — and
        # without it a `return found` plant survives the whole battery.
        _empty = Path(td) / "empty-vocab.md"
        _empty.write_text(
            "**`blocked_on` is a typed edge, not a flag.** with `kind`\n"
            "∈ and then nothing at all.\n", encoding="utf-8")
        if declared_kinds(_empty) is not None:
            failures.append("a declared-but-EMPTY vocabulary returned a set — it "
                            "would accept every kind while reading as configured")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.md").write_text(readme, encoding="utf-8")
        (root / "bad.yaml").write_text("\tnot: [yaml", encoding="utf-8")
        s = scan(root, root / "README.md")
        f = findings(s)
        if not any("could not parse" in x for x in f):
            failures.append("an unparseable object passed silently")
        if s["population"]["parsed"] != 0:
            failures.append("an unparseable file was counted as parsed")

    # A MALFORMED ENTRY: a NEW one fails, the dated known one is reported as debt.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.md").write_text(readme, encoding="utf-8")
        (root / "fresh.yaml").write_text(
            "id: X\nblocked_on:\n  - JUST-A-STRING\n", encoding="utf-8")
        s2 = scan(root, root / "README.md")
        f2 = findings(s2)
        if not any("not a mapping" in x for x in f2):
            failures.append("a NEW malformed entry did not fail the guard")
        known = next(iter(KNOWN_MALFORMED_2026_09_12))
        (root / "fresh.yaml").rename(root / known)
        s3 = scan(root, root / "README.md")
        if findings(s3):
            failures.append(f"the dated known-malformed entry failed: {findings(s3)}")
        if s3["rows"][0]["state"] != "malformed_edge":
            failures.append("a non-mapping entry was not graded malformed_edge")

    for line in (
            "declared kind accepted", "undeclared kind refused",
            "new malformed entry refused, dated one reported as debt",
            "grandfathered kind reported as debt, not failed",
            "unreadable vocabulary fails loud", "unparseable object fails loud"):
        print(f"  self-test {line}: {'FAIL' if failures else 'PASS'}")
    if failures:
        print("edge-kind-vocabulary self-test: FAIL")
        for x in failures:
            print("   " + x)
        return 1
    print("edge-kind-vocabulary self-test: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    s = scan()
    pop = s["population"]
    print(f"edge-kind-vocabulary: {pop['parsed']} file(s) parsed of {pop['files']}, "
          f"{len(pop['unparseable'])} unparseable, {pop['edges']} edge(s); "
          f"declared vocabulary {s['declared']}")

    # THE DEBT, printed on EVERY run — a grandfather list nobody sees is an
    # approval, and this one is meant to shrink.
    live = {r["kind"] for r in s["rows"] if r["state"] == "grandfathered"}
    if live:
        print(f"  ~ GRANDFATHERED (undeclared, in use, predates this guard) — "
              f"{len(live)} kind(s), REPORTED not failed:")
        for k in sorted(live):
            n = sum(1 for r in s["rows"] if r["kind"] == k)
            print(f"      {k:<14} x{n}  {GRANDFATHERED_2026_09_12[k]}")
        print("    Declare them in docs/claude/work/README.md or re-point the edges; "
              "widening the grandfather list is how this guard gets hollowed out.")

    mal = [r for r in s["rows"] if r["state"] == "malformed_edge"
           and r["file"] in KNOWN_MALFORMED_2026_09_12]
    if mal:
        print(f"  ~ KNOWN MALFORMED ENTRIES — {len(mal)}, REPORTED not failed "
              f"(a DIFFERENT class from an undeclared kind):")
        for r in mal:
            print(f"      {r['file']}")
            print(f"        {KNOWN_MALFORMED_2026_09_12[r['file']]}")
        print("    constraint_readout.grade_blocked_on grades these `malformed` "
              "rather than `declared_none`, so the false all-clear is already gone. "
              "Re-shaping the entry is the object owner's or the manager's call.")

    f = findings(s)
    if f:
        print(f"\nedge-kind-vocabulary: {len(f)} finding(s)")
        for x in f:
            print(f"  - {x}")
        return 1
    print("edge-kind-vocabulary: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
