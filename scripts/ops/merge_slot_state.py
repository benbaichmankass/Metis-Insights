#!/usr/bin/env python3
#
# wiring: imported by `scripts/ops/claim_merge_slot.py`, which prints the
# incumbent claim's state BEFORE it overwrites it. Pure policy: `grade` does no
# I/O at all and `branch_on_origin` is the one function that shells out.
"""IS THE MERGE SLOT ACTUALLY HELD, OR IS IT A GHOST?

THE DEFECT — the release step has no mechanism, and never has
-------------------------------------------------------------
`check_pr_landing.py` R13 **refuses** to let a branch arm the landing route
unless it holds `merge_slot` in `docs/claude/session-board.json`. So the CLAIM
half is a mechanism and happens every single time. The RELEASE half is prose, in
three places, and is carried by nothing: no helper, no guard, no workflow.

MEASURED over the last 25 commits touching `session-board.json`: **ZERO** ever
wrote a cleared slot
(`BL-20260910-THE-MERGE-SLOT-HAS-A-DOCUMENTED-RELEASE-STEP-WITH-NO-MECHANISM-SO-EVERY-CLAIM-READS-HELD-FOREVER`).
Not a discipline gap — the documented step has never been performed by anyone.
And it is **structurally unperformable from an armed PR**: clearing the slot to
nulls while arming fails R13 by construction, and `claim_merge_slot.py` refuses
an empty `--branch` outright.

⚠️ **AND NO SESSION CAN BE ASKED TO STOP.** `.github/actions/commit-to-main`
writes the claim on every automated commit and 27 workflows call it, so the slot
is rewritten **mechanically at the rate `main` moves** — measured at a median
7.1-minute gap between merges
(`BL-20260909-MERGE-SLOT-IS-NEVER-RELEASED-AFTER-A-MERGE-SO-EVERY-CLAIMANT-DISPLACES-A-GHOST`).

THE COST IS A JUDGEMENT CALL EVERY CLAIMANT MAKES ALONE
-------------------------------------------------------
`held_by` reads the same whether the holder is mid-merge or finished hours ago,
so a claimant either **waits forever on a merged PR** or **displaces blind**.
Both were live in the 2026-09-09 window; one session withdrew its arming pair
and queued behind a claim that was already spent.

WHAT THIS DOES — option (b) of that row: DERIVED, never written
---------------------------------------------------------------
The slot already records `branch`, so nothing new has to be written at claim
time and this works on **every claim already in the history**. The answer is
computed at the moment the judgement is made, inside the tool every claimant
already runs.

FOUR STATES, NEVER COLLAPSED
----------------------------
``unclaimed``    the slot is null/empty. Nothing to displace.
``spent``        **ESTABLISHED.** The claiming branch is GONE from `origin`, or
                 a supplied PR payload says it merged/closed. Displace freely.
``live``         **ESTABLISHED.** A supplied PR payload says the claiming PR is
                 OPEN. Displacing may cut in front of real work — say so.
``undecidable``  the branch is still on `origin` and no PR payload was supplied.
                 **WE COULD NOT ESTABLISH IT.** Neither held nor free.

⚠️ **BRANCH PRESENCE IS NOT EVIDENCE OF LIVENESS, AND THAT IS MEASURED RATHER
THAN ASSUMED.** Pruning is uneven here: the automation branches ARE reaped —
the slot on `main` at 2026-09-12T06:13Z named
`automation/reconcile-open-prs-34677561396-1`, whose PR had merged and whose
branch `git ls-remote` could not find, so that claim was decidably a ghost —
while **4 of 4** sampled `claude/mi188*` / `mi191*` / `mi193*` lane branches
merged FOUR DAYS earlier were still on `origin`. So absence is decisive and
presence proves nothing, which is exactly why presence grades `undecidable` and
never `live`. Grading it `live` would recreate the wait-on-a-ghost this exists
to end, with the machine's authority behind it.

⚠️ **THIS RELEASES NOTHING AND SERIALIZES NOTHING.** R13's own docstring is
explicit that a committed claim reaches no other session until the branch
merges, and concurrent-merge safety rests on branch-protection required checks,
not on this field. What it removes is the *unaided judgement call*, which is the
cost the backlog rows actually name.

Self-test:  python3 scripts/ops/merge_slot_state.py --self-test
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

UNCLAIMED, SPENT, LIVE, UNDECIDABLE = "unclaimed", "spent", "live", "undecidable"
ALL_STATES = (UNCLAIMED, SPENT, LIVE, UNDECIDABLE)


def branch_on_origin(branch: str, root: Path = Path(".")) -> Optional[bool]:
    """True / False / **None**. ``None`` is *we could not look*, never False.

    A failed `ls-remote` and a branch that is genuinely gone are opposite facts:
    the first says nothing, the second is the whole finding.
    """
    if not branch:
        return None
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-remote", "--heads", "origin", branch],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return bool(res.stdout.strip())


# A pull-request `state` that means the claim can no longer reach `main`.
# `merged` is listed even though GitHub reports it via `merged`/`merged_at`,
# because a caller hand-building a payload may spell it that way.
_CLOSED_STATES = {"closed", "merged"}


def _pr_label(pr: Dict[str, Any]) -> str:
    """The PR's number, or a phrase that does not read like one.

    `f"PR #{pr.get('number')}"` renders `PR #None` on a payload that names no
    PR — which reads as a fact about a pull request rather than as the absence
    of one.
    """
    n = pr.get("number")
    return str(n) if isinstance(n, int) else "(no number in the payload)"


def grade(claim: Optional[Dict[str, Any]],
          branch_present: Optional[bool] = None,
          pr: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """PURE. The incumbent claim plus what we could establish, one row out.

    ``branch_present`` is the tri-state from `branch_on_origin`. ``pr`` is an
    optional pull-request payload; when given it is AUTHORITATIVE, because it
    answers the question directly instead of by proxy.
    """
    if not isinstance(claim, dict) or not str(claim.get("branch") or "").strip():
        return _row(UNCLAIMED, claim,
                    "the merge slot is empty — nothing to displace.")
    branch = str(claim["branch"]).strip()
    holder = str(claim.get("held_by") or "").strip() or "(unattributed)"

    unusable = ""
    if isinstance(pr, dict):
        # AUTHORITATIVE — but ONLY when the payload actually answers. See
        # `_pr_is_answerable`: a dict that carries no verdict is not a direct
        # answer, and defaulting it to `open` invents one.
        if pr.get("merged") or pr.get("merged_at"):
            return _row(SPENT, claim,
                        f"PR #{_pr_label(pr)} for {branch!r} is MERGED, so "
                        f"{holder} has finished. Displace freely.")
        state = str(pr.get("state") or "").strip().lower()
        if state in _CLOSED_STATES:
            return _row(SPENT, claim,
                        f"PR #{_pr_label(pr)} for {branch!r} is {state}, so "
                        f"the claim cannot still be on its way to `main`. "
                        f"Displace freely.")
        if state == "open":
            return _row(LIVE, claim,
                        f"PR #{_pr_label(pr)} for {branch!r} is OPEN, so "
                        f"{holder} may be mid-merge. Displacing is permitted — "
                        f"R13 does not serialize — but say so, because this one "
                        f"is NOT a ghost.")
        # ⚠️ THE PAYLOAD DID NOT ANSWER, AND THAT IS NOT `open`.
        # MEASURED 2026-09-12: `GET /repos/.../pulls/` with an empty number
        # returns `{"message": "Request path could not be canonicalized."}` —
        # a dict with no `merged`, no `merged_at` and no `state`. The old
        # `str(pr.get("state") or "open")` turned that into LIVE, with a `why`
        # reading "PR #None ... is OPEN": a definite answer the code never
        # established, about a PR it never saw. It failed in the CAUTIOUS
        # direction, so nothing unsafe happened — and it is still the exact
        # collapse this module exists to refuse, one level in from the
        # presence-is-not-liveness refusal it was written for.
        unusable = (f" ⚠️ A pull-request payload WAS supplied and could not be "
                    f"read ({_pr_label(pr)}): it carries no `merged`, no "
                    f"`merged_at` and no recognised `state`, so it is NOT the "
                    f"direct answer and was not treated as one.")

    if branch_present is False:
        return _row(SPENT, claim,
                    f"the claiming branch {branch!r} is GONE from `origin`, so "
                    f"nothing can still be merging from it and {holder} has "
                    f"finished. Displace freely. (This is the common case: "
                    f"`commit-to-main` takes the slot on every automated commit "
                    f"and those branches are reaped on merge.)" + unusable)
    if branch_present is True:
        return _row(UNDECIDABLE, claim,
                    f"the claiming branch {branch!r} is still on `origin`, which "
                    f"is NOT evidence that {holder} is live — merged lane "
                    f"branches are not reliably pruned here (measured: 4 of 4 "
                    f"sampled branches merged four days earlier were still "
                    f"present). Read the claiming PR's state to settle it."
                    + unusable)
    return _row(UNDECIDABLE, claim,
                f"could not reach `origin` to see whether {branch!r} still "
                f"exists — WE DID NOT LOOK. This is not 'the slot is free'."
                + unusable)


def _row(state: str, claim: Optional[Dict[str, Any]], why: str) -> Dict[str, Any]:
    c = claim if isinstance(claim, dict) else {}
    return {"state": state, "branch": c.get("branch"), "held_by": c.get("held_by"),
            "claimed_at": c.get("claimed_at"), "why": why,
            "safe_to_displace": state in (UNCLAIMED, SPENT)}


def render(row: Dict[str, Any]) -> str:
    icon = {SPENT: "  ", LIVE: "⚠️ ", UNDECIDABLE: "⚠️ "}.get(row["state"], "  ")
    head = f"{icon}incumbent merge-slot claim: {row['state'].upper()}"
    if row.get("branch"):
        head += f" — {row['held_by']} / {row['branch']} (claimed {row['claimed_at']})"
    return head + "\n     " + row["why"]


def read_claim(board: Path) -> Tuple[Optional[Dict[str, Any]], bool]:
    """(claim, readable). An unreadable board is NOT an empty slot."""
    try:
        doc = json.loads(board.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, False
    slot = doc.get("merge_slot") if isinstance(doc, dict) else None
    return (slot if isinstance(slot, dict) else None), True


def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    fails: List[str] = []

    def ok(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok   {label}")

    C = {"held_by": "session_X", "branch": "claude/thing",
         "claimed_at": "2026-09-12T06:13:28Z", "purpose": "p"}

    # ── the ghost, established from git alone — the common automation case ──
    g = grade(C, branch_present=False)
    ok("a claim whose branch is GONE from origin is `spent`", g["state"] == SPENT)
    ok("…and is safe to displace", g["safe_to_displace"] is True)

    # ── presence is NOT liveness, and that is the load-bearing refusal ──────
    u = grade(C, branch_present=True)
    ok("a claim whose branch is STILL on origin is `undecidable`, NEVER `live`",
       u["state"] == UNDECIDABLE)
    ok("…and is NOT safe to displace unaided", u["safe_to_displace"] is False)
    ok("…and says WHY presence proves nothing, with the measurement",
       "4 of 4" in u["why"])

    # ── a PR payload is authoritative and settles both directions ──────────
    ok("a MERGED PR settles it as spent",
       grade(C, True, {"number": 1, "merged": True})["state"] == SPENT)
    ok("a CLOSED-unmerged PR is also spent (it cannot still reach main)",
       grade(C, True, {"number": 1, "state": "closed"})["state"] == SPENT)
    live = grade(C, False, {"number": 1, "state": "open"})
    ok("an OPEN PR is `live` EVEN WHEN the branch read said gone — the direct "
       "answer beats the proxy", live["state"] == LIVE)
    ok("…and `live` is not safe to displace unaided",
       live["safe_to_displace"] is False)
    ok("…but it still says displacement is permitted, because R13 does not "
       "serialize", "does not serialize" in live["why"])

    # ── we could not look ──────────────────────────────────────────────────
    n = grade(C, branch_present=None)
    ok("an unreachable origin is `undecidable`, not `spent`", n["state"] == UNDECIDABLE)
    ok("…and says plainly it is not 'the slot is free'",
       "not 'the slot is free'" in n["why"])
    ok("`branch_on_origin` returns None for an empty branch rather than False",
       branch_on_origin("") is None)

    # ── an empty or malformed slot ─────────────────────────────────────────
    for empty in (None, {}, {"branch": ""}, {"branch": "   "}, "nonsense"):
        ok(f"an empty/malformed slot ({empty!r}) is `unclaimed`",
           grade(empty, False)["state"] == UNCLAIMED)
    ok("`unclaimed` IS safe to displace", grade(None, False)["safe_to_displace"])

    # ── an unattributable claim is still graded, not dropped ───────────────
    ok("a claim with no held_by is still graded rather than ignored",
       grade({"branch": "b"}, False)["state"] == SPENT)

    ok("all four states are reachable",
       {grade(None, False)["state"], grade(C, False)["state"],
        grade(C, True)["state"],
        grade(C, True, {"number": 1, "state": "open"})["state"]} == set(ALL_STATES))

    if not quiet:
        print(f"\n{'FAIL' if fails else 'PASS'}: "
              f"{len(fails)} failure(s) in the merge-slot-state policy")
        for f in fails:
            print(f"  FAIL {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--board", default="docs/claude/session-board.json")
    ap.add_argument("--pr-json", default=None,
                    help="Optional pull-request payload for the CLAIMING branch. "
                         "Authoritative when given; without it a still-present "
                         "branch grades `undecidable`, never `live`.")
    args = ap.parse_args(argv)
    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    claim, readable = read_claim(Path(args.board))
    if not readable:
        print("merge-slot-state: the board could not be read — WE DID NOT LOOK. "
              "This is not an empty slot.")
        return 2
    pr = None
    if args.pr_json:
        try:
            raw = json.loads(Path(args.pr_json).read_text(encoding="utf-8"))
            pr = raw[0] if isinstance(raw, list) and raw else raw
        except (OSError, json.JSONDecodeError):
            pr = None
    row = grade(claim, branch_on_origin(str((claim or {}).get("branch") or "")), pr)
    print(render(row))
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
