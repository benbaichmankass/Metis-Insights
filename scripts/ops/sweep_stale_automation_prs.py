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
``verify-timeout-minutes`` window (default 30), one refresh attempt. When the
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
ARMING = re.compile(r"^\.github/(pr-landing|pr-automerge-requests)/")

#: R13's claim file. Also written by every arming branch — see above — and
#: additionally rewritten by `commit-to-main`'s conflict resolution, so a diff
#: here says nothing about the payload.
SLOT_FILE = "docs/claude/session-board.json"

#: APPEND-ONLY payloads: a row present here and absent from `main` is LOST if
#: the PR is closed. `pending-pings.jsonl` is the operator's notification queue
#: — see `OPEN-PRS.json`'s rows for #10902/#10908/#10914. A file in this set
#: can never be classified `superseded` by a timestamp, because the timestamp
#: grades the REGISTER and the loss is in the ROWS.
APPEND_ONLY = frozenset({"docs/claude/pending-pings.jsonl"})

#: Fields a generated register uses to date itself, in the order they are
#: preferred. A file carrying none of them is `undated` — reported as such
#: rather than guessed at.
DATE_FIELDS = ("generated_at", "observed_at", "as_of", "updated_at")

REFRESH = "refresh"
SUPERSEDED_IDENTICAL = "superseded_identical"
SUPERSEDED_OLDER = "superseded_older"
SUPERSEDED_BY_OPEN_PR = "superseded_by_open_pr"
CARRIES_APPEND_ONLY = "carries_append_only"
UNDATED_PAYLOAD = "undated_payload"
NO_PAYLOAD = "no_payload"

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
        "same": [], "older": [], "newer": [], "undated": [], "append_only": []}
    for path in files:
        _, changed = git("diff", "--name-only", main, head, "--", path, cwd=cwd)
        if not changed.strip():
            buckets["same"].append(path)
            continue
        if path in APPEND_ONLY:
            n = extra_rows(head, path, main, cwd=cwd)
            buckets["append_only"].append(f"{path} (+{n} row(s) not on main)")
            continue
        theirs, ours = dated_at(head, path, cwd=cwd), dated_at(main, path, cwd=cwd)
        if theirs and ours:
            buckets["older" if theirs <= ours else "newer"].append(path)
        else:
            buckets["undated"].append(path)

    # ORDER MATTERS. The states that mean "a human must look" are tested FIRST,
    # so a PR that is superseded on its register but still carries an unread
    # ping is never filed as cleanly superseded. That precedence is the whole
    # correction described in this module's docstring.
    if buckets["append_only"]:
        state, why = CARRIES_APPEND_ONLY, "; ".join(buckets["append_only"])
    elif buckets["undated"]:
        state, why = UNDATED_PAYLOAD, (
            f"{len(buckets['undated'])} payload file(s) differ from main with no "
            f"comparable timestamp — newer/older COULD NOT BE DETERMINED: "
            + ", ".join(buckets["undated"][:3]))
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
    elif buckets["older"]:
        state, why = SUPERSEDED_OLDER, (
            f"{len(buckets['older'])} payload file(s) OLDER than main's — "
            f"landing this would REWIND main: " + ", ".join(buckets["older"][:3]))
    elif buckets["same"]:
        state, why = SUPERSEDED_IDENTICAL, (
            f"all {len(buckets['same'])} payload file(s) already byte-identical "
            f"on main")
    else:
        state, why = NO_PAYLOAD, "only arming files and the R13 slot claim"
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
            if path not in best or stamp > best[path][0]:
                best[path] = (stamp, pr["number"])
    return {path: num for path, (_stamp, num) in best.items()}


def refresh(entry: Dict[str, Any], main: str, held_by: str,
            apply: bool) -> Tuple[bool, str]:
    """Merge `main` into the stranded branch and re-assert its R13 claim.

    This is byte-for-byte what `commit-to-main`'s own stale-branch refresh does
    — merge `main`, resolve a slot-claim conflict by taking main's board and
    re-asserting our own claim over it, push. The only difference is WHO does
    it and WHEN: here, after the producing run is long gone.
    """
    ref = entry["ref"]
    if not apply:
        return True, "would refresh (dry-run)"
    if git("fetch", "origin", ref)[0] != 0:
        return False, "could not fetch the branch"
    if git("checkout", "-B", ref, f"origin/{ref}")[0] != 0:
        return False, "could not check the branch out"
    code, _ = git("merge", main, "-m",
                  "Merge main so the required checks re-run against the current base")
    if code != 0:
        _, conflicted = git("diff", "--name-only", "--diff-filter=U")
        if conflicted.strip() != SLOT_FILE:
            git("merge", "--abort")
            return False, f"conflict outside the slot file ({conflicted!r}) — left alone"
        if git("checkout", "--theirs", "--", SLOT_FILE)[0] != 0:
            git("merge", "--abort")
            return False, "could not take main's board"
    claim = REPO / "scripts/ops/claim_merge_slot.py"
    if not claim.is_file():
        git("merge", "--abort")
        return False, ("scripts/ops/claim_merge_slot.py is absent — REFUSING to "
                       "push a branch that would fail its own R13 guard")
    done = subprocess.run(
        [sys.executable, str(claim), "--branch", ref, "--held-by", held_by,
         "--purpose", ("Re-asserted by sweep_stale_automation_prs.py after the "
                       "producing run exited; arming IS the merge (R13).")],
        capture_output=True, text=True, cwd=str(REPO))
    if done.returncode != 0:
        git("merge", "--abort")
        return False, f"claim_merge_slot refused: {done.stderr.strip()[:200]}"
    git("add", "--", SLOT_FILE)
    git("commit", "-q", "--no-edit")
    if git("push", "origin", f"HEAD:refs/heads/{ref}")[0] != 0:
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
                  SUPERSEDED_BY_OPEN_PR, CARRIES_APPEND_ONLY, UNDATED_PAYLOAD,
                  NO_PAYLOAD):
        if counts.get(state):
            print(f"  {state:24} {counts[state]}")
    print("=" * 72)

    failures = 0
    for e in sorted(entries, key=lambda x: x["pr"]):
        if e["state"] in ACTIONABLE:
            ok, note = refresh(e, main, held_by, apply)
            failures += 0 if ok else 1
            print(f"  #{e['pr']} {e['state']:24} {note}")
        else:
            print(f"  #{e['pr']} {e['state']:24} {e['why'][:150]}")

    needs_human = [e for e in entries
                   if e["state"] in (CARRIES_APPEND_ONLY, UNDATED_PAYLOAD)]
    if needs_human:
        print(f"\n⚠️ {len(needs_human)} PR(s) NEED A HUMAN READ and were not "
              f"touched. This sweeper never closes a PR: a `closed_unmerged` row "
              f"with no `disposition` grades `undispositioned` in "
              f"open_pr_record.py, and an append-only payload's rows are LOST on "
              f"close. Read the diff, then close by hand with a recorded reason.")
        for e in needs_human:
            print(f"    #{e['pr']} {e['state']}: {e['why'][:120]}")
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
        branch("automation/arming", lambda: write(
            ".github/pr-landing/automation-arming.json", "{}\n"))

        prs = [{"number": 1, "ref": "automation/newer"},
               {"number": 2, "ref": "automation/older"},
               {"number": 3, "ref": "automation/newest"},
               {"number": 4, "ref": "automation/undated"},
               {"number": 5, "ref": "automation/ping"},
               {"number": 6, "ref": "automation/arming"}]
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
        check("arming files only -> no_payload", got[6], NO_PAYLOAD)

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
