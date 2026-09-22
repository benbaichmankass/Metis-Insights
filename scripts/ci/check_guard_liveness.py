#!/usr/bin/env python3
"""A CHECK MUST BE ABLE TO SAY WHEN IT CANNOT SEE ITS SUBJECT, AND THAT MUST BE LOUD.

THE MISSING INVARIANT, NOT THE THREE BUGS
-----------------------------------------
Three independent instances in one week, all the same shape — a check reporting a
passing or quiet state about a thing it could no longer see:

  1. `check_manager_scope.py` derived manager identity from
     `docs/claude/work/MANAGER-LEASE.json`, which the 2026-09-21 operating reset
     archived. The roster froze at 7 pre-reset sessions, could never grow, and
     the guard PASSED on all 38 of the next manager's commits — 31 of them off
     its own surface.
  2. `work_digest` had five of six SOURCES reading `absent` on every run since
     the same reset while the digest reported itself healthy (fixed in #12719).
  3. `check_manager_queue_watch.py` reports `NEVER_RAN_OVERDUE`: armed 479 hours,
     ~479 expected firings, ZERO receipts ever written. The watchdog built
     because *"nothing catches a manager doing nothing"* had itself done nothing,
     and nothing caught it.

⚠️ **THREE IS NOT THREE BUGS, IT IS A MISSING INVARIANT.** In every one,
*"I have not run / I cannot see my subject"* and *"I ran and found nothing"* were
INDISTINGUISHABLE. That is `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states"
— *"ask whether 'we did not look' and 'we looked and found nothing' are
distinguishable; if they are not, that is the bug"* — applied to the CHECKS
THEMSELVES rather than to the data they read. `scripts/ci/check_collapsed_states.py`
polices producers; nothing policed the police.

WHAT THIS MEASURES, AND THE INSTRUMENT WAS VALIDATED BEFORE IT WAS TRUSTED
--------------------------------------------------------------------------
A guard's **declared subject** is a module-level constant naming a repo path. If
that path is absent at HEAD, the guard cannot grade it.

⚠️ **THE OBVIOUS PROBE DOES NOT WORK, AND IT WAS MEASURED RATHER THAN ASSUMED.**
Scanning for *any* path literal anywhere in a guard returns **29 of 69**
`scripts/ci/check_*.py` files and is DOMINATED BY SELF-TEST FIXTURES —
`docs/WORKPLAN-a.md`, `scripts/ops/bare_tool.py`,
`.github/pr-landing/nested/f.json` are planted inside `self_test()`, not graded.
A guard is supposed to plant paths that do not exist. Restricting to MODULE-LEVEL
assignments (`ast.Module.body`, never a function body) removes that whole class:
**19 of 95** with no fixture noise. Prove the probe can find a positive, and
prove it does not fire on the negative — the naive one failed the second test.

FOUR STATES, NEVER COLLAPSED — and the fourth is the one that matters
---------------------------------------------------------------------
  ``live``       every declared subject exists. The guard can grade.
  ``degraded``   SOME subjects absent. It still grades — over a SILENTLY NARROWER
                 population. This is the `work_digest` shape: five of six sources
                 `absent` and a healthy self-report. It is NOT a pass.
  ``dead``       EVERY declared subject absent. The guard grades NOTHING and its
                 green means nothing. This is the `check_manager_scope` shape.
  ``no_declared_subject``
                 the guard declares no module-level repo path, so **THIS
                 INSTRUMENT CANNOT SPEAK TO IT.** ⚠️ That is *we could not look*
                 and it is reported as its own count, never folded into `live`.
                 Collapsing it into `live` would be this module committing the
                 exact defect it exists to find, and the count is large (46 of
                 95), so the temptation is real.

MEASURED 2026-09-22 over **95 guard scripts** (`scripts/ci/check_*.py` +
`scripts/check_*.py`), every module-level repo-path constant tested with
`git cat-file -e HEAD:<path>`:

| state | count |
|---|--:|
| `no_declared_subject` — this instrument cannot speak to it | **46** |
| `live` | **30** |
| `degraded` — grades a silently narrower set | **11** |
| **`dead` — grades NOTHING** | **8** |

⚠️ **THE EIGHT DEAD ARE ALL THE 2026-09-21 RESET'S BLAST RADIUS, AND TWO OF THEM
ARE NAMED IN THE CANONICAL DOC AS LIVE MECHANISMS.** `check_claim_basis.py` is
cited by `docs/CLAUDE-RULES-CANONICAL.md` § "Backlog governance" as what enforces
the `status` enum *"whole-file over every review backlog"* — and all four review
backlogs are archived, so it grades nothing. `check_soak_registered.py` is cited
as what fails a soak log with no register alarm — its register
(`docs/claude/OPEN-ITEMS.json`) is archived. `check_operator_owed.py` is cited as
what FAILS an item carried without a state change — its register is archived. A
canonical doc naming a mechanism that cannot run is the folklore failure that
document has its own section about.

WHY IT IS REPORT-FIRST, AND WHY THAT IS NOT WEAKNESS
-----------------------------------------------------
⚠️ **FAILING ALL 19 ON DAY ONE WOULD RED-WALL THE REPO AND GET THIS REVERTED.**
That is not a hypothetical: `check_pr_queue_watch.py` records refusing to fail
PRs on backlog size for exactly this reason, and § "could not measure is its own
outcome" records a guard whose first CI run emitted 117 fake findings from one
absent import, *"burying the only fact that mattered"*. So:

  * the **existing** debt is carried in a dated `BASELINE` and PRINTED as a count
    on every run — the `check_soak_registered.py` pattern, whose acceptability
    rests on not being silent: every name is a visible line in the diff, under a
    comment saying the list may only SHRINK;
  * a **NEW** `dead` or `degraded` guard — one not in the baseline — FAILS. That
    is the regression this exists to catch, and it costs nothing today because
    the baseline holds everything already broken;
  * a baselined guard whose subject has COME BACK fails too, because the list
    may only shrink and a stale exemption is a hole that accumulates.

What becomes blocking beyond that is the operator's call, and the numbers above
are what they need to make it. Nothing here proposes promoting the 19.

WHAT THIS DOES NOT CLAIM
------------------------
⚠️ **AN ABSENT SUBJECT IS NOT THE ONLY WAY A CHECK DIES.** The third instance —
`check_manager_queue_watch` — is NOT in this sweep's reach: its subject exists as
a path and the thing that died is the **Routine that writes it**. That failure
needs a receipt-age check, which that guard already has and which correctly
reports `NEVER_RAN_OVERDUE`. This instrument covers *archived input*; it does not
cover *unwired schedule*. Stated so a reader does not price this as a complete
answer to the class.

⚠️ **AND `live` HERE MEANS "ITS SUBJECT EXISTS", NEVER "IT WORKS".** A guard can
read a present file and grade the wrong thing. This is a liveness probe, not a
correctness one.

Run with `--self-test` to plant each state, or bare to report the sweep.
EXIT: 0 report-only pass · 1 a NEW dead/degraded guard, or a baseline entry whose
subject returned · 2 the sweep itself could not run (we could not look).
"""
from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]

