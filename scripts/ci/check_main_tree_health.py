#!/usr/bin/env python3
# wiring: .github/workflows/main-tree-watch.yml (cron) + --self-test
"""Grade the DEFAULT BRANCH's tree on a cadence, because nothing else does.

WHY THIS EXISTS
---------------
``guards.yml`` carries ``push: branches: [main]``, so on paper every commit to
``main`` is graded. MEASURED 2026-09-17, twice, on disjoint populations:

  * by MERGE CREDENTIAL, over all 29 PR-merge commits in one window --
    user credential 5 of 5 produce a push run, ``GITHUB_TOKEN`` 0 of 24;
  * by COMMIT KIND, over a later 5.1h window -- PR merges 0 of 31,
    ``chore(ops)`` pushes 9 of 9.

An auto-merged PR's merge commit is created by ``GITHUB_TOKEN``, and GitHub
does not start a workflow run for it. So the repo's only post-merge check
skips precisely the commits that carry reviewed work.

⚠️ THE BLIND LANDINGS ARE LATE, NOT UNCOVERED, and this file must not be sold
as closing a hole that does not exist. ``main`` is linear, so a later push run
grades the whole tree including the earlier commit's content: all 24 blind
landings were eventually covered, median lag 34.4 min. What this bounds is the
GAP BETWEEN CONSECUTIVE GRADED RUNS, measured at up to **442.6 minutes**.

⚠️ AND THE REAL ARGUMENT IS CORRELATION, NOT LENGTH. That coverage is carried
almost entirely by ``chore(ops)`` automation pushes -- 95 of the last 100 push
runs -- which stop exactly when the repo is unhealthy. ``error-feed-digest``
landed 0 of 22 during the four-day due-list outage. The one mechanism grading
``main`` goes quiet precisely when ``main`` most needs grading. A SCHEDULE is
the only carrier that does not depend on other work happening.

⚠️ WHY NOT JUST SCHEDULE ``run_guards.py``. ``run_guards --base main`` on a
scheduled run OF ``main`` diffs ``main`` against itself, so every diff-scoped
guard passes over an empty diff -- a green that checked nothing, which this
repo calls the same sin as a red that checked nothing, and worse for trust.
Only the DIFF-INDEPENDENT rules can be honestly graded on a cadence, and those
are what this file runs.

⚠️ WHAT THIS CANNOT SEE, SAID HERE RATHER THAN DISCOVERED LATER. Each probe
below has sub-rules that need a ``--base`` and DO NOT RUN here -- R2/R3 of the
id guard. The tool
already SAYS SO in its own output (they print "no base given" rather than a
bare OK), so this file does not have to invent the caveat; it must simply not
overwrite it. A pass here is "the diff-independent rules found nothing", never
"main is healthy".

THE CALENDAR CLASS IS WHY A CADENCE IS NOT OPTIONAL
---------------------------------------------------
⚠️ 2026-09-22 (E45): the worked example below is HISTORY. The probe it describes
was removed when its register was archived, and no probe here covers the
calendar class today. The reasoning is kept because it is the argument for the
cadence itself, which still holds.

``check_open_items.py``'s 21-day affirmation window crossed on the CALENDAR,
with nobody's diff. MEASURED 2026-09-17: ``OI-20260826-JOURNAL-TRUST-COVERS-ONE-ACCOUNT``
reached 22 days against a limit of 21, turned ``main`` red with no commit
having done anything, and stranded ``econ-calendar-produce`` run #1953 -- whose
diff is a 62,369-line timestamped point-in-time capture from a DAILY producer,
so re-running does not recover it. A PR-time check cannot catch that class at
all: there is no PR.
"""
from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys

#: Three never-collapsed gradings. `could_not_run` is *we could not look* and
#: is NEVER folded into `clean` -- a probe that failed to execute proves
#: nothing about the tree, and reporting it as clean is the exact defect this
#: whole family of guards exists to prevent.
CLEAN = "clean"
FINDING = "finding"
COULD_NOT_RUN = "could_not_run"

#: Exit codes. Both non-zero states page, and they are DIFFERENT non-zero
#: values so the alert can say which happened -- the `stale-automation-sweep`
#: convention, where exit 2 means COULD NOT LOOK.
EXIT = {CLEAN: 0, FINDING: 1, COULD_NOT_RUN: 2}


@dataclasses.dataclass(frozen=True)
class Probe:
    name: str
    argv: tuple[str, ...]
    #: what it grades, and -- load-bearing -- what it does NOT grade here.
    covers: str


PROBES = (
    Probe("register-ids", ("python3", "scripts/ci/check_register_ids.py"),
          "whole-file id uniqueness (R1). R2/R3 need a --base and DO NOT run "
          "here; the tool says so itself."),
    Probe("backlog-refs", ("python3", "scripts/ops/check_backlog_refs.py", "--all"),
          "every tracking id in the tree resolves to a filed row."),
    # ⚠️ REMOVED 2026-09-22 (E45): the third probe was `open-items`, running
    # `scripts/ci/check_open_items.py` over `docs/claude/OPEN-ITEMS.json`. The
    # 2026-09-21 reset ARCHIVED that register, so from that date this cron was
    # reporting `register is MISSING` on every hourly run -- a red that measured
    # nothing, which § "could not measure is its own outcome" names as the same
    # sin as a green that measures nothing, and worse for trust. The guard is
    # retired; see
    # docs/archive/2026-09-21-operating-reset/guards/RETIRED-GUARDS-2026-09-22.md.
    #
    # ⚠️ AND THE CALENDAR CLASS IT COVERED IS NOW UNCOVERED HERE. Nothing in
    # this file reaches a rule that crosses on the calendar with nobody's diff.
    # Said rather than quietly dropped: the two probes below are diff-independent
    # but not calendar-driven, so a pass here is narrower than it was.
)


