#!/usr/bin/env python3
"""A run is not done until its rows are READABLE from the shared branch.

WHY THIS EXISTS
---------------
Operator directive, 2026-08-27: *"more consistent, higher frequency training and
backtesting … with actual usable results that are recorded correctly."*

The 2026-08-27 architecture review
(``docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md``) found the same
defect three times, in three well-built and honestly-documented components: the
compute was moved to the right tool, and the RESULT never arrived.

  1. ``e35-bracket-sweep.yml`` — a corpus job to commit per-cell rows shipped
     2026-08-23; the sweep RAN 2026-08-26; ``docs/research/m20-sweep-corpus.jsonl``
     is still 1,379 rows with **zero** cells naming a stop.
  2. ``trainer-offload-train.yml`` — trains on a runner, then ``--no-register``.
  3. The M20 coverage matrix — 468 verdicts whose conditioning defects live in
     the backlog, not the matrix.

None was noticed by its own workflow, because **a job exiting 0 is treated as
done**. This asserts the other half:

    A job that exits 0 having landed nothing is a FAILED job.

WHAT IT CHECKS, AND WHERE IT LOOKS
----------------------------------
It reads the store **from the shared ref** (``origin/main`` by default) via
``git show``, **not** from the working tree. That distinction is the whole
point: the working tree always contains the rows the job just wrote, so
checking it would pass in exactly the case this exists to catch — the commit
that never pushed, the push that went to a side branch, the extractor that
emitted nothing.

THREE STATES, NEVER COLLAPSED
-----------------------------
(``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states".)

  ``landed``         — present on the SHARED ref. A session can read them.
  ``pending_merge``  — present on the ref the job PUSHED to, but that ref is not
                       the shared one. **This is the e35 state, and it is why
                       the fourth state exists.** ``e35-bracket-sweep.yml``
                       retargets its corpus push to a side branch whenever it is
                       dispatched on the protected default branch, and its own
                       notice says *"A HUMAN OR A SESSION must open the PR — this
                       job cannot, and no workflow will fire from its push."*
                       Nobody did. Collapsing this into ``landed`` would call the
                       run a success when no session can read its rows;
                       collapsing it into ``absent`` would blame the job for work
                       it actually did. Neither is true, and they need different
                       follow-ups — open the PR, versus fix the run.
  ``absent``         — readable, and the rows are on NEITHER ref. The finding.
  ``could_not_read`` — we could not obtain a ref or parse the store. **NOT a
                       pass**, and deliberately not folded into ``absent``
                       either: "the rows are missing" and "we never looked" call
                       for opposite follow-ups, and only one is a bug in the run.

Exit codes: 0 ``landed`` · **1 ``pending_merge``** · 1 ``absent`` · 2
``could_not_read``. ``pending_merge`` is deliberately NON-ZERO: the run's rows
are not readable, which is the condition this tool exists to make loud. It is
reported under its own name so the fix is obvious.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

LANDED = "landed"
PENDING_MERGE = "pending_merge"
ABSENT = "absent"
COULD_NOT_READ = "could_not_read"


def read_store_at_ref(ref: str, path: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Return (rows, note). rows is None when the ref/store could not be read."""
    try:
        out = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git show failed: {exc}"
    if out.returncode != 0:
        return None, f"git show {ref}:{path} exited {out.returncode}: {out.stderr.strip()[:200]}"

    rows: list[dict[str, Any]] = []
    for i, line in enumerate(out.stdout.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            return None, f"{path} line {i} is not JSON: {exc}"
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, f"read {len(rows)} row(s) from {ref}:{path}"


def _matches(row: dict[str, Any], field: str, contains: str | None) -> bool:
    if field not in row:
        return False
    if contains is None:
        return True
    return contains in str(row.get(field))


def _count(rows: list[dict[str, Any]] | None, field: str,
           contains: str | None) -> int | None:
    if rows is None:
        return None
    return sum(1 for r in rows if _matches(r, field, contains))


def check(
    rows: list[dict[str, Any]] | None,
    *,
    field: str,
    contains: str | None,
    min_rows: int,
    pushed_rows: list[dict[str, Any]] | None = None,
    same_ref: bool = True,
) -> tuple[str, int, str]:
    """Return (state, matched_on_shared, human note). Pure — no I/O, so testable.

    ``pushed_rows`` is the store as it stands on the ref the JOB PUSHED to, when
    that differs from the shared ref. It is what separates ``pending_merge``
    from ``absent``: without it, a correctly-working job whose push was
    retargeted is indistinguishable from a job that produced nothing.
    """
    what = f"rows with `{field}`" + (f" containing '{contains}'" if contains else "")
    shared = _count(rows, field, contains)
    if shared is None:
        return COULD_NOT_READ, 0, "the shared ref could not be read — this is NOT a pass"
    if shared >= min_rows:
        return LANDED, shared, f"{shared} {what} (need >= {min_rows}) of {len(rows or [])} total"

    if not same_ref:
        pushed = _count(pushed_rows, field, contains)
        if pushed is None:
            return COULD_NOT_READ, shared, (
                "absent from the shared ref, and the pushed ref could not be read — "
                "we cannot tell a retargeted push from a job that produced nothing"
            )
        if pushed >= min_rows:
            return PENDING_MERGE, shared, (
                f"{pushed} {what} are on the PUSHED ref but only {shared} on the shared "
                f"ref — the job did its part and the rows are NOT readable by a session. "
                f"Open the PR that merges them; nothing downstream will do it"
            )
    return ABSENT, shared, (
        f"only {shared} {what} (need >= {min_rows}) among {len(rows or [])} total rows — "
        f"the run produced output that did not reach the shared ref"
    )


def _self_test() -> int:
    fails = []
    R = [{"cell": "sm2.0_tp3", "leg": "a"}, {"cell": "trail4", "leg": "b"}]

    st, n, _ = check(R, field="cell", contains="sm", min_rows=1)
    if st != LANDED or n != 1:
        fails.append(f"control 1: a present row should be `landed`, got {st}/{n}")

    st, n, _ = check(R, field="cell", contains="sm", min_rows=5)
    if st != ABSENT:
        fails.append(f"control 2: too few matches should be `absent`, got {st}")

    st, _, _ = check(R, field="nope", contains=None, min_rows=1)
    if st != ABSENT:
        fails.append(f"control 3: a missing FIELD should be `absent`, got {st}")

    st, _, _ = check(None, field="cell", contains="sm", min_rows=1)
    if st != COULD_NOT_READ:
        fails.append(f"control 4: an unreadable store must be `could_not_read`, got {st}")

    # The load-bearing one: an unreadable store must NOT be reported as landed,
    # and must NOT be collapsed into `absent` either.
    if COULD_NOT_READ in (LANDED, ABSENT):
        fails.append("control 5: the three states are not distinct")

    st, _, _ = check([], field="cell", contains="sm", min_rows=1)
    if st != ABSENT:
        fails.append(f"control 6: an EMPTY store should be `absent`, got {st}")

    # The e35 shape: absent from shared, PRESENT on the ref the job pushed to.
    st, n, _ = check([], field="cell", contains="sm", min_rows=1,
                     pushed_rows=R, same_ref=False)
    if st != PENDING_MERGE:
        fails.append(f"control 8: rows on the pushed ref only must be "
                     f"`pending_merge`, got {st}")
    if n != 0:
        fails.append(f"control 8b: matched count must report the SHARED ref (0), got {n}")

    # ... and absent from BOTH is still `absent`, not pending_merge.
    st, _, _ = check([], field="cell", contains="sm", min_rows=1,
                     pushed_rows=[], same_ref=False)
    if st != ABSENT:
        fails.append(f"control 9: absent from both refs must stay `absent`, got {st}")

    # An unreadable PUSHED ref must not silently become `absent` — we did not look.
    st, _, _ = check([], field="cell", contains="sm", min_rows=1,
                     pushed_rows=None, same_ref=False)
    if st != COULD_NOT_READ:
        fails.append(f"control 10: an unreadable pushed ref must be "
                     f"`could_not_read`, got {st}")

    # A real ref that does not exist must surface as could_not_read, not a pass.
    rows, _ = read_store_at_ref("refs/does/not/exist", "nope.jsonl")
    if rows is not None:
        fails.append("control 7: a bogus ref returned rows instead of None")

    if fails:
        for f in fails:
            print(f"::error::self-test: {f}")
        return 1
    print("assert-rows-landed: self-test OK — 10 planted controls all fire")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--store", help="path to the JSONL results store, as committed")
    ap.add_argument("--ref", default="origin/main",
                    help="the SHARED ref to read from (default origin/main). Never the "
                         "working tree — that always contains what the job just wrote.")
    ap.add_argument("--field", default="cell",
                    help="row field that identifies this run's output")
    ap.add_argument("--contains", default=None,
                    help="substring the field must contain (omit = field need only exist)")
    ap.add_argument("--min-rows", type=int, default=1)
    ap.add_argument("--pushed-ref", default=None,
                    help="the ref this job actually pushed to, when the workflow may "
                         "retarget away from the shared branch (e35 does exactly this "
                         "on a protected-branch dispatch). Supplying it is what lets a "
                         "retargeted-but-successful run report `pending_merge` instead "
                         "of being blamed as `absent`.")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.store:
        print("::error::--store is required")
        return 2

    rows, note = read_store_at_ref(args.ref, args.store)
    pushed_rows, pushed_note, same_ref = None, "", True
    if args.pushed_ref and args.pushed_ref != args.ref:
        same_ref = False
        pushed_rows, pushed_note = read_store_at_ref(args.pushed_ref, args.store)
    state, _matched, detail = check(
        rows, field=args.field, contains=args.contains, min_rows=args.min_rows,
        pushed_rows=pushed_rows, same_ref=same_ref,
    )
    print(f"assert-rows-landed: {state} — {detail}")
    print(f"  shared_ref={args.ref} store={args.store} ({note})")
    if not same_ref:
        print(f"  pushed_ref={args.pushed_ref} ({pushed_note})")
    if state == LANDED:
        return 0
    if state == PENDING_MERGE:
        print("::error::the rows landed on the ref this job pushed to, but that ref is "
              "NOT the shared branch — so no session can read them. This job cannot "
              "open its own PR (a GITHUB_TOKEN push starts no workflows), so the merge "
              "is owed by a human or a session. Non-zero deliberately: an unreadable "
              "result is not a success.")
        return 1
    if state == ABSENT:
        print("::error::the run's rows are NOT on the shared ref. A job that exits 0 "
              "having landed nothing is a FAILED job — check that the commit was "
              "created, that the push targeted the shared branch, and that the "
              "extractor emitted rows at all.")
        return 1
    print("::error::could not read the store — this is an ABSENT result, not a clean one.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
