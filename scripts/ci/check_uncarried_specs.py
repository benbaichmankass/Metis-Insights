#!/usr/bin/env python3
"""A NEW un-carried spec fails the PR. The standing 98 do not.

WHAT THIS CLOSES
────────────────
`OI-20260906-RESEARCH-THAT-SPECIFIES-WORK-IS-CARRIED-BY-NOTHING` clause (2):

    A MECHANISM makes an un-carried spec visible WITHOUT a session thinking of
    it ... and it has been run over the EXISTING tree, not only armed for new
    artifacts.

Clause (1) — the COUNT — was delivered by MI-152: `scripts/ops/uncarried_specs.py`
with its own controls, the report at `docs/research/uncarried-spec-census-2026-09-06.md`,
and the per-artifact record at `docs/claude/UNCARRIED-SPEC-BASELINE.json`. That
baseline's own `_doc` says it was committed *"so the proposed mechanism has a
measured state to diff against."* This is that mechanism.

⚠️ **MEASURED BEFORE BUILDING: nothing ran it.** `uncarried_specs.py` appeared in
no guard list and in no workflow — grepped, and independently recorded in
`docs/claude/work/RETIRED-MIRRORS-2026-09-11.md`, which classes the baseline as
*"Not a live consumer, and not a CI guard."* An instrument nobody runs measures
nothing, which is the same defect one level up from the one it measures.

WHY IT GATES ON THE DIFF AND REPORTS ON THE TREE
───────────────────────────────────────────────
The standing count is **98 of 111 specs un-carried (88.3%)**. A guard that failed
on that would red every PR in the repo on the day it merged, and the repo has
already written down what happens next: it gets switched off. So the whole-tree
census is **REPORTED and never gates**, and what FAILS is narrow: a file this
diff **ADDS** that classifies as a spec and that nothing carries.

⚠️ **THE FIRST DRAFT GATED ON `live − committed_baseline` AND THAT WAS WRONG.**
`docs/claude/UNCARRIED-SPEC-BASELINE.json` is a SNAPSHOT at `817a5a5f`
(2026-09-06) and its own `_doc` says so: *"IT IS A SNAPSHOT, NOT A LIVE READ."*
Differencing a live census against it attributes **six days of tree drift** to
whichever PR happens to run the guard — measured, it reported ~dozens of
`docs/research/*` files as "NEW and un-carried" that this diff never touched.

That is the **same defect one level up** from the one MI-280 U44 fixed in
`session-brief-guard`: comparing against a moving-or-stale reference and then
printing *"THIS DIFF introduced it"*. Writing it twice in one session is the
argument for stating it here rather than quietly correcting it.

⚠️ **SO THE BASELINE IS A REPORTED COMPARISON, NOT A GATE.** The census line
prints both numbers; a drift between them is information, not a verdict.

⚠️ **THE CENSUS PRINTS ON EVERY RUN, PASS OR FAIL.** A guard that prints only
when it fails lets a zero-coverage state read as a green, which is the
`env-knob-coverage` lesson and, one level up, the whole subject of this row.

⚠️ **THE VERDICT IS `uncarried_specs.analyse`, IMPORTED.** This file re-derives
no part of "is this artifact a spec?" or "is it carried?". Two definitions of a
verdict are free to drift, and the drift is silent — see
`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`, which a lane in this very session
walked into by writing a second grader for a question that already had one.

STATES, NEVER COLLAPSED
───────────────────────
`ok` · `new_uncarried` (the finding — a spec this diff ADDS that nothing carries)
· `unreadable` — *we could not look*: the instrument could not be run, or the
added-file list could not be computed. `unreadable` FAILS. A census that did not
run is not a clean census, and the cheapest way to defeat this guard must not be
to break its input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "docs/claude/UNCARRIED-SPEC-BASELINE.json"
INSTRUMENT = REPO / "scripts/ops/uncarried_specs.py"

OK, NEW_UNCARRIED, UNREADABLE = "ok", "new_uncarried", "unreadable"


def _load_instrument():
    """Import the ONE owner of the verdict. None is *we could not look*."""
    try:
        spec = importlib.util.spec_from_file_location("uncarried_specs", INSTRUMENT)
        mod = importlib.util.module_from_spec(spec)
        # Registered BEFORE exec so a module defining a dataclass can resolve
        # its own __module__ — the failure mode is an opaque AttributeError.
        sys.modules["uncarried_specs"] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def baseline_uncarried(path: Path = BASELINE) -> set[str] | None:
    """Paths the committed baseline records as un-carried. None = unreadable."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    specs = doc.get("specs")
    if not isinstance(specs, list):
        return None
    carried = ("active", "queued")
    out = set()
    for row in specs:
        if not isinstance(row, dict) or "path" not in row:
            return None
        if row.get("state") not in carried:
            out.add(str(row["path"]))
    return out


