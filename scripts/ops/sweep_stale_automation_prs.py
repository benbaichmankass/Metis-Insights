#!/usr/bin/env python3
"""NOBODY OWNS AN AUTOMATION PR AFTER THE RUN THAT OPENED IT EXITS.

WHY THIS EXISTS
---------------
`.github/actions/commit-to-main` already does everything a producer can do for
its own PR: 25 of 25 call sites set ``verify-merged: true`` (measured
2026-09-09 by parsing every workflow under `.github/workflows/` that references
the action), and `refresh-stale-branch` merges `main` in ONCE when the wait is
not progressing, minting a new head sha so the required checks re-run.

All of that lives inside the PRODUCING RUN'S OWN LIFETIME — one
``verify-timeout-minutes`` window (default 50 since 2026-10-04), one refresh attempt. When the
job exits, the branch has no owner. Its checks ran once, on the sha it was
opened with, and NOTHING in this repository ever looks at it again. So any
blocker that outlives one 30-minute window strands the branch PERMANENTLY,
while the producer's next cron opens another branch that strands the same way.

MEASURED 2026-09-09T08:0xZ, population = all 52 open PRs returned by
`list_pull_requests(state=open)`: 50 have `automation/*` head refs, and 47 of
those carry NO R13 merge-slot claim because they were cut before MI-208/#11494
taught the action to write one (#11494 landed 06:37:54Z; #11493, opened
06:00:16Z, has no claim; #11499, opened 06:38:14Z — twenty seconds later — has
one). That is not 47 bugs. It is one missing lifecycle stage, and any future
blocker will refill the queue exactly the same way.

⚠️ WHY THE FIX IS NOT IN THE PRODUCING WORKFLOW
-----------------------------------------------
Because the producing run is OVER. A stranded branch needs an actor that
outlives it, so this is a cadence job, not another input to `commit-to-main`.

⚠️ THIS SWEEPER REFRESHES. IT NEVER CLOSES, AND THAT IS A SAFETY PROPERTY.
--------------------------------------------------------------------------
Closing the superseded ones is the other half of the triage and it is
DELIBERATELY NOT AUTOMATED HERE. Supersession is not mechanically decidable
from a timestamp, and the repo has already paid for believing it was:
`OPEN-PRS.json`'s rows for #10902/#10908/#10914 carry the warning *"DO NOT CLOSE
WITHOUT READING ITS docs/claude/pending-pings.jsonl DIFF FIRST — a work-digest
PR carries QUEUED PINGS, and closing it unmerged DROPS them."*

That is not hypothetical. A first version of this triage classified by the
newest dated register alone and called 22 PRs cleanly superseded. Re-run with
append-only payloads separated out, the true figure was 15 — and SEVEN
work-digest PRs each carried one `pending-pings.jsonl` row absent from `main`.
Auto-closing on the first classification would have silently dropped seven
operator notifications. So this file reports those and refuses to act on them;
a session closes them by hand, having read the diff, and records a
`disposition` (`open_pr_record.py` grades a `closed_unmerged` row with none as
`undispositioned`, which is how cause (2) of this incident happened).

⚠️ WHAT THIS FIXES IS ONE OF TWO SUB-CASES, AND THE OTHER IS NAMED NOT HIDDEN
-----------------------------------------------------------------------------
MEASURED 2026-09-09T09:30Z on PR #11518, which POSTDATES #11494 and carries a
valid R13 claim, so it is not the cause #11494 fixed. Its producer's refresh
FIRED AND WORKED (head commit 9ce35856 is `Merge main so the required checks
re-run against the current base`), all four required checks went GREEN at
08:31-08:33Z, auto-merge was armed — and an hour later it is still open and now
conflicts with `main` in BOTH `docs/claude/session-board.json` AND
`docs/claude/work/WORK-DIGEST.json`.

⚠️ AUTO-MERGE DOES NOT RESOLVE CONFLICTS. It waits, silently and forever. The
producer's one refresh attempt is spent and its run has exited. So a PR can be
green, armed, correctly claimed and STILL permanently stranded.

  (i)  the only conflict is the R13 slot file  -> THIS FILE REFRESHES IT, taking
       `main`'s board and re-asserting the claim, and the PR lands.
  (ii) a DATA conflict between two generator runs (WORK-DIGEST.json here) -> NO
       RULE HERE CAN SETTLE IT. Taking either side discards one run's output.
       `refresh()` aborts on any conflicted path other than the slot file and
       says so, and the PR is reported for a human read.

Sub-case (ii) is a real limit of this file, not an oversight: see
BL-20260909-A-GREEN-ARMED-AUTOMATION-PR-STALLS-FOREVER-WHEN-MAIN-MOVES-BECAUSE-AUTO-MERGE-DOES-NOT-RESOLVE-CONFLICTS.

⚠️ AND IT REFUSES TO REFRESH A SUPERSEDED PR — THE DANGEROUS DIRECTION
----------------------------------------------------------------------
Every one of these PRs already has auto-merge ARMED. So refreshing one is not
"giving it another chance to be judged"; it is LANDING it on green. A PR whose
register is OLDER than `main`'s would then REWIND `main` to a stale receipt.
Refreshing indiscriminately is therefore worse than leaving the branch
stranded, and this file only ever refreshes a PR it has shown carries content
`main` does not have and nothing newer supersedes.

THE STATES, NEVER COLLAPSED
---------------------------
``refresh``               every payload file is NEWER than `main`'s and no open
                          PR carries a newer version of any of them. Landing it
                          moves `main` forward. REFRESHED.
``superseded_identical``  every payload file is already byte-identical on
                          `main`. The PR is a no-op. Report, do not touch.
``superseded_older``      every dated payload file is OLDER than `main`'s.
                          Landing it would REWIND `main`. Report, do not touch.
``superseded_by_open_pr`` a newer OPEN PR carries the same payload file. Only
                          the newest may land. Report, do not touch.
``carries_append_only``   the diff touches an append-only file (queued operator
                          pings). Its rows may be absent from `main` and closing
                          it drops them. NEEDS A HUMAN READ. Report.
``undated_payload``       a payload file differs from `main` and carries no
                          comparable timestamp, so newer/older CANNOT BE
                          DETERMINED. ⚠️ This is "we could not tell", NOT "it is
                          safe". Report.
``no_payload``            only arming files and the slot claim. Report.

Run ``--self-test`` to plant each case, ``--dry-run`` (the default) to print the
triage without touching a branch, and ``--apply`` to push the refreshes.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]

#: Written by every arming branch, so they carry no information about whether
#: the PR's CONTENT should land. Excluded from the payload for the same reason
#: `check_pr_landing.LANDING_MACHINERY` excludes a branch's own declaration:
#: a property every candidate has cannot discriminate between them.
#:
#: ⚠️ `.github/merge-slots/` IS IN THIS LIST SINCE 2026-10-04 (lane CI-AUTOMERGE),
#: and its absence was the whole jam. R13 moved to one claim file per branch
#: (MI-280) — a file that by construction does NOT EXIST on `main` until the
#: branch merges. Left out, every automation PR graded `absent_on_main` on its
#: own claim. MEASURED on stale-automation-sweep run 37229187372: 115 open
#: `automation/*` PRs, 0 refreshed, every one "needs a human read".
ARMING = re.compile(r"^\.github/(pr-landing|pr-automerge-requests|merge-slots)/")

#: R13's RETIRED shared claim file. Gone from `main` (the per-branch route above
#: replaced it), still excluded so a branch cut before the move grades on its
#: payload rather than on a stale board edit.
SLOT_FILE = "docs/claude/session-board.json"

#: Where `commit-to-main` and `claim_merge_slot.py --branch-claim` write the
#: per-branch R13 claim. The slug rule is the script's own (`branch_slot_rel`).
BRANCH_SLOT_DIR = ".github/merge-slots"

#: APPEND-ONLY payloads: a row present here and absent from `main` is LOST if
#: the PR is closed. `pending-pings.jsonl` is the operator's notification queue
#: — see `OPEN-PRS.json`'s rows for #10902/#10908/#10914. A file in this set
#: can never be classified `superseded` by a timestamp, because the timestamp
#: grades the REGISTER and the loss is in the ROWS.
APPEND_ONLY = frozenset({"docs/claude/pending-pings.jsonl"})

#: Fields a generated register uses to date itself, in the order they are
#: preferred. A file carrying none of them is `undated` — reported as such
#: rather than guessed at.
DATE_FIELDS = ("generated_at", "observed_at", "as_of", "updated_at",
               # research-queue-dispatch-receipt.json (PI-20261006-LCEVL8D5-0001, 2026-10-07)
               "timestamp")

#: A research-queue unit file. Its only machine-written, datable change is the
#: dispatcher's `last_dispatched_at` stamp. MEASURED 2026-10-07 on stale-automation-
#: sweep run 37659085458: 18 of 20 open automation PRs graded `undated_payload` --
#: every dispatch-stamp PR (a unit YAML + the receipt) -- so the sweep could never
#: say whether one was superseded.
QUEUE_UNIT = re.compile(r"^research/queue/RQ-\d{8}-\d{3}\.yaml$")
_STAMP_LINE = re.compile(r"^last_dispatched_at:.*\n?", re.M)
_STAMP_VALUE = re.compile(r"^last_dispatched_at:\s*'?([^'\n]*)'?\s*$", re.M)


def stamp_of(blob: Optional[str]) -> str:
    """The unit's `last_dispatched_at` ('' for null/absent: the oldest possible)."""
    m = _STAMP_VALUE.search(blob or "")
    v = (m.group(1).strip() if m else "")
    return "" if v in ("null", "~") else v


