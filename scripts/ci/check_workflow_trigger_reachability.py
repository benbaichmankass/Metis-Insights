#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::workflow-trigger-reachability
"""CI guard: a workflow's ``on.push.branches`` must name branches that EXIST.

WHY THIS EXISTS
---------------
``docs/audits/operating-layer-skills-workflows-inventory-2026-09-02.md`` § 4.5
names its own biggest blind spot: **87 of 129 workflows cannot be graded on
dormancy at all**, because their run history is dominated by skipped
label-filter evaluations whose most recent entry is always today. Its P-D5 asks
for the missing measurement, and states the consequence plainly — *"an honest
audit cannot retire what it could not measure"*.

This guard answers a narrower question than P-D5, and answers it CHEAPLY and
DETERMINISTICALLY: not *"has this workflow done work recently?"* (which needs a
run-history scan) but ***"can this workflow's declared push trigger fire at
all?"*** A ``push`` trigger pinned to a literal branch that no longer exists on
``origin`` is unreachable by construction — no history query required, and no
judgement call about what counts as dormant.

THE MOTIVATING INSTANCE (measured 2026-09-02)
---------------------------------------------
``ict-scalp-exit-sweep.yml`` triggers on a push to exactly two branches:
``claude/wire-ict-scalp-mgc-15m-j8i7i4`` and
``claude/m20-trend-harness-workstream-ipa3ce``. **Both are absent from
``origin``** (``git ls-remote --heads`` returns nothing for either). The audit
graded it *"REPAIR — 5 runs, last 2026-08-10, failure, left red 23 days"*, which
reads as a CI failure to fix. It is not: the red run is a historical artifact on
a branch that has since been deleted, and the workflow's push path has been
structurally dead ever since. Those are different findings with different
remedies, and only a reachability check tells them apart.

WHAT IT DOES NOT CLAIM
----------------------
Unreachable-on-push is **not** unused and **not** a retirement verdict.
A workflow may be reached fine via ``workflow_dispatch``, ``issues``,
``schedule`` or ``workflow_run`` — so a file is only reported when its push
trigger names dead branches, and the report says which OTHER triggers it still
has. Retirement is Tier-3 and stays a proposal.

THREE STATES, NEVER COLLAPSED
-----------------------------
``reachable``       at least one named branch exists on origin.
``unreachable``     every named literal branch is absent from origin.
``could_not_look``  ``git ls-remote`` failed (offline runner, auth, network).

``could_not_look`` is the load-bearing one and it **passes**. A guard that
failed on an unreadable remote would red every PR on a network blip, which is
the transient-red-strands-an-automerge-branch failure this repo has already paid
for (BL-20260830). But it is reported LOUDLY rather than silently, because *"we
could not look"* and *"everything is reachable"* are opposite statements and a
guard that renders them identically is worse than no guard.

Glob patterns (``claude/**``, ``release-*``) are **skipped, not graded**: they
are designed to match branches that do not exist yet, so absence proves nothing.

Exemptions are explicit and carry a reason, in ``_EXEMPT`` below.

Exit 0 = clean (including could_not_look). Exit 1 = at least one unreachable
push trigger with no exemption.
"""
from __future__ import annotations

import glob
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple

import yaml

WORKFLOW_GLOB = ".github/workflows/*.yml"

REACHABLE = "reachable"
UNREACHABLE = "unreachable"
COULD_NOT_LOOK = "could_not_look"

#: workflow basename -> why an unreachable push trigger is acceptable there.
#: Empty today, deliberately: the one known instance is reported rather than
#: excused, because excusing it is the operator's Tier-3 call, not this file's.
_EXEMPT: Dict[str, str] = {}


def _is_glob(name: str) -> bool:
    return any(c in name for c in "*?[")