#: Where guards live. Both directories, because `check_claim_basis.py` — the one
#: the canonical doc leans on hardest — is in `scripts/`, not `scripts/ci/`.
GUARD_GLOBS = ("scripts/ci/check_*.py", "scripts/check_*.py")

#: A module-level string constant is treated as a declared subject when it starts
#: with one of these. Anything else is not a repo path.
REPO_PREFIXES = ("docs/", "scripts/", "config/", "src/", "comms/", "automation/",
                 "research/", "ml/", "deploy/", "tests/", ".github/",
                 "runtime_logs/")

LIVE = "live"
DEGRADED = "degraded"
DEAD = "dead"
NO_SUBJECT = "no_declared_subject"

#: The debt as it stood when this guard was written, by guard filename.
#:
#: ⚠️ AN ESCAPE HATCH, AND IT IS THE `check_soak_registered.py` PATTERN ON
#: PURPOSE. What makes it acceptable is that it is NOT SILENT: every name is a
#: visible line here, in the diff, under a comment saying the list may only
#: SHRINK, and the count prints on every run. A reviewer sees a deliberate act.
#:
#: ⚠️ THE LIST MAY ONLY SHRINK. A guard whose subject has come back is REMOVED
#: from here, and this module FAILS if it is still listed — a stale exemption is
#: how a baseline turns into a permanent hole.
#:
#: Every entry is the 2026-09-21 operating reset's blast radius: it archived the
#: four review backlogs, OPEN-ITEMS.json, the lease, SESSIONS.json,
#: MERGE-QUEUE.json, session-board.json, RECURRENCE-LEDGER.json and
#: operator-owed-register.json, and nothing swept the guards that read them.
BASELINE_2026_09_22 = {
    # ── ALL EIGHT `dead` ENTRIES REMOVED 2026-09-22 (E45, PR #12756) ──────
    #
    # 19 -> 11. They were: check_backlog_unresolve, check_claim_basis,
    # check_open_items, check_operator_owed, check_recurrence_ledger,
    # check_register_field_loss, check_soak_registered,
    # check_stated_population.
    #
    # ⚠️ REMOVED BECAUSE THEY WERE DISPOSED OF, NOT BECAUSE THE LIST WAS
    # TIDIED. This module fails on rot in BOTH directions and it named all
    # eight itself on the first run after the merge — four `BASELINE STALE`
    # (the guard was retired and its script deleted, with a written reason in
    # docs/archive/2026-09-21-operating-reset/guards/RETIRED-GUARDS-2026-09-22.md)
    # and four `BASELINE RECOVERED` (re-pointed at a subject that exists, each
    # now failing on a violation planted against that new subject in its own
    # --self-test, because a green re-point proves nothing).
    #
    # ⚠️ `check_stated_population.py` RECOVERED AS `no_declared_subject`, NOT
    # `live`, AND THAT IS THIS INSTRUMENT'S OWN FALSE POSITIVE, CORRECTED.
    # It was never dead: its graded population is a GLOB over `docs/**.md` in
    # the PR diff, and it is registered in run_guards.py on every PR. Verified
    # by running it against a planted diff — exit 1, naming the line. It read
    # `dead` only because four archived path constants were the only
    # module-level paths this module can see, which is the CONVERSE of the
    # limit stated at the top of this file: `live` means the subject exists,
    # never that the guard works — and `dead` can mean the guard's subject is
    # a glob this module cannot see. Filed as
    # PI-20260922-E45-GUARD-LIVENESS-CALLS-A-GLOB-SCOPED-GUARD-DEAD-AND-STATED-POPULATION-WAS-THE-FALSE-POSITIVE.
    #
    # degraded — grade a silently narrower set
    "check_artifact_caveats.py": DEGRADED,
    "check_automerge_trigger.py": DEGRADED,
    "check_canonical_doc_coherence.py": DEGRADED,
    "check_impossibility_claims.py": DEGRADED,
    "check_manager_scope.py": DEGRADED,
    "check_pr_landing.py": DEGRADED,
    "check_register_ids.py": DEGRADED,
    "check_role_pack_operating_layer.py": DEGRADED,
    "check_scope_overlap.py": DEGRADED,
    "check_spec_carrier.py": DEGRADED,
    "check_uncarried_specs.py": DEGRADED,
}