#: The tiers the census counts as SPECIFICATIONS. Read off the committed
#: baseline rather than assumed: its 111 specs are exactly 98 tier-A + 13
#: tier-B. Tier C is the instrument's *borderline* bucket, which its own report
#: states is reported separately, and tier `none` is not a spec at all.
#:
#: ⚠️ THIS FILTER IS LOAD-BEARING AND ITS ABSENCE WAS A REAL BUG IN THIS FILE.
#: `analyse()` returns a row for EVERY artifact — 402 of them — so an unfiltered
#: read called 341 things un-carried against the baseline's 98, and would have
#: failed a PR for adding an ordinary research note that specifies no work. A
#: guard that fires on the wrong population is argued with, then switched off.
SPEC_TIERS = ("A", "B")


def live_uncarried(mod) -> tuple[set[str] | None, dict]:
    """Un-carried SPEC paths as of the working tree, via the imported instrument."""
    try:
        res = mod.analyse(REPO)
    except Exception:
        return None, {}
    rows = res.get("rows")
    if not isinstance(rows, list):
        return None, res
    carried = set(getattr(mod, "CARRIED_STATES", ("active", "queued")))
    return ({str(r["path"]) for r in rows
             if r.get("tier") in SPEC_TIERS and r.get("state") not in carried},
            res)


def spec_count(res: dict) -> int | None:
    """How many artifacts classify as specs. None is *we could not look*."""
    rows = res.get("rows") if isinstance(res, dict) else None
    if not isinstance(rows, list):
        return None
    return sum(1 for r in rows if r.get("tier") in SPEC_TIERS)


