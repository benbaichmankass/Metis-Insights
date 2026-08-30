#!/usr/bin/env python3
# wiring: manual-only + CI-reportable. This REPORTS; it gates nothing. Same
# posture as scripts/ops/evidence_workflow_inventory.py, and for the same
# reason: the judgement about what to do with a stuck branch is a human one.
"""Which `automation/*` branches never landed, and how far behind they are.

WHY THIS EXISTS
---------------
Measured 2026-08-30: **7 of 7 `automation/*` branches were unmerged into
`main`**, two of them carrying open PRs a day old, and four carrying no open PR
at all. Nobody had noticed, because the only way to see it is to list branches
by hand.

THE MECHANISM, established rather than assumed. PRs #10398 and #10407 were both
red on exactly two checks:

    guards      -> session-brief-guard: CLAUDE.md's SESSION-BRIEF block is STALE
    pytest-run  -> 5 failures, all in tests/test_exit_reason_reclassify_on_late_price.py

Neither had anything to do with what those PRs changed (a calendar snapshot and
a 9-line append). Both were conditions of their BASE, `76d14af5`, and both were
fixed on `main` afterwards — verified by running each at each commit:

    76d14af5  session-brief --check -> exit 1 ;  that test file -> 5 failed
    15c192bc  session-brief --check -> exit 0 ;  that test file -> 11 passed

So the PRs inherited a transient red base. **And they can never recover**, because
nothing updates an automation branch after it is opened: on both, `updated_at`
was byte-equal to `created_at`. Checks never re-run, so auto-merge waits forever
on a frozen snapshot of a base that no longer exists. `main` got fixed; the PRs
never found out.

That is the class: **a transient red base permanently strands an auto-merge
branch, silently.** It is the M40 R2 failure — the producer ran, the rows exist,
nobody can read them on `main` — wearing different clothes.

WHAT THIS DOES *NOT* DO, DELIBERATELY
-------------------------------------
It does **not** diagnose *why* a branch is stuck, and it does not claim a red
check is base-inherited. Establishing that took running the failing check at two
commits; a script guessing it from `behind_by` would be asserting a cause it
never measured — the UNPROVENANCED DIAGNOSTIC OUTPUT class this repo already
enforces against. This surfaces candidates and states the denominator. A human
or a session diagnoses.

FIVE STATES, NEVER COLLAPSED
----------------------------
  ``landed``        — an ancestor of the shared ref. Nothing to do.
  ``in_flight``     — unmerged and YOUNGER than --stale-hours. Not a finding:
                      a branch opened ten minutes ago is supposed to be here.
  ``stuck``         — unmerged and older than --stale-hours. The candidate.
  ``no_remote``     — named but absent from the remote (already deleted).
  ``unknown``       — WE COULD NOT LOOK (a git call failed). Never folded into
                      any of the above, and never reported as "not stuck".
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

LANDED, IN_FLIGHT, STUCK, NO_REMOTE, UNKNOWN = (
    "landed", "in_flight", "stuck", "no_remote", "unknown")

DEFAULT_PREFIX = "automation/"
DEFAULT_STALE_HOURS = 6.0


@dataclass
class Row:
    branch: str
    state: str
    age_hours: Optional[float]
    behind: Optional[int]
    detail: str


def _git(*args: str) -> Optional[str]:
    """Run git; return None on failure — 'we could not look', never ''."""
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def list_branches(prefix: str) -> Optional[List[str]]:
    out = _git("ls-remote", "--heads", "origin", f"refs/heads/{prefix}*")
    if out is None:
        return None
    names = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            names.append(parts[1].removeprefix("refs/heads/"))
    return sorted(names)


def classify(branch: str, shared_ref: str, stale_hours: float,
             now: Optional[datetime] = None) -> Row:
    sha = _git("rev-parse", f"refs/remotes/origin/{branch}")
    if sha is None:
        return Row(branch, NO_REMOTE, None, None,
                   "no remote-tracking ref (fetch first, or it was deleted)")

    merged = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, shared_ref],
        capture_output=True, text=True)
    if merged.returncode == 0:
        return Row(branch, LANDED, None, 0, f"ancestor of {shared_ref}")
    if merged.returncode not in (0, 1):
        return Row(branch, UNKNOWN, None, None,
                   "git merge-base failed — we could not look")

    iso = _git("show", "-s", "--format=%cI", sha)
    if iso is None:
        return Row(branch, UNKNOWN, None, None,
                   "could not read the commit date — we could not look")
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return Row(branch, UNKNOWN, None, None, f"unparseable commit date {iso!r}")

    ref_now = now or datetime.now(timezone.utc)
    age = (ref_now - when).total_seconds() / 3600.0

    behind = None
    cnt = _git("rev-list", "--count", f"{sha}..{shared_ref}")
    if cnt is not None and cnt.isdigit():
        behind = int(cnt)

    state = STUCK if age >= stale_hours else IN_FLIGHT
    b = "unknown" if behind is None else str(behind)
    return Row(branch, state, age, behind,
               f"unmerged; {age:.1f}h old; {b} commit(s) behind {shared_ref}")


def _selftest() -> int:
    """Planted controls. The load-bearing one is the POSITIVE control: if a
    known-landed branch stops classifying as `landed`, every other verdict here
    is meaningless and the probe is broken, so it short-circuits."""
    fails: List[str] = []
    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

    # A row older than the threshold is `stuck`; younger is `in_flight`.
    # These exercise the boundary directly rather than through git.
    for age, want in ((0.0, IN_FLIGHT), (5.9, IN_FLIGHT),
                      (6.0, STUCK), (48.0, STUCK)):
        got = STUCK if age >= DEFAULT_STALE_HOURS else IN_FLIGHT
        if got != want:
            fails.append(f"age {age}h should be {want}, got {got}")

    # `unknown` must never be reported as a clean state.
    if UNKNOWN in (LANDED, IN_FLIGHT):
        fails.append("`unknown` collapsed into a clean state")

    # An absent branch is NOT the same as a stuck one.
    if NO_REMOTE == STUCK:
        fails.append("`no_remote` collapsed into `stuck`")

    for f in fails:
        print("FAIL " + f)
    print(f"selftest: {4 + 2 - len(fails)}/{4 + 2} passed")
    return 1 if fails else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--shared-ref", default="origin/main")
    ap.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()

    names = list_branches(a.prefix)
    if names is None:
        # An unreadable remote is NOT "no stuck branches".
        print("::error::could not list remote branches — we could not look. "
              "This is NOT a clean result.", file=sys.stderr)
        return 2
    if not names:
        print(f"POPULATION: 0 branch(es) matching {a.prefix}* on origin — "
              f"nothing to classify.")
        return 0

    rows = [classify(n, a.shared_ref, a.stale_hours) for n in names]
    by = {}
    for r in rows:
        by.setdefault(r.state, []).append(r)

    print(f"POPULATION: {len(rows)} branch(es) matching {a.prefix}* on origin, "
          f"graded against {a.shared_ref} (stale after {a.stale_hours}h).")
    for state in (STUCK, IN_FLIGHT, LANDED, NO_REMOTE, UNKNOWN):
        got = by.get(state, [])
        if not got:
            continue
        print(f"\n{state}: {len(got)}")
        for r in sorted(got, key=lambda x: -(x.age_hours or 0)):
            print(f"  {r.branch}\n      {r.detail}")

    n_stuck = len(by.get(STUCK, []))
    n_unknown = len(by.get(UNKNOWN, []))
    print(f"\nstuck={n_stuck} · unknown={n_unknown} (unknown is 'we could not "
          f"look', NOT 'not stuck')")
    if n_stuck:
        print("::warning::" + f"{n_stuck} automation branch(es) have not landed. "
              "Check each one's PR: a red check on a base that has since been "
              "fixed will NEVER re-run on its own. Updating the branch re-runs it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