def _exists(root: Path, rel: str, ref: str = "HEAD") -> bool:
    """Does `rel` exist at `ref`? Read from git, not the working tree.

    ⚠️ The working tree would answer about the tree the author is editing; the
    verdict is about the COMMITTED state, which is what CI and every other
    session sees.
    """
    rc = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{ref}:{rel}"],
                        capture_output=True).returncode
    return rc == 0


def declared_subjects(path: Path) -> Optional[dict[str, set[str]]]:
    """{CONSTANT_NAME: {repo paths}} from MODULE-LEVEL assignments only.

    ⚠️ `tree.body` AND NOT `ast.walk(tree)`, AND THAT ONE WORD IS THE WHOLE
    INSTRUMENT. Walking the tree reaches literals inside `self_test()`, which are
    FIXTURES a guard is supposed to plant at paths that do not exist. Measured:
    the walking version returns 29 of 69 and is dominated by them; this returns
    19 of 95 with none.

    Returns None when the file cannot be parsed — *we could not look*, which the
    caller reports rather than scoring.
    """
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except SyntaxError:
        return None
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not names or node.value is None:
            continue
        for lit in ast.walk(node.value):
            if not (isinstance(lit, ast.Constant) and isinstance(lit.value, str)):
                continue
            v = lit.value
            if v.startswith(REPO_PREFIXES) and "*" not in v and not v.endswith("/"):
                out.setdefault(names[0], set()).add(v)
    return out