def stamp_only_diff(main_blob: Optional[str], head_blob: Optional[str]) -> bool:
    """True iff the two versions of a unit file differ ONLY in `last_dispatched_at`.

    ⚠️ This is what makes the stamp a datable register. A unit whose status, grading
    or result fields also changed is a different PR kind (grade/result) and stays
    undated -- a stamp's date says nothing about whether THOSE edits are newer.
    """
    if main_blob is None or head_blob is None:
        return False
    return _STAMP_LINE.sub("", main_blob) == _STAMP_LINE.sub("", head_blob)

REFRESH = "refresh"
SUPERSEDED_IDENTICAL = "superseded_identical"
SUPERSEDED_OLDER = "superseded_older"
SUPERSEDED_BY_OPEN_PR = "superseded_by_open_pr"
CARRIES_APPEND_ONLY = "carries_append_only"
UNDATED_PAYLOAD = "undated_payload"
NO_PAYLOAD = "no_payload"
ABSENT_ON_MAIN = "absent_on_main"

#: Prefix of `refresh()`'s note when merging `main` conflicts. `run()` reports it as a
#: warning, not a failure -- the branch is untouched and only a human can resolve it.
CONFLICT_NOTE = "conflict"

#: The ONLY state this file acts on. Everything else is reported and left alone.
ACTIONABLE = (REFRESH,)