def grade(returncode: int | None, *, spawn_failed: bool = False) -> str:
    """Grade ONE probe's outcome.

    ⚠️ A probe that could not be executed at all, or that was killed, is
    `could_not_run` -- never `clean`. `returncode is None` is the timeout /
    no-answer case and is deliberately not truthy-tested, since 0 is falsy.
    """
    if spawn_failed or returncode is None:
        return COULD_NOT_RUN
    if returncode == 0:
        return CLEAN
    return FINDING


def overall(grades: list[str]) -> str:
    """Fold per-probe gradings into the run's verdict.

    ⚠️ ORDER IS LOAD-BEARING: a FINDING outranks a COULD_NOT_RUN, because a
    real red must not be downgraded to "we could not look" by an unrelated
    probe failing. And an EMPTY probe list is `could_not_run`, never `clean` --
    grading nothing is not a clean tree.
    """
    if not grades:
        return COULD_NOT_RUN
    if FINDING in grades:
        return FINDING
    if COULD_NOT_RUN in grades:
        return COULD_NOT_RUN
    return CLEAN


def run_probe(p: Probe, *, timeout: int = 300) -> tuple[str, str]:
    try:
        r = subprocess.run(list(p.argv), capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return COULD_NOT_RUN, f"timed out after {timeout}s"
    except OSError as exc:
        return COULD_NOT_RUN, f"could not execute: {type(exc).__name__}: {exc}"
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(ln for ln in out.strip().splitlines()[-4:])
    return grade(r.returncode), tail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    grades: list[str] = []
    print(f"main-tree-watch: {len(PROBES)} diff-independent probe(s)\n")
    for p in PROBES:
        g, tail = run_probe(p)
        grades.append(g)
        print(f"--- {p.name}: {g.upper()}")
        print(f"    covers: {p.covers}")
        for line in tail.splitlines():
            print(f"    | {line}")
        print()

    verdict = overall(grades)
    n = {s: grades.count(s) for s in (CLEAN, FINDING, COULD_NOT_RUN)}
    print("=" * 70)
    print(f"main-tree-watch: {verdict.upper()} — {len(PROBES)} probe(s): "
          f"{n[CLEAN]} clean, {n[FINDING]} finding, {n[COULD_NOT_RUN]} could-not-run")
    print("⚠️ THIS IS NOT A STATEMENT THAT `main` IS HEALTHY. Only the "
          "diff-independent rules ran; every diff-scoped rule (id-guard R2/R3, "
          "observation-preservation, and the whole of run_guards' diff-scoped "
          "set) was NOT evaluated, because a scheduled run of `main` diffs it "
          "against itself and would pass them vacuously.")
    if verdict == FINDING:
        print("::error::main-tree-watch: the DEFAULT BRANCH has a finding in a "
              "diff-independent rule. Every PR in the repo is red until it is fixed.")
    elif verdict == COULD_NOT_RUN:
        print("::error::main-tree-watch: a probe COULD NOT RUN. This is *we did "
              "not look*, not a clean tree, and it pages for that reason.")
    return EXIT[verdict]


def _self_test() -> int:
    fails = []

    def ck(label, got, want):
        if got != want:
            fails.append(f"{label}: got {got!r} want {want!r}")

    # grade() — the three states, and the two that are easy to collapse
    ck("exit 0 is clean", grade(0), CLEAN)
    ck("exit 1 is a finding", grade(1), FINDING)
    ck("exit 2 is a finding", grade(2), FINDING)
    ck("a timeout is could_not_run, NOT clean", grade(None), COULD_NOT_RUN)
    ck("a spawn failure is could_not_run, NOT clean",
       grade(0, spawn_failed=True), COULD_NOT_RUN)

    # overall() — the fold, including the two orderings that matter
    ck("all clean folds to clean", overall([CLEAN, CLEAN]), CLEAN)
    ck("a finding outranks a could_not_run",
       overall([COULD_NOT_RUN, FINDING]), FINDING)
    ck("a could_not_run outranks a clean",
       overall([CLEAN, COULD_NOT_RUN]), COULD_NOT_RUN)
    ck("grading NOTHING is could_not_run, never clean", overall([]), COULD_NOT_RUN)

    # the exit map — both non-zero states must page, and be distinguishable
    ck("clean exits 0", EXIT[CLEAN], 0)
    if EXIT[FINDING] == 0 or EXIT[COULD_NOT_RUN] == 0:
        fails.append("a non-clean verdict exits 0 — it would never page")
    if EXIT[FINDING] == EXIT[COULD_NOT_RUN]:
        fails.append("finding and could_not_run share an exit code — the alert "
                     "cannot say which happened")

    # the probe set is non-empty and every probe declares what it does NOT cover
    if not PROBES:
        fails.append("no probes registered — the watcher would grade nothing "
                     "and exit could_not_run forever")
    for p in PROBES:
        if not p.covers.strip():
            fails.append(f"{p.name} declares no coverage statement")

    print(f"main-tree-watch self-test: {len(fails)} failure(s)")
    for f in fails:
        print(f"  FAIL {f}")
    if not fails:
        print("  all planted controls fire")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