def classify(root: Path, path: Path) -> tuple[str, list[str], int]:
    """(state, absent_subjects, total_subjects) for one guard."""
    subs = declared_subjects(path)
    if subs is None:
        return NO_SUBJECT, [], 0
    allp: set[str] = set()
    for vals in subs.values():
        allp |= vals
    if not allp:
        return NO_SUBJECT, [], 0
    absent = sorted(p for p in allp if not _exists(root, p))
    if not absent:
        return LIVE, [], len(allp)
    if len(absent) == len(allp):
        return DEAD, absent, len(allp)
    return DEGRADED, absent, len(allp)


def sweep(root: Path) -> tuple[dict[str, tuple[str, list[str], int]], list[str]]:
    """({guard_filename: (state, absent, total)}, notes)."""
    notes: list[str] = []
    files: list[Path] = []
    for g in GUARD_GLOBS:
        files.extend(sorted(root.glob(g)))
    # A guard may match both globs only if the dirs nest; they do not. Dedupe by
    # name anyway, because the BASELINE is keyed on the filename.
    seen: dict[str, Path] = {}
    for f in files:
        seen.setdefault(f.name, f)
    result = {name: classify(root, p) for name, p in seen.items()}
    notes.append(
        f"population: {len(result)} guard script(s) matching {', '.join(GUARD_GLOBS)}; "
        f"every module-level repo-path constant tested with `git cat-file -e HEAD:<path>`")
    return result, notes


def report(result: dict[str, tuple[str, list[str], int]]) -> list[str]:
    """The human-readable sweep. Counts every state separately, always."""
    buckets: dict[str, list[str]] = {LIVE: [], DEGRADED: [], DEAD: [], NO_SUBJECT: []}
    for name, (state, _absent, _tot) in sorted(result.items()):
        buckets[state].append(name)
    out = [
        f"  {NO_SUBJECT}: {len(buckets[NO_SUBJECT])} — declares no module-level "
        f"repo path, so THIS INSTRUMENT CANNOT SPEAK TO IT. Not a pass; a gap in "
        f"coverage, and the largest count here.",
        f"  {LIVE}: {len(buckets[LIVE])} — every declared subject exists. "
        f"`live` means its subject is THERE, never that it works.",
        f"  {DEGRADED}: {len(buckets[DEGRADED])} — grades a SILENTLY NARROWER set.",
        f"  {DEAD}: {len(buckets[DEAD])} — grades NOTHING; its green means nothing.",
    ]
    for state in (DEAD, DEGRADED):
        if not buckets[state]:
            continue
        out.append("")
        out.append(f"  {state.upper()}:")
        for name in buckets[state]:
            _s, absent, tot = result[name]
            baselined = " [BASELINED]" if name in BASELINE_2026_09_22 else " ⚠️ NEW"
            out.append(f"    {name}{baselined} — {len(absent)} of {tot} subject(s) absent")
            for p in absent:
                out.append(f"        - {p}")
    return out