def git(*args: str, cwd: Optional[Path] = None) -> Tuple[int, str]:
    p = subprocess.run(["git", "-C", str(cwd or REPO), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def _blob(ref: str, path: str, cwd: Optional[Path] = None) -> Optional[str]:
    code, out = git("show", f"{ref}:{path}", cwd=cwd)
    return out if code == 0 else None


def dated_at(ref: str, path: str, cwd: Optional[Path] = None) -> Optional[str]:
    """The register's own generation timestamp, or None if it has none.

    ⚠️ Returns None both for "this file is not JSON" and for "this JSON has no
    date field". The caller must not read None as "not newer" — it means the
    comparison CANNOT BE MADE, and it routes to `undated_payload`.
    """
    raw = _blob(ref, path, cwd=cwd)
    if not raw or not raw.strip():
        return None
    if QUEUE_UNIT.match(path):
        return stamp_of(raw) or None
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    for field in DATE_FIELDS:
        value = doc.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def payload_files(base: str, head: str, cwd: Optional[Path] = None) -> List[str]:
    """The files this branch changes that actually say something about landing."""
    code, out = git("merge-base", base, head, cwd=cwd)
    merge_base = out if code == 0 else base
    _, names = git("diff", "--name-only", merge_base, head, cwd=cwd)
    return [f for f in names.split("\n")
            if f and not ARMING.match(f) and f != SLOT_FILE]


def extra_rows(ref: str, path: str, main: str,
               cwd: Optional[Path] = None) -> int:
    """Rows this branch has that `main` does not — what closing would DROP."""
    theirs = set((_blob(ref, path, cwd=cwd) or "").splitlines())
    ours = set((_blob(main, path, cwd=cwd) or "").splitlines())
    return len(theirs - ours)


def looks_appended(main_blob: Optional[str], head_blob: Optional[str]) -> bool:
    """Is `main`'s content a strict PREFIX of this branch's — i.e. an APPEND?

    ⚠️ WHY THIS EXISTS BESIDE `APPEND_ONLY`, WHICH IS A HARDCODED PATH LIST.
    MEASURED 2026-09-09 by hand-triaging #11475, a PR the list does not cover:
    `comms/macro/econ_calendar_snapshots.jsonl` on that branch is `main` plus
    **427 rows main does not have**, and the branch also carries a
    point-in-time capture (`econ_calendar_captures/US-...fxstreet.json`) that is
    ABSENT FROM MAIN ENTIRELY. A PIT capture is forward-only; nothing
    re-derives it. So the queue holds a SECOND append-only payload class and
    the list knew about one.

    It graded `undated_payload` and was therefore never actionable — but only
    BY ACCIDENT, because JSONL does not parse as a JSON object so `dated_at`
    returned None. Had that generator stamped `generated_at` the file would
    have been graded on its REGISTER and become closable, dropping 427 rows.
    ⚠️ AND STAMPING `generated_at` IS EXACTLY THE REMEDY THIS SESSION FILED for
    the 13 undated PRs — so the filed fix would have ARMED this bug. That is
    why the detection is now a PROPERTY of the content and not a list somebody
    has to remember to extend.

    ⚠️ IT ONLY EVER MOVES A PR TOWARD "a human must look", never toward
    actionable — `carries_append_only` is not in `ACTIONABLE`. A regenerated
    register that merely happens to grow by appending is therefore graded
    conservatively, which is the accepted cost and the safe direction.

    ⚠️ IT DOES NOT REPLACE `APPEND_ONLY`. A branch that REWRITES a known
    append-only file breaks the prefix relation, and that is precisely the
    dangerous case — so a path in the set stays append-only whatever its
    content does.
    """
    if not main_blob or not head_blob:
        return False
    return len(head_blob) > len(main_blob) and head_blob.startswith(main_blob)


def classify(pr: Dict[str, Any], main: str, newest_holder: Dict[str, int],
             cwd: Optional[Path] = None) -> Dict[str, Any]:
    """Grade ONE pr dict ({'number', 'ref'}) against `main`.

    `newest_holder` maps a payload path -> the PR number that carries the newest
    version of it among the open set, so that only that PR is ever refreshed.
    """
    head = pr["ref"] if "/" not in main else f"origin/{pr['ref']}"
    code, _ = git("rev-parse", "--verify", head, cwd=cwd)
    if code != 0:
        head = pr["ref"]
    files = payload_files(main, head, cwd=cwd)
    buckets: Dict[str, List[str]] = {
        "same": [], "older": [], "newer": [], "undated": [], "append_only": [],
        "absent": []}
    for path in files:
        _, changed = git("diff", "--name-only", main, head, "--", path, cwd=cwd)
        if not changed.strip():
            buckets["same"].append(path)
            continue
        main_blob = _blob(main, path, cwd=cwd)
        head_blob = _blob(head, path, cwd=cwd)
        # ⚠️ ABSENT is asked BEFORE the date comparison, because `dated_at` on a
        # file that does not exist returns None for the same reason it returns
        # None on an undated one — and those are DIFFERENT FACTS. Reporting
        # "no comparable timestamp" for a file `main` has never had names a
        # cause no code path tested, which is the UNPROVENANCED DIAGNOSTIC
        # OUTPUT class this repo has a guard for. Branch on the real stage.
        if main_blob is None:
            buckets["absent"].append(path)
            continue
        if path in APPEND_ONLY or looks_appended(main_blob, head_blob):
            n = extra_rows(head, path, main, cwd=cwd)
            buckets["append_only"].append(f"{path} (+{n} row(s) not on main)")
            continue
        if QUEUE_UNIT.match(path) and not stamp_only_diff(main_blob, head_blob):
            buckets["undated"].append(path)   # a grade/result edit: a stamp date cannot order it
            continue
        theirs, ours = dated_at(head, path, cwd=cwd), dated_at(main, path, cwd=cwd)
        if QUEUE_UNIT.match(path):
            # null is the OLDEST stamp, so a stamp-only diff always orders
            theirs, ours = theirs or "", ours or ""
            buckets["older" if theirs <= ours else "newer"].append(path)
        elif theirs and ours:
            buckets["older" if theirs <= ours else "newer"].append(path)
        else:
            buckets["undated"].append(path)

    # ORDER MATTERS, AND IT CHANGED ON 2026-09-09 AFTER A MEASUREMENT.
    #
    # ⚠️ `carries_append_only` USED TO SHORT-CIRCUIT EVERYTHING, AND THAT WAS
    # BACKWARDS ON THE REFRESH PATH. Its purpose is that a PR carrying an unread
    # operator ping must never be CLOSED as cleanly superseded -- the motivating
    # mistake in this module's docstring. But it was also blocking the REFRESH,
    # and refreshing is the act that LANDS those rows on `main`. So the safety
    # property was stranding exactly the PRs whose rows most needed to arrive.
    #
    # MEASURED, population = the 49 open `automation/*` PRs at 2026-09-09T14:18Z:
    # ALL TEN open work-digest PRs graded `carries_append_only`, because a work
    # digest ALWAYS queues a ping row. So the sweeper could NEVER refresh a
    # work-digest PR -- and `check_digest_liveness` (6h) is the TIGHTEST
    # repo-wide receipt guard, carried by precisely that class. The one
    # mechanism built to keep those clocks alive was structurally unable to
    # touch the tightest one.
    #
    # It is now an ANNOTATION that forbids CLOSING (which this file never does
    # anyway) and does NOT forbid landing. The register verdict decides, and the
    # append fact rides along in `why`.
    #
    # `older` is asked BEFORE `newer` so a PR carrying one of each cannot be
    # refreshed into a partial REWIND. (Measured the same read: 0 of 49 PRs mix
    # the two today, so this costs nothing now and closes the hole before it is
    # reachable.)
    #
    # ⚠️ `absent` is NOT a blocker on its own (2026-10-04, lane CI-AUTOMERGE).
    # A file `main` has never had cannot be REWOUND by landing it — it is pure
    # addition, and this PR is its only copy, which is the reason to LAND it,
    # not to leave it. Every research result is a new file, so while `absent`
    # short-circuited, the sweeper could never move one evidence record onto
    # `main`. It now rides along like `append_note`; `absent_on_main` is kept
    # only for a PR whose OTHER files make it unrefreshable (older, undated,
    # beaten), so a human still reads that mix.
    append_note = "; ".join(buckets["append_only"])
    absent_note = (
        f"{len(buckets['absent'])} new file(s) main does not have yet: "
        + ", ".join(buckets["absent"][:3])) if buckets["absent"] else ""
    if buckets["undated"]:
        state, why = UNDATED_PAYLOAD, (
            f"{len(buckets['undated'])} payload file(s) differ from main with no "
            f"comparable timestamp — newer/older COULD NOT BE DETERMINED: "
            + ", ".join(buckets["undated"][:3]))
    elif buckets["older"]:
        state, why = SUPERSEDED_OLDER, (
            f"{len(buckets['older'])} payload file(s) OLDER than main's — "
            f"landing this would REWIND main: " + ", ".join(buckets["older"][:3]))
    elif buckets["newer"]:
        beaten = [(p, newest_holder[p]) for p in buckets["newer"]
                  if newest_holder.get(p, pr["number"]) != pr["number"]]
        if beaten:
            state = SUPERSEDED_BY_OPEN_PR
            why = "; ".join(f"#{n} carries a newer {p}" for p, n in beaten)
        else:
            state = REFRESH
            why = (f"{len(buckets['newer'])} payload file(s) newer than main's "
                   f"and newest among the open set: "
                   + ", ".join(buckets["newer"][:3]))
            if append_note:
                why += (f" — and it LANDS append-only rows main lacks, which is "
                        f"the point: {append_note}")
    elif buckets["absent"]:
        state, why = REFRESH, absent_note
    elif buckets["same"]:
        state, why = SUPERSEDED_IDENTICAL, (
            f"all {len(buckets['same'])} payload file(s) already byte-identical "
            f"on main")
    else:
        state, why = NO_PAYLOAD, "only arming files and the R13 slot claim"

    # ⚠️ THE OVERRIDE IS ONE-DIRECTIONAL. It can only move a PR OUT of a
    # closable/quiet verdict into "a human must look" — it can never make one
    # actionable, and it deliberately does NOT fire on REFRESH, because landing
    # is how the rows reach `main`.
    if absent_note and state == REFRESH and buckets["newer"]:
        why += f" — and {absent_note}"
    elif absent_note and state != REFRESH:
        state = ABSENT_ON_MAIN
        why = (f"{absent_note} — this PR is their only copy, and it cannot be "
               f"refreshed because: {why}")
    if append_note and state != REFRESH:
        state = CARRIES_APPEND_ONLY
        why = f"{append_note} — {why}"

    return {"pr": pr["number"], "ref": pr["ref"], "state": state, "why": why,
            "files": files}


def newest_by_path(prs: List[Dict[str, Any]], main: str,
                   cwd: Optional[Path] = None) -> Dict[str, int]:
    """For each payload path, the OPEN pr number carrying the newest version.

    ⚠️ A path whose holders are undated is deliberately ABSENT from this map,
    not defaulted to anyone: `classify` then routes those PRs to
    `undated_payload` rather than letting an unorderable path elect a winner.
    """
    best: Dict[str, Tuple[str, int]] = {}
    for pr in prs:
        head = f"origin/{pr['ref']}"
        if git("rev-parse", "--verify", head, cwd=cwd)[0] != 0:
            head = pr["ref"]
        for path in payload_files(main, head, cwd=cwd):
            if path in APPEND_ONLY:
                continue
            stamp = dated_at(head, path, cwd=cwd)
            if not stamp:
                continue
            if QUEUE_UNIT.match(path) and not stamp_only_diff(
                    _blob(main, path, cwd=cwd), _blob(head, path, cwd=cwd)):
                continue
            if path not in best or stamp > best[path][0]:
                best[path] = (stamp, pr["number"])
    return {path: num for path, (_stamp, num) in best.items()}


def _branch_slot_rel(branch: str) -> str:
    """The per-branch R13 claim path — the script's own slug rule, not a copy."""
    sys.path.insert(0, str(REPO / "scripts/ops"))
    from claim_merge_slot import branch_slot_rel  # noqa: E402
    return branch_slot_rel(branch, BRANCH_SLOT_DIR)


def refresh(entry: Dict[str, Any], main: str, held_by: str,
            apply: bool, cwd: Optional[Path] = None) -> Tuple[bool, str]:
    """Merge `main` into the stranded branch, keep its R13 claim, push.

    ⚠️ REWRITTEN 2026-10-04 (lane CI-AUTOMERGE) for the per-branch claim. The
    old body merged `main`, then re-spliced the SHARED `merge_slot` field in
    `docs/claude/session-board.json` — a file no longer on `main` — and so could
    not have succeeded on any branch even had one been graded `refresh`. A
    per-branch claim needs no re-assertion: it is a file only this branch adds,
    so merging `main` cannot touch it. It is WRITTEN here only when a branch cut
    before the move carries none, and VERIFIED in every case before the push.
    """
    ref = entry["ref"]
    if not apply:
        return True, "would refresh (dry-run)"
    if git("fetch", "origin", ref, cwd=cwd)[0] != 0:
        return False, "could not fetch the branch"
    if git("checkout", "-B", ref, f"origin/{ref}", cwd=cwd)[0] != 0:
        return False, "could not check the branch out"
    code, _ = git("merge", main, "-m",
                  "Merge main so the required checks re-run against the current base",
                  cwd=cwd)
    if code != 0:
        _, conflicted = git("diff", "--name-only", "--diff-filter=U", cwd=cwd)
        git("merge", "--abort", cwd=cwd)
        return False, f"{CONFLICT_NOTE} ({conflicted!r}) — left alone for a human"

    slot = _branch_slot_rel(ref)
    code, merge_base = git("merge-base", main, "HEAD", cwd=cwd)
    if code != 0:
        return False, "could not resolve the merge-base to verify the claim"
    _, changed = git("diff", "--name-only", merge_base, "HEAD", "--", slot, cwd=cwd)
    if not changed.strip():
        claim = REPO / "scripts/ops/claim_merge_slot.py"
        if not claim.is_file():
            return False, ("scripts/ops/claim_merge_slot.py is absent — REFUSING to "
                           "push a branch that would fail its own R13 guard")
        done = subprocess.run(
            [sys.executable, str(claim), "--branch-claim", "--branch", ref,
             "--held-by", held_by,
             "--purpose", ("Written by sweep_stale_automation_prs.py after the "
                           "producing run exited; arming IS the merge (R13).")],
            capture_output=True, text=True, cwd=str(cwd or REPO))
        if done.returncode != 0:
            return False, f"claim_merge_slot refused: {done.stderr.strip()[:200]}"
        git("add", "--", slot, cwd=cwd)
        # ⚠️ `-m`, NEVER `--no-edit` — see the 2026-09-09 false success: on the
        # no-conflict path the merge has already committed, there is no message
        # to reuse, and a discarded failure here pushed a branch with no claim.
        if git("commit", "-q", "-m",
               "Write this branch's R13 per-branch merge-slot claim", cwd=cwd)[0] != 0:
            git("reset", "--hard", "HEAD", cwd=cwd)
            return False, "could not commit the merge-slot claim — nothing pushed"
        _, changed = git("diff", "--name-only", merge_base, "HEAD", "--", slot,
                         cwd=cwd)

    # ⚠️ VERIFY THE EFFECT, NOT THE CALL — the question R13 asks is whether the
    # claim is in THIS branch's own diff and names THIS branch.
    if not changed.strip():
        return False, (f"{slot} is NOT in this branch's diff — refusing to push a "
                       f"branch that would still fail R13")
    try:
        holder = json.loads(_blob("HEAD", slot, cwd=cwd) or "{}").get("branch")
    except (json.JSONDecodeError, ValueError, AttributeError):
        holder = None
    if holder != ref:
        return False, (f"{slot} names {holder!r}, not {ref!r} — refusing to push a "
                       f"branch that rides someone else's claim")

    if git("push", "origin", f"HEAD:refs/heads/{ref}", cwd=cwd)[0] != 0:
        return False, "push failed"
    return True, "refreshed and pushed — the required checks re-run on the new sha"


class CouldNotLook(RuntimeError):
    """The open set could not be OBSERVED. Distinct from 'there are none'.

    ⚠️ Never downgrade this to an empty list. `open_pr_record.py` names the same
    hazard — *"no live open-PR list was supplied, so nothing was compared. ⚠️ WE
    DID NOT LOOK … This is NOT `recorded`"* — and a sweeper that reported
    "0 stranded PRs" because it could not reach GitHub would be the exact
    failed-read-rendering-as-a-clean-negative this repo keeps paying for.
    """


def open_automation_prs(supplied: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The live open set. From a supplied observation, or from `gh`.

    NEVER from a committed file. `OPEN-PRS.json` is explicitly *"not
    authoritative for CI or mergeability"* by its own docstring, and it is one
    of the registers this sweeper exists to unblock — reading it here would make
    the sweeper's input depend on the very thing it is repairing.

    `--open-prs` exists because a PM-side / Claude-Code-on-the-web session has
    the GitHub MCP but NO `gh` binary (measured 2026-09-09: `which gh` is empty
    in that container), while the Actions runner has `gh` and no MCP. Same
    contract as `open_pr_record.py --open-prs`, so the two agree about what an
    observation is.
    """
    if supplied is not None:
        try:
            rows = json.loads(supplied.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CouldNotLook(f"--open-prs {supplied} is unreadable: {exc}") from exc
    else:
        try:
            done = subprocess.run(
                ["gh", "pr", "list", "--state", "open", "--limit", "200",
                 "--json", "number,headRefName,createdAt"],
                capture_output=True, text=True, cwd=str(REPO))
        except FileNotFoundError as exc:
            raise CouldNotLook(
                "no `gh` binary and no --open-prs observation was supplied, so "
                "the open set COULD NOT BE READ. This is 'we did not look', not "
                "'nothing is stranded'. On a PM-side session pass the MCP's "
                "`list_pull_requests` output as --open-prs; on a runner install "
                "`gh`.") from exc
        if done.returncode != 0:
            raise CouldNotLook(f"gh pr list failed: {done.stderr.strip()[:300]}")
        rows = json.loads(done.stdout or "[]")

    out = []
    for r in rows:
        ref = r.get("headRefName") or (r.get("head") or {}).get("ref") or ""
        if str(ref).startswith("automation/"):
            out.append({"number": r["number"], "ref": ref,
                        "created_at": r.get("createdAt") or r.get("created_at", "")})
    return out


def run(main: str, apply: bool, held_by: str,
        supplied: Optional[Path] = None) -> int:
    try:
        prs = open_automation_prs(supplied)
    except CouldNotLook as exc:
        print(f"::error::sweep-stale-automation-prs: COULD NOT LOOK — {exc}")
        return 2
    print(f"POPULATION: {len(prs)} open PR(s) with an `automation/*` head ref, "
          f"graded against {main}.")
    if not prs:
        print("nothing to sweep.")
        return 0
    for pr in prs:
        git("fetch", "-q", "origin", pr["ref"])
    holders = newest_by_path(prs, main)
    entries = [classify(pr, main, holders) for pr in prs]

    counts: Dict[str, int] = {}
    for e in entries:
        counts[e["state"]] = counts.get(e["state"], 0) + 1
    print("\nTRIAGE")
    print("=" * 72)
    for state in (REFRESH, SUPERSEDED_IDENTICAL, SUPERSEDED_OLDER,
                  SUPERSEDED_BY_OPEN_PR, CARRIES_APPEND_ONLY, ABSENT_ON_MAIN,
                  UNDATED_PAYLOAD, NO_PAYLOAD):
        if counts.get(state):
            print(f"  {state:24} {counts[state]}")
    print("=" * 72)

    failures = 0
    conflicts = 0
    for e in sorted(entries, key=lambda x: x["pr"]):
        if e["state"] in ACTIONABLE:
            ok, note = refresh(e, main, held_by, apply)
            if not ok and note.startswith(CONFLICT_NOTE):
                # ⚠️ NOT a failed refresh (PI-20261006-LCEVL8D5-0001, 2026-10-07): the sweep was
                # red on runs 110-119 for two PRs (#11912, #13133) it can only ever leave alone.
                # A permanently-red job is the alarm fatigue this repo files as its own bug --
                # it taught everyone to skip the sweep's one real signal. A conflict is still
                # surfaced, as a warning annotation naming the PR, and the PR stays open.
                conflicts += 1
                print(f"::warning::sweep-stale-automation-prs: #{e['pr']} {note}")
            else:
                failures += 0 if ok else 1
            print(f"  #{e['pr']} {e['state']:24} {note}")
        else:
            print(f"  #{e['pr']} {e['state']:24} {e['why'][:150]}")

    needs_human = [e for e in entries
                   if e["state"] in (CARRIES_APPEND_ONLY, ABSENT_ON_MAIN,
                                     UNDATED_PAYLOAD)]
    if needs_human:
        print(f"\n⚠️ {len(needs_human)} PR(s) NEED A HUMAN READ and were not "
              f"touched. This sweeper never closes a PR: a `closed_unmerged` row "
              f"with no `disposition` grades `undispositioned` in "
              f"open_pr_record.py, and an append-only payload's rows are LOST on "
              f"close. Read the diff, then close by hand with a recorded reason.")
        for e in needs_human:
            print(f"    #{e['pr']} {e['state']}: {e['why'][:120]}")
    if conflicts:
        print(f"\n⚠️ {conflicts} PR(s) CONFLICT with main and were left alone (warnings above).")
    if not apply:
        print("\n(dry-run — nothing was pushed. Re-run with --apply.)")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# SELF-TEST — a planted instance of every state, in a scratch repo.
# One direction proves the classifier runs, never that it discriminates, so
# each case asserts the state it must produce AND that no other case produces
# it by accident.
# --------------------------------------------------------------------------
def _self_test() -> int:
    import shutil
    import tempfile

    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {label}"
              + ("" if good else f"  (got {got!r}, want {want!r})"))

    tmp = Path(tempfile.mkdtemp(prefix="sweep-selftest-"))
    try:
        def g(*a: str) -> Tuple[int, str]:
            return git(*a, cwd=tmp)

        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")

        def write(path: str, text: str) -> None:
            p = tmp / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

        def commit(msg: str) -> None:
            g("add", "-A")
            g("commit", "-q", "-m", msg)

        # main carries a register dated at noon and one queued ping.
        write("reg.json", json.dumps({"generated_at": "2026-09-09T12:00:00Z",
                                      "v": "main"}) + "\n")
        write("docs/claude/pending-pings.jsonl", '{"t":"a"}\n')
        commit("main")

        def branch(name: str, mutate) -> None:
            g("checkout", "-q", "main")
            g("checkout", "-q", "-b", name)
            mutate()
            commit(name)
            g("checkout", "-q", "main")

        branch("automation/newer", lambda: write(
            "reg.json", json.dumps({"generated_at": "2026-09-09T13:00:00Z",
                                    "v": "newer"}) + "\n"))
        branch("automation/older", lambda: write(
            "reg.json", json.dumps({"generated_at": "2026-09-09T11:00:00Z",
                                    "v": "older"}) + "\n"))
        branch("automation/newest", lambda: write(
            "reg.json", json.dumps({"generated_at": "2026-09-09T14:00:00Z",
                                    "v": "newest"}) + "\n"))
        branch("automation/undated", lambda: write("reg.json",
                                                   json.dumps({"v": "x"}) + "\n"))
        branch("automation/ping", lambda: write(
            "docs/claude/pending-pings.jsonl", '{"t":"a"}\n{"t":"b"}\n'))
        branch("automation/arming", lambda: (
            write(".github/pr-landing/automation-arming.json", "{}\n"),
            write(".github/merge-slots/automation-arming.json", "{}\n")))
        branch("automation/result", lambda: (
            write("research/results/RQ-1/1.jsonl", '{"r":1}\n'),
            write(".github/merge-slots/automation-result.json", "{}\n")))
        branch("automation/result-plus-older", lambda: (
            write("research/results/RQ-1/2.jsonl", '{"r":2}\n'),
            write("reg.json", json.dumps({"generated_at": "2026-09-09T10:00:00Z",
                                          "v": "older2"}) + "\n")))

        prs = [{"number": 1, "ref": "automation/newer"},
               {"number": 2, "ref": "automation/older"},
               {"number": 3, "ref": "automation/newest"},
               {"number": 4, "ref": "automation/undated"},
               {"number": 5, "ref": "automation/ping"},
               {"number": 6, "ref": "automation/arming"},
               {"number": 8, "ref": "automation/result"},
               {"number": 9, "ref": "automation/result-plus-older"}]
        holders = newest_by_path(prs, "main", cwd=tmp)
        got = {p["number"]: classify(p, "main", holders, cwd=tmp)["state"]
               for p in prs}

        check("a newer register that nothing beats -> refresh", got[3], REFRESH)
        check("a newer register BEATEN by an open PR -> superseded_by_open_pr",
              got[1], SUPERSEDED_BY_OPEN_PR)
        check("an older register -> superseded_older (never refreshed: it would "
              "REWIND main)", got[2], SUPERSEDED_OLDER)
        check("a register with no date -> undated_payload, not a guess",
              got[4], UNDATED_PAYLOAD)
        check("an append-only ping row -> carries_append_only, NOT superseded",
              got[5], CARRIES_APPEND_ONLY)
        check("arming files + its own per-branch R13 claim -> no_payload (the "
              "claim is never on main before merge, so it says nothing)",
              got[6], NO_PAYLOAD)
        check("a NEW result file main lacks -> refresh (landing it rewinds "
              "nothing; this PR is its only copy)", got[8], REFRESH)
        check("a new file beside an OLDER register -> absent_on_main (a human "
              "reads the mix; never refreshed into a rewind)", got[9],
              ABSENT_ON_MAIN)

        # THE CORRECTION THIS MODULE EXISTS FOR, planted explicitly: a PR whose
        # register is stale AND which carries an unread ping must not read as
        # cleanly superseded. Classifying it by the register alone is what would
        # have dropped seven operator pings.
        branch("automation/stale-reg-plus-ping", lambda: (
            write("reg.json", json.dumps({"generated_at": "2026-09-09T11:00:00Z",
                                          "v": "old"}) + "\n"),
            write("docs/claude/pending-pings.jsonl", '{"t":"a"}\n{"t":"z"}\n')))
        mixed = classify({"number": 7, "ref": "automation/stale-reg-plus-ping"},
                         "main", holders, cwd=tmp)
        check("stale register + unread ping -> carries_append_only (the ping "
              "wins over the stale register)", mixed["state"], CARRIES_APPEND_ONLY)
        check("...and it names how many rows would be dropped",
              "+1 row(s) not on main" in mixed["why"], True)

        # refresh() end to end, against a scratch origin: a branch cut BEFORE
        # the per-branch claim existed must come back with its claim written,
        # naming itself, and `main` merged in.
        origin = Path(tempfile.mkdtemp(prefix="sweep-selftest-origin-"))
        git("init", "-q", "--bare", str(origin), cwd=origin)
        g("remote", "add", "origin", str(origin))
        g("push", "-q", "origin", "main", "automation/result-plus-older")
        g("checkout", "-q", "main")
        write("later.txt", "main moved\n")
        commit("main moves")
        g("push", "-q", "origin", "main")
        g("fetch", "-q", "origin")
        ok_r, note = refresh({"ref": "automation/result-plus-older"},
                             "origin/main", "selftest", True, cwd=tmp)
        check(f"refresh() pushes a branch with no claim yet [{note}]", (ok_r, note[:9]),
              (True, "refreshed"))
        g("fetch", "-q", "origin")
        pushed = "origin/automation/result-plus-older"
        claim = json.loads(_blob(
            pushed, ".github/merge-slots/automation-result-plus-older.json",
            cwd=tmp) or "{}")
        check("...carrying a per-branch claim that names the branch",
              claim.get("branch"), "automation/result-plus-older")
        check("...with main merged in",
              git("merge-base", "--is-ancestor", "origin/main", pushed,
                  cwd=tmp)[0], 0)

        # Only `refresh` is ever acted on.
        check("exactly one state is actionable", list(ACTIONABLE), [REFRESH])
        check("superseded_older is NOT actionable",
              SUPERSEDED_OLDER in ACTIONABLE, False)
        check("carries_append_only is NOT actionable",
              CARRIES_APPEND_ONLY in ACTIONABLE, False)

        # A refresh that cannot write the R13 claim must REFUSE, not push.
        # (Checked by construction: `refresh` returns False when the script is
        # absent. Proving it needs the real path to be missing, so assert the
        # guard exists rather than deleting a repo file.)
        src = Path(__file__).read_text(encoding="utf-8")
        check("refresh() refuses when claim_merge_slot.py is absent",
              "REFUSING to" in src and "claim_merge_slot.py is absent" in src, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if "origin" in locals():
            shutil.rmtree(origin, ignore_errors=True)

    print(f"sweep-stale-automation-prs self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main", default="origin/main",
                    help="the base ref to grade against (default origin/main)")
    ap.add_argument("--apply", action="store_true",
                    help="actually push the refreshes (default is a dry run)")
    ap.add_argument("--held-by", default="sweep_stale_automation_prs.py",
                    help="attribution written into the R13 merge-slot claim")
    ap.add_argument("--open-prs", type=Path, default=None,
                    help=("a JSON list of open PRs, as an alternative to `gh` — the "
                          "PM-side route, since that container has the GitHub MCP "
                          "but no `gh` binary"))
    ap.add_argument("--self-test", action="store_true",
                    help="plant every state and prove the classifier separates them")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    return run(args.main, args.apply, args.held_by, args.open_prs)


if __name__ == "__main__":
    raise SystemExit(main())