def added_paths(base_ref: str, repo: Path | None = None) -> list[str] | None:
    """Files this diff ADDS, relative to *base_ref*. None is *we could not look*.

    ⚠️ `--diff-filter=A` only. A spec that already existed is not this author's
    to carry, and blaming them for it is how a guard gets argued with instead of
    fixed.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=A", f"{base_ref}...HEAD"],
            capture_output=True, text=True, timeout=60, cwd=repo or REPO)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def grade(added: list[str] | None, live_uncarried_set: set[str] | None
          ) -> tuple[str, set[str]]:
    """Did THIS DIFF add a spec that nothing carries? Pure, so it is arguable in tests."""
    if added is None or live_uncarried_set is None:
        return UNREADABLE, set()
    return_new = {p for p in added if p in live_uncarried_set}
    return (NEW_UNCARRIED if return_new else OK), return_new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--base", default="origin/main",
                    help="gate against files ADDED relative to this ref")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    mod = _load_instrument()
    if mod is None:
        print(f"::error::uncarried-specs: could not import {INSTRUMENT} — that is "
              "*we could not look*, not a clean census. Failing closed.")
        return 1

    # The instrument's OWN controls run first. A guard whose probe is never
    # shown to discriminate is indistinguishable from one that always passes.
    try:
        probe_ok = mod.self_test()
    except Exception:
        probe_ok = False
    if not probe_ok:
        print("::error::uncarried-specs: the instrument's own controls FAILED, so "
              "its census cannot be trusted and this guard will not report one.")
        return 1

    live, res = live_uncarried(mod)
    base = baseline_uncarried()
    added = added_paths(a.base)

    # THE WHOLE-TREE CENSUS, EVERY RUN, PASS OR FAIL — and it NEVER gates.
    # ⚠️ The keys are TOP-LEVEL on analyse()'s result. A first draft read them
    # from a `population` sub-dict that does not exist, so the line printed
    # "of ? specs (? artifacts scanned)" — a denominator that announces itself
    # as missing is better than a wrong one, but it is still a diagnostic
    # reporting a quantity it did not compute.
    n_specs = spec_count(res)
    scanned = res.get("population_scanned") if isinstance(res, dict) else None
    print(f"uncarried-specs: census — {len(live) if live is not None else '?'} un-carried "
          f"of {n_specs if n_specs is not None else '?'} specs "
          f"({scanned if scanned is not None else '?'} artifacts scanned). "
          f"The 2026-09-06 baseline recorded "
          f"{len(base) if base is not None else '?'} — reported for comparison, "
          f"NOT a gate: it is a snapshot at 817a5a5f and drift against it is not "
          f"this diff's doing.")

    state, new = grade(added, live)

    if state == UNREADABLE:
        which = "live census" if live is None else f"added-file list against {a.base}"
        print(f"::error::uncarried-specs: the {which} could not be read. That is "
              "*we did not look*, which is NOT the same as 'this diff adds no "
              "un-carried spec'. Failing closed, deliberately: the cheapest way "
              "past this guard must not be to break its input.")
        return 1

    if state == NEW_UNCARRIED:
        print("::error::uncarried-specs: this diff ADDS spec(s) that nothing carries. "
              "A spec no register points at is exactly as invisible as a bad one — "
              "docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md sat in that state "
              "for 14 days while the work it specified went unbuilt.")
        for p_ in sorted(new):
            print(f"  ADDED by this diff and un-carried: {p_}")
        print("  REMEDY: point a work object, ROADMAP row or open-items row at it — "
              "or, if it genuinely specifies no work, say so in the artifact. "
              "⚠️ Adding it to the baseline is NOT the remedy: the baseline does "
              "not gate anything, so editing it changes nothing here.")
        return 1

    print(f"uncarried-specs: OK — this diff adds {len(added)} file(s) and none of "
          "them is an un-carried spec.")
    return 0


def _self_test() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= bool(cond)
        print(f"  self-test ({label}): {'PASS' if cond else 'FAIL'}")

    A, B, C = "docs/research/a.md", "docs/research/b.md", "docs/research/c.md"

    check("adding nothing is ok", grade([], {A, B}) == (OK, set()))
    check("adding a CARRIED file is ok", grade([C], {A, B}) == (OK, set()))
    check("adding an UN-CARRIED spec is the finding",
          grade([B], {A, B}) == (NEW_UNCARRIED, {B}))
    check("only the ADDED one is reported, not the whole standing pile",
          grade([B], {A, B, C}) == (NEW_UNCARRIED, {B}))

    # ⚠️ THE CONTROL FOR THE DEFECT THE FIRST DRAFT SHIPPED. A large standing
    # pile that this diff did NOT add must not fail the PR. The first version
    # gated on `live - committed_baseline` and reported dozens of untouched
    # docs/research/* files as this diff's doing -- the same stale-reference
    # blame that MI-280 U44 had just fixed in session-brief-guard.
    check("a big standing pile with NO additions stays green",
          grade([], {A, B, C}) == (OK, set()))

    check("an unreadable ADDED-FILE list is unreadable, never ok",
          grade(None, {A}) == (UNREADABLE, set()))
    check("an unreadable LIVE census is unreadable, never ok",
          grade([A], None) == (UNREADABLE, set()))
    check("both unreadable is unreadable", grade(None, None)[0] == UNREADABLE)

    # ⚠️ An EMPTY live census must not be read as a clean sweep: it is `ok` only
    # because nothing ADDED is in it, and the census line reports the zero.
    check("an empty live set is ok even when files were added",
          grade([A, B], set()) == (OK, set()))

    # The diff reader, against the REAL repo -- it must return a LIST (possibly
    # empty), never None, on a ref that exists. None here would mean every run
    # fails closed and nobody would know why.
    real_added = added_paths("HEAD")
    check("added_paths returns a list against a valid ref (HEAD...HEAD is empty)",
          isinstance(real_added, list) and real_added == [])
    check("added_paths on a bogus ref is None -- *we could not look*, not 'nothing added'",
          added_paths("no-such-ref-at-all") is None)

    # ⚠️ ADDED-ONLY, PROVED BEHAVIOURALLY IN A THROWAWAY REPO. A plant flipping
    # `--diff-filter=A` to `AM` ESCAPED the first battery: every other control
    # was about the GRADE, and none of them exercised the git read. Blaming an
    # author for a spec they merely TOUCHED is the dangerous direction -- the
    # standing pile is 103 files, so one incidental edit would fail the PR and
    # the remedy would not be in the author's hands.
    ok_added = _self_test_added_only()
    ok &= ok_added

    # The baseline reader, against the REAL committed file — a control that
    # would go red if the file's shape changed under us.
    base = baseline_uncarried()
    check("the committed baseline parses and is non-empty",
          base is not None and len(base) > 0)
    # ⚠️ THE READER IS RECONCILED AGAINST THE FILE'S OWN DECLARED TOTAL. This is
    # the control that matters: it pins "what this guard thinks un-carried means"
    # to what the baseline's author measured, so a reader bug cannot pass by
    # agreeing with itself. Measured: both read 98.
    import json as _json
    try:
        _declared = _json.loads(BASELINE.read_text(encoding="utf-8")).get(
            "uncarried_total_strict")
    except Exception:
        _declared = None
    check("this reader's un-carried count reconciles with the baseline's own "
          f"declared uncarried_total_strict ({_declared})",
          base is not None and _declared is not None and len(base) == _declared)

    # ⚠️ AND THE NEGATIVE CONTROL IS CARRIED, NOT UN-CARRIED — a control that
    # asserted the opposite was written first and FAILED, correctly. It encoded
    # an assumption rather than a reading: EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md
    # is the artifact the whole class is named after, so it *feels* like the
    # un-carried exemplar, but MI-148 rescued it and the baseline records it
    # `active` with four work carriers. The incident is what the row is about;
    # the artifact itself has been carried since before the baseline was taken.
    check("the baseline's named negative control is CARRIED, so it is absent "
          "from the un-carried set (rescued by MI-148 before the baseline)",
          base is not None and not any("EXIT-GEOMETRY-REBUILD" in p for p in base))
    check("a malformed baseline reads as unreadable, not as empty",
          baseline_uncarried(Path("/nonexistent/nope.json")) is None)

    # ⚠️ PINS SPEC_TIERS TO THE BASELINE'S OWN DEFINITION rather than to my
    # reading of it. Every one of the baseline's 111 specs must fall inside
    # SPEC_TIERS; if someone widens the filter to include tier C or `none`, the
    # guard starts firing on ordinary research notes, and if someone narrows it
    # the population silently shrinks. Either way this control goes red.
    import json as _json2
    try:
        _specs = _json2.loads(BASELINE.read_text(encoding="utf-8")).get("specs") or []
        _tiers = {sp.get("tier") for sp in _specs}
    except Exception:
        _tiers = None
    # ⚠️ EQUALITY, NOT CONTAINMENT -- and the difference is the whole control.
    # A first draft asserted `_tiers <= set(SPEC_TIERS)`, which is preserved by
    # WIDENING, so a plant adding tier C and `none` PASSED. Widening is the
    # dangerous direction: it makes the guard fire on ordinary research notes
    # that specify no work, and a guard that fires on the wrong population gets
    # argued with and then switched off. Narrowing was already caught.
    check(f"SPEC_TIERS EQUALS the baseline's own tier set (SPEC_TIERS={SPEC_TIERS}, "
          f"baseline tiers: {sorted(_tiers) if _tiers else '?'})",
          _tiers is not None and _tiers == set(SPEC_TIERS))

    print("uncarried-specs self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _self_test_added_only() -> bool:
    """`added_paths` reports ADDED files and never MODIFIED ones.

    Needs a real repository: the property lives in a git invocation, and a pure
    test cannot see a wrong `--diff-filter`.
    """
    import shutil
    import subprocess
    import tempfile

    if shutil.which("git") is None:
        print("  self-test (added-only): COULD-NOT-RUN -- no git on PATH")
        return False

    def run(*args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, timeout=30)

    tmp = tempfile.mkdtemp(prefix="ucs-")
    try:
        run("init", "-q", "-b", "main", cwd=tmp)
        run("config", "user.email", "t@t", cwd=tmp)
        run("config", "user.name", "t", cwd=tmp)
        Path(tmp, "existing.md").write_text("v1\n", encoding="utf-8")
        run("add", "-A", cwd=tmp)
        run("commit", "-q", "-m", "base", cwd=tmp)
        base = run("rev-parse", "HEAD", cwd=tmp).stdout.strip()

        Path(tmp, "existing.md").write_text("v2 -- MODIFIED, not added\n", encoding="utf-8")
        Path(tmp, "brand-new.md").write_text("ADDED by this diff\n", encoding="utf-8")
        run("add", "-A", cwd=tmp)
        run("commit", "-q", "-m", "modify one, add one", cwd=tmp)

        got = added_paths(base, repo=Path(tmp))
        good = got == ["brand-new.md"]
        print("  self-test (added-only: a MODIFIED file is not reported as added): "
              f"{'PASS' if good else f'FAIL got={got}'}")
        return good
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