def findings(result: dict[str, tuple[str, list[str], int]]) -> list[str]:
    """What FAILS: a NEW dead/degraded guard, or a baseline entry that recovered."""
    fails: list[str] = []
    for name, (state, absent, tot) in sorted(result.items()):
        if state in (DEAD, DEGRADED) and name not in BASELINE_2026_09_22:
            fails.append(
                f"NEW {state.upper()}: {name} declares {len(absent)} of {tot} "
                f"subject(s) that do not exist at HEAD: {', '.join(absent)}.\n"
                f"      A check whose subject was removed reports a passing or "
                f"quiet state about a thing it cannot see. Either restore the "
                f"subject, re-point the guard at a surface that exists, or RETIRE "
                f"the guard with a stated reason — `killed` is a first-class "
                f"outcome and beats carrying a check that grades nothing.\n"
                f"      Do NOT silence this by adding the name to "
                f"BASELINE_2026_09_22: that list is the debt as of 2026-09-22 and "
                f"may only SHRINK.")
    for name, expected in sorted(BASELINE_2026_09_22.items()):
        if name not in result:
            fails.append(
                f"BASELINE STALE: {name} is listed as {expected} and no longer "
                f"exists as a guard. Remove the entry — a baseline that outlives "
                f"its subject accumulates slots nobody can audit.")
            continue
        state = result[name][0]
        if state in (LIVE, NO_SUBJECT):
            fails.append(
                f"BASELINE RECOVERED: {name} is baselined as {expected} and now "
                f"grades as {state} — its subject came back. REMOVE the entry. "
                f"The list may only shrink, and a stale exemption is how a "
                f"baseline becomes a permanent hole.")
    return fails