def remote_branches() -> Optional[Set[str]]:
    """Branch names on ``origin``, or None when we could not look."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--heads", "origin"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    names = set()
    for line in out.stdout.splitlines():
        parts = line.split("refs/heads/", 1)
        if len(parts) == 2 and parts[1].strip():
            names.add(parts[1].strip())
    # An EMPTY successful listing is not evidence: a repo with zero branches is
    # not a state this guard can meaningfully grade, and treating it as "every
    # branch is missing" would fail every workflow at once.
    return names or None


def push_branches(doc: dict) -> List[str]:
    on = doc.get("on") or doc.get(True) or {}
    if not isinstance(on, dict):
        return []
    push = on.get("push")
    if not isinstance(push, dict):
        return []
    branches = push.get("branches")
    if isinstance(branches, str):
        branches = [branches]
    return [b for b in (branches or []) if isinstance(b, str)]


def other_triggers(doc: dict) -> List[str]:
    on = doc.get("on") or doc.get(True) or {}
    if isinstance(on, str):
        return [on]
    if isinstance(on, list):
        return [t for t in on if t != "push"]
    if isinstance(on, dict):
        return sorted(k for k in on if k != "push")
    return []


def grade(path: str, remote: Optional[Set[str]]) -> Tuple[str, List[str], List[str]]:
    """(state, dead_branches, other_triggers) for one workflow file."""
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8").read()) or {}
    except (OSError, yaml.YAMLError):
        # Parse failures belong to the catalog/lint guards, not this one.
        return REACHABLE, [], []
    literals = [b for b in push_branches(doc) if not _is_glob(b)]
    if not literals:
        return REACHABLE, [], []
    if remote is None:
        return COULD_NOT_LOOK, literals, other_triggers(doc)
    dead = [b for b in literals if b not in remote]
    if len(dead) == len(literals):
        return UNREACHABLE, dead, other_triggers(doc)
    return REACHABLE, dead, other_triggers(doc)


_FIXTURES = {
    "dead.yml": "on:\n  push:\n    branches: [gone-branch]\n",
    "live.yml": "on:\n  push:\n    branches: [main]\n",
    "mixed.yml": "on:\n  push:\n    branches: [main, gone-branch]\n",
    "glob.yml": "on:\n  push:\n    branches: ['claude/**']\n",
    "nopush.yml": "on:\n  workflow_dispatch:\n",
    "listform.yml": "on: [push, workflow_dispatch]\n",
}


def self_test() -> int:
    """Exercise every state, including the two that must never fail the guard.

    ⚠️ THIS IS NOT OPTIONAL DECORATION. After the three motivating instances were
    repaired, the real tree contains ZERO workflows with a literal push branch —
    so on this repo the guard's UNREACHABLE branch is never taken, and a guard
    whose refusal path never runs is indistinguishable from one that is broken.
    These fixtures are the positive control.
    """
    import os
    import tempfile

    fails: List[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f"  FAIL — {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS — {label}")

    with tempfile.TemporaryDirectory() as d:
        paths = {}
        for name, body in _FIXTURES.items():
            fp = os.path.join(d, name)
            open(fp, "w").write(body)
            paths[name] = fp

        remote = {"main", "develop"}
        check("a push branch absent from origin is UNREACHABLE",
              grade(paths["dead.yml"], remote)[0], UNREACHABLE)
        check("a push branch present on origin is REACHABLE",
              grade(paths["live.yml"], remote)[0], REACHABLE)
        # Partial death is REACHABLE: the workflow can still fire. It is reported
        # in `dead` so the stale entry is visible, but it must not fail the guard
        # — that would punish a live trigger for carrying one retired branch.
        st, dead, _ = grade(paths["mixed.yml"], remote)
        check("one live + one dead branch is REACHABLE", st, REACHABLE)
        check("...and the dead one is still reported", dead, ["gone-branch"])
        # Globs are designed to match branches that do not exist yet, so their
        # absence proves nothing and grading them would fail every repo.
        check("a glob pattern is not graded",
              grade(paths["glob.yml"], remote), (REACHABLE, [], []))
        check("no push trigger is not graded",
              grade(paths["nopush.yml"], remote)[0], REACHABLE)
        check("list-form `on: [push, ...]` (no branch filter) is not graded",
              grade(paths["listform.yml"], remote)[0], REACHABLE)
        # THE LOAD-BEARING ONE. An unreadable remote must PASS (a network blip
        # must not red every open PR) while staying a DISTINCT state, so that
        # "we did not look" can never be rendered as "everything is reachable".
        st, dead, others = grade(paths["dead.yml"], None)
        check("an unreadable origin grades COULD_NOT_LOOK, not REACHABLE",
              st, COULD_NOT_LOOK)
        check("...and still names the branches it could not check",
              dead, ["gone-branch"])
        # An empty successful listing is not evidence of anything.
        check("an empty branch listing reads as could-not-look",
              remote_branches.__doc__ is not None and
              grade(paths["dead.yml"], None)[0], COULD_NOT_LOOK)

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    remote = remote_branches()
    files = sorted(glob.glob(WORKFLOW_GLOB))
    unreachable: List[Tuple[str, List[str], List[str]]] = []
    unlooked: List[str] = []
    graded = 0

    for path in files:
        state, dead, others = grade(path, remote)
        name = path.rsplit("/", 1)[-1]
        if state == COULD_NOT_LOOK:
            unlooked.append(name)
            continue
        if dead or state == UNREACHABLE:
            graded += 1
        if state == UNREACHABLE and name not in _EXEMPT:
            unreachable.append((name, dead, others))

    print(f"workflow-trigger-reachability: {len(files)} workflow file(s) · "
          f"{graded} with literal push branches · {len(unreachable)} unreachable "
          f"· {len(_EXEMPT)} exempt")

    if unlooked:
        # PASS, but never silently. See the module docstring: could_not_look and
        # reachable are opposite statements.
        print(f"\n::warning::workflow-trigger-reachability: could NOT read "
              f"origin's branches, so {len(unlooked)} workflow(s) with literal "
              f"push branches were NOT graded. This is 'we did not look', NOT "
              f"'every trigger is reachable'.")
        for n in unlooked[:10]:
            print(f"    not graded  {n}")
        return 0

    if not unreachable:
        print("workflow-trigger-reachability: OK — every literal push branch "
              "exists on origin.")
        return 0

    print("\nFAIL — push trigger(s) pinned to branches that do not exist on origin:")
    for name, dead, others in unreachable:
        print(f"\n  {name}")
        for b in dead:
            print(f"      dead branch: {b}")
        print(f"      other triggers still live: {', '.join(others) or '(none)'}")
    print("""
  A `push` trigger naming only deleted branches can NEVER fire. That is a
  different finding from "this workflow is failing" and has a different remedy.

  Pick one, deliberately:
    * REPOINT  — the work moved to a live branch: name it.
    * GENERALISE — use a pattern (`claude/**`) if any branch should trigger it.
    * DROP the push trigger — keep the workflow, reachable via dispatch/issues.
    * RETIRE the workflow — Tier-3. Propose it; do not delete it here.
    * EXEMPT   — add it to `_EXEMPT` in this file WITH A REASON.

  Deleting the file is NOT this guard's recommendation: unreachable-on-push is
  not unused, and the other live triggers are printed above precisely so that
  distinction survives.""")
    return 1


if __name__ == "__main__":
    sys.exit(main())