# --------------------------------------------------------------------------
# Self-test: plant each state in a throwaway repo and prove the call.
# --------------------------------------------------------------------------
def _self_test() -> int:
    import shutil
    import tempfile

    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'PASS' if passed else 'FAIL'}  {label}"
              + (f" — {detail}" if detail and not passed else ""))
        ok = ok and passed

    print("check_guard_liveness self-test")
    tmp = Path(tempfile.mkdtemp())
    try:
        root = tmp / "repo"
        (root / "scripts" / "ci").mkdir(parents=True)
        (root / "docs").mkdir()
        (root / "docs" / "present.json").write_text("{}\n")

        def guard(name: str, body: str) -> None:
            (root / "scripts" / "ci" / name).write_text(body)

        # live: its one subject exists.
        guard("check_live.py", 'SUBJ = "docs/present.json"\n')
        # dead: its only subject is gone.
        guard("check_dead.py", 'SUBJ = "docs/archived.json"\n')
        # degraded: one of two gone.
        guard("check_degraded.py",
              'A = "docs/present.json"\nB = "docs/archived.json"\n')
        # no declared subject.
        guard("check_nosubj.py", 'THRESHOLD = 42\n')
        # ⚠️ THE FIXTURE CONTROL, and it is the whole instrument: a guard that
        # plants a non-existent path INSIDE a function must read `live`. The
        # naive ast.walk probe scores this `dead`, which is how it reached 29 of
        # 69 on the real tree.
        guard("check_fixture.py",
              'SUBJ = "docs/present.json"\n\n\n'
              'def self_test():\n'
              '    open("docs/planted-does-not-exist.json", "w").write("{}")\n')

        subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "s@e.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "s"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True,
                       capture_output=True)

        result, _notes = sweep(root)
        states = {k: v[0] for k, v in result.items()}
        check("P1 a guard whose only subject is archived reads DEAD",
              states.get("check_dead.py") == DEAD, str(states))
        check("P2 a guard with one of two subjects gone reads DEGRADED",
              states.get("check_degraded.py") == DEGRADED, str(states))
        check("N1 a guard whose subject exists reads LIVE",
              states.get("check_live.py") == LIVE, str(states))
        check("N2 a guard declaring no repo path reads NO_DECLARED_SUBJECT, "
              "not live", states.get("check_nosubj.py") == NO_SUBJECT, str(states))
        check("N3 THE FIXTURE CONTROL: a path planted inside a FUNCTION does not "
              "make the guard dead", states.get("check_fixture.py") == LIVE,
              f"got {states.get('check_fixture.py')} — the probe is walking into "
              f"function bodies again, which is the 29-of-69 false-positive class")

        # the four states are reported with SEPARATE counts, never folded
        text = "\n".join(report(result))
        for state in (LIVE, DEGRADED, DEAD, NO_SUBJECT):
            check(f"N4 `{state}` is reported as its own count",
                  state in text, text)

        # a NEW dead guard FAILS; baselined ones do not
        f = findings(result)
        check("P3 a dead guard NOT in the baseline is a FAILING finding",
              any("check_dead.py" in x and "NEW" in x for x in f), str(f))
        check("P3b ...and the finding refuses the cheap silencing",
              any("may only SHRINK" in x for x in f), str(f))

        saved = dict(BASELINE_2026_09_22)
        try:
            BASELINE_2026_09_22.clear()
            BASELINE_2026_09_22.update({"check_dead.py": DEAD,
                                        "check_degraded.py": DEGRADED})
            f2 = findings(result)
            check("N5 a BASELINED dead guard is not a failing finding",
                  not any("check_dead.py" in x and "NEW" in x for x in f2), str(f2))
            # a recovered baseline entry must FAIL rather than linger
            BASELINE_2026_09_22["check_live.py"] = DEAD
            f3 = findings(result)
            check("P4 a baseline entry whose subject came back FAILS",
                  any("RECOVERED" in x and "check_live.py" in x for x in f3), str(f3))
            BASELINE_2026_09_22.pop("check_live.py")
            BASELINE_2026_09_22["check_gone_entirely.py"] = DEAD
            f4 = findings(result)
            check("P5 a baseline entry naming a guard that no longer exists FAILS",
                  any("BASELINE STALE" in x for x in f4), str(f4))
        finally:
            BASELINE_2026_09_22.clear()
            BASELINE_2026_09_22.update(saved)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nself-test: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="also FAIL on the baselined debt. NOT the default: "
                         "failing all 19 on day one red-walls the repo and gets "
                         "the guard reverted rather than the debt fixed.")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    result, notes = sweep(REPO)
    if not result:
        print("guard-liveness: could_not_check — no guard script matched "
              f"{GUARD_GLOBS}. NOTHING was swept; this is not a pass.")
        return 2

    print("guard-liveness: a check must be able to say when it cannot see its subject")
    for n in notes:
        print(f"  · {n}")
    for line in report(result):
        print(line)

    baselined = sum(1 for k in BASELINE_2026_09_22 if k in result)
    print("")
    print(f"  baselined debt: {baselined} of {len(BASELINE_2026_09_22)} entries "
          f"still present — this list may only SHRINK. Every one is the "
          f"2026-09-21 operating reset's blast radius.")

    fails = findings(result)
    if args.strict:
        for name, (state, absent, tot) in sorted(result.items()):
            if state in (DEAD, DEGRADED) and name in BASELINE_2026_09_22:
                fails.append(f"--strict {state.upper()}: {name} "
                             f"({len(absent)} of {tot} subject(s) absent)")
    if fails:
        print("")
        print("A CHECK THAT CANNOT SEE ITS SUBJECT MUST SAY SO:")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("")
    print("  No NEW dead or degraded guard, and no baseline entry has recovered "
          "unnoticed. Read that as 'no regression', never as 'the fleet is "
          "healthy' — 8 guards grade nothing and 46 are outside this "
          "instrument's reach.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
