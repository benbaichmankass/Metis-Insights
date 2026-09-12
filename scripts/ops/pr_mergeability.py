#!/usr/bin/env python3
#
# wiring: imported by `scripts/ops/pr_queue_latency.py` (the queue-wide watcher,
# fired by `.github/workflows/pr-queue-watch.yml`) and by
# `scripts/ops/ci_settle.py` (the per-PR, on-demand reader). It is a PURE policy
# module with no network and no side effects; the two callers do the fetching.
"""CAN THIS PULL REQUEST ACTUALLY BE MERGED? — the one owner of that question.

THE DEFECT THIS EXISTS FOR, MEASURED TWICE
------------------------------------------
**A PR can be 4-of-4 green and impossible to merge, and the two render
identically.** Checks are green, the roll-up is green, every UI that counts
failures shows nothing — and `mergeable_state` is ``dirty``, so the merge button
cannot work for anybody.

Two instances, and the second is live:

* **PR #11842** was 4-of-4 green from 2026-09-12T01:02Z and unmergeable for
  **3.5 hours**. Its green checks were stale against a base `main` had passed.
* **PR #11738** — *"Arm R2 — the per-pass IB circuit breaker (MI-240, Tier-2,
  operator-approved)"* — MEASURED 2026-09-12T05:5xZ via
  `mcp__github__pull_request_read`: **5 of 5 checks `success`** (`guards`,
  `pytest-run`, `pytest-collect`, `repo-inventory`, `open-and-automerge`, all
  concluded 2026-09-11T17:25–17:41Z) and ``mergeable_state: "dirty"``, with
  `updated_at` 2026-09-11T17:24:52Z — **over twelve hours**. Its own body says
  *"what is outstanding is the click, not the decision"*. The click cannot work.

⚠️ **AND THE WATCHER THAT EXISTS CALLS THAT PR ``waiting``**, i.e. *"open and
unmerged with no push for 12h"* — which points the reader at the MANAGER. The
truth is that **nobody** can merge it and the remedy belongs to its AUTHOR: merge
the base branch in. Same value, opposite owners, opposite remedies. That
conflation is the thing this module separates, and it is why the fix is a new
DIMENSION rather than a new threshold.

WHY THE QUEUE WATCHER COULD NOT SEE IT — structural, not an oversight
--------------------------------------------------------------------
`pr-queue-watch.yml` reads ``gh api repos/OWNER/REPO/pulls?state=open`` — the
**LIST** endpoint. GitHub does not serve ``mergeable`` / ``mergeable_state`` /
``rebaseable`` there; they exist only on the single-PR ``GET .../pulls/{n}``,
because GitHub computes mergeability lazily, per PR, on demand.

MEASURED 2026-09-12, with a positive control so the absence means something:
``list_pull_requests`` was asked for ``mergeable_state`` **by name** over all 5
open PRs and returned it on **0 of 5**, while ``pull_request_read method=get`` on
#11738 returned ``"mergeable_state": "dirty"`` from the same session minutes
later. So the field was reachable and the list simply does not carry it.

FIVE STATES, NEVER COLLAPSED
----------------------------
The point of a verdict field is that a caller can BRANCH on it. Before this
module the mergeability answer existed only as (a) a raw GitHub string and (b)
English appended to a ``reason``, and a state nothing branches on is already
collapsed.

``mergeable``      ``clean`` / ``has_hooks`` / ``unstable`` / ``behind``. The
                   merge is possible NOW. ⚠️ ``behind`` belongs here and not in
                   a warning state: this repo turned ``require-up-to-date`` OFF
                   on 2026-08-10 precisely so ``behind`` stops blocking, and
                   grading it as a problem would re-create the ~9-minute CI
                   churn that removal was meant to end.
``conflicted``     ``dirty`` — a merge conflict with the base. **Nobody can
                   merge it, however green it is.** THE FINDING. The owner is
                   the PR's author and the remedy is a base merge; a manager
                   cannot discharge it by merging.
``checks_blocking`` ``blocked`` — a required check is missing or failing, or a
                   review is required. A DIFFERENT fault with a DIFFERENT
                   remedy, and the **expected, healthy** state of a PR that
                   declares ``landing: "hold"``. Folding it into ``conflicted``
                   would page on every correctly-held PR in the repo, which is
                   the desensitised-alarm P1.
``not_computed``   ``unknown``, ``null``, or the key absent. **WE DID NOT
                   LOOK.** GitHub computes mergeability on demand and answers
                   ``unknown`` until it has; a cold read routinely lands here.
                   ⚠️ MEASURED 2026-09-12: 2 of the 5 open PRs (#11827, #11817)
                   read ``unknown`` on a first fetch. Reading that as mergeable
                   is how a detector reports a conflicted queue as clean.
``unreadable``     the per-PR read itself failed. Never folded into
                   ``not_computed``: *"GitHub has not computed it"* is a real
                   observation of GitHub's state, *"our fetch died"* is not an
                   observation of anything.

``merge_possible`` is a separate tri-state (True / False / **None**) rather than
a boolean, for the same reason: ``None`` is *we do not know*, and a boolean here
would have to lie in one direction or the other.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not fetch, does not merge, does not comment, and does not decide when to
page — `queue_latency.escalation_due` stays the one owner of the page policy and
is IMPORTED by the caller rather than re-derived here. It grades one PR's payload
and returns a row.

Self-test:  python3 scripts/ops/pr_mergeability.py --self-test
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ONE OWNER for "what did the checks say". `ci_settle.summarise` already grades a
# check roll-up into seven states and is mutation-tested; a second copy here
# would be free to drift, and this repo records what a second copy of a rule
# costs. It is called WITHOUT a `pr` payload on purpose — that keeps the answer
# purely about CHECKS, so this module's mergeability axis and that module's
# check axis stay orthogonal instead of each folding the other in.
import ci_settle  # noqa: E402

MERGEABLE = "mergeable"
CONFLICTED = "conflicted"
CHECKS_BLOCKING = "checks_blocking"
NOT_COMPUTED = "not_computed"
UNREADABLE = "unreadable"

ALL_STATES = (MERGEABLE, CONFLICTED, CHECKS_BLOCKING, NOT_COMPUTED, UNREADABLE)

#: The GitHub vocabulary, mapped to OUR verdict. ⚠️ An UNRECOGNISED value maps to
#: `not_computed`, never to `mergeable`: GitHub may add a state, and a default
#: that reads a stranger as healthy fails in the reassuring direction.
_GITHUB_TO_VERDICT = {
    "clean": MERGEABLE,
    "has_hooks": MERGEABLE,
    "unstable": MERGEABLE,
    "behind": MERGEABLE,
    "dirty": CONFLICTED,
    "blocked": CHECKS_BLOCKING,
    "draft": CHECKS_BLOCKING,
    "unknown": NOT_COMPUTED,
}

#: Prose for a human, kept beside the verdict rather than instead of it.
MERGE_STATE_NOTES = {
    "dirty": "merge conflict with the base branch",
    "blocked": "a required check is missing or failing, or a review is required",
    "behind": "head is behind the base (not blocking here — 'require up to date' is off)",
    "unstable": "a NON-required check is failing; the PR is still mergeable",
    "clean": "mergeable",
    "has_hooks": "mergeable, with pre-receive hooks",
    "draft": "the PR is a draft, so GitHub reports it as unmergeable until marked ready",
    "unknown": "GitHub has not computed mergeability yet — ask again shortly",
}

#: Check states (from `ci_settle`) in which the PR looks finished to a reader.
#: `cancelled` is deliberately NOT here — a cancelled check produced no verdict
#: and this repo already refuses to read it as a pass.
_LOOKS_DONE = {"green"}


def merge_state_note(mergeable_state: Optional[str]) -> Optional[str]:
    """Prose for a GitHub `mergeable_state`, or ``None`` if there is none."""
    if mergeable_state is None:
        return None
    return MERGE_STATE_NOTES.get(
        mergeable_state, f"unrecognised value {mergeable_state!r}")


def verdict_of(mergeable_state: Optional[str], read_ok: bool = True) -> str:
    """PURE. GitHub's string (or the absence of one) in, our verdict out.

    ``read_ok=False`` means the per-PR fetch FAILED. That is `unreadable` and is
    never `not_computed`, whatever string happens to be in hand.
    """
    if not read_ok:
        return UNREADABLE
    if mergeable_state is None:
        return NOT_COMPUTED
    return _GITHUB_TO_VERDICT.get(str(mergeable_state).lower(), NOT_COMPUTED)


def merge_possible(verdict: str) -> Optional[bool]:
    """True / False / **None**. ``None`` is *we do not know* and is not False."""
    if verdict == MERGEABLE:
        return True
    if verdict in (CONFLICTED, CHECKS_BLOCKING):
        return False
    return None


def grade(pr: Optional[Dict[str, Any]],
          checks: Optional[List[Dict[str, Any]]] = None,
          read_ok: bool = True) -> Dict[str, Any]:
    """PURE. One PR payload (+ optional check runs) in, one mergeability row out.

    ``checks`` is optional because it costs a second API call per PR. When it is
    omitted the row carries ``check_state: null`` — *not looked at* — and
    ``misleading`` is ``None`` rather than ``False``, because whether a
    conflicted PR LOOKS finished is precisely the thing we did not measure.
    """
    pr = pr or {}
    raw = pr.get("mergeable_state")
    verdict = verdict_of(raw, read_ok=read_ok)

    check_state: Optional[str] = None
    if checks is not None:
        # ⚠️ THE EMPTY `pr={}` IS DELIBERATE AND LOAD-BEARING, not laziness.
        # `ci_settle.summarise` reports `conflict` AHEAD of `no_checks` when it
        # is handed a dirty PR — the right ordering THERE, because the conflict
        # explains the emptiness, and it would fold this module's own axis back
        # in HERE. Handing it a mergeability-free payload keeps its answer
        # purely about CHECKS, which is the only thing this call is asking for.
        # `pr_read_ok=True` is honest about that call: the payload is not
        # unread, it is deliberately narrowed.
        check_state = ci_settle.summarise(
            pr={}, pr_read_ok=True, checks=checks, checks_read_ok=True,
        )["state"]

    if check_state is None:
        misleading: Optional[bool] = None
    else:
        misleading = (verdict == CONFLICTED and check_state in _LOOKS_DONE)

    row: Dict[str, Any] = {
        "merge_verdict": verdict,
        "merge_possible": merge_possible(verdict),
        "mergeable_state": raw,
        "merge_state_note": merge_state_note(raw),
        "check_state": check_state,
        "misleading": misleading,
        "why": _why(verdict, raw, check_state, misleading),
    }
    return row


def _why(verdict: str, raw: Optional[str], check_state: Optional[str],
         misleading: Optional[bool]) -> str:
    if verdict == UNREADABLE:
        return ("the per-PR read FAILED — WE COULD NOT LOOK. This is not "
                "'GitHub has not computed it' and is certainly not 'mergeable'.")
    if verdict == NOT_COMPUTED:
        return (f"mergeable_state={raw!r} — GitHub computes mergeability lazily "
                f"and has not answered yet. Ask again shortly. NOT mergeable, "
                f"and not a finding either.")
    if verdict == CONFLICTED:
        base = ("MERGE CONFLICT with the base branch. NOBODY can merge this — "
                "not the manager, not auto-merge. The owner is the PR's AUTHOR "
                "and the remedy is to merge the base branch in, not to wait.")
        if misleading:
            return (base + f" ⚠️ AND ITS CHECKS ARE `{check_state}`, so it reads "
                    f"as READY on every surface that counts failures. This is "
                    f"the state that cost #11842 3.5h and #11738 12h+.")
        if check_state is not None:
            return base + (f" Its checks are `{check_state}`. ⚠️ GitHub builds "
                           f"pull_request runs against the MERGE ref, so a PR "
                           f"that went conflicted before CI ran carries ZERO "
                           f"checks by construction.")
        return base
    if verdict == CHECKS_BLOCKING:
        return ("a required check is missing or failing, or a review is "
                "required. NOT a conflict: the remedy is CI or a review, and "
                "this is the EXPECTED state of a PR that declares "
                "`landing: \"hold\"`. Counted, never paged on.")
    note = merge_state_note(raw) or "mergeable"
    return f"mergeable_state={raw!r} — {note}. The merge is possible now."


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts by verdict, plus the two numbers a reader acts on.

    ⚠️ `conflicted` and `checks_blocking` are counted SEPARATELY and never
    summed into one "unmergeable" figure. They have different owners.
    """
    by_verdict: Dict[str, int] = {s: 0 for s in ALL_STATES}
    for r in rows:
        v = r.get("merge_verdict")
        if v in by_verdict:
            by_verdict[v] += 1
    conflicted = [r for r in rows if r.get("merge_verdict") == CONFLICTED]
    return {
        "by_verdict": by_verdict,
        "conflicted": len(conflicted),
        # ⚠️ Counts only rows where `misleading` is TRUE. A row whose checks were
        # not fetched carries `None` and is excluded from BOTH the numerator and
        # any claim of absence — see `misleading_ungraded`.
        "misleading": sum(1 for r in conflicted if r.get("misleading") is True),
        "misleading_ungraded": sum(
            1 for r in conflicted if r.get("misleading") is None),
    }


# ---------------------------------------------------------------------------
# SELF-TEST — the policy is pure precisely so it is arguable HERE rather than
# against a live queue. Every case asserts in BOTH directions where a direction
# exists: the planted condition fires AND a clean input stays quiet. One
# direction proves a check runs, never that it discriminates.
# ---------------------------------------------------------------------------
def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    fails: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok   {label}")

    ok = {"name": "guards", "status": "completed", "conclusion": "success"}
    bad = {"name": "guards", "status": "completed", "conclusion": "failure"}
    run = {"name": "guards", "status": "in_progress", "conclusion": None}

    # --- every GitHub value lands on exactly one verdict ----------------------
    for raw, want in (("clean", MERGEABLE), ("has_hooks", MERGEABLE),
                      ("unstable", MERGEABLE), ("behind", MERGEABLE),
                      ("dirty", CONFLICTED), ("blocked", CHECKS_BLOCKING),
                      ("draft", CHECKS_BLOCKING), ("unknown", NOT_COMPUTED)):
        check(f"`{raw}` grades `{want}`", verdict_of(raw) == want)

    check("`behind` is MERGEABLE, not a warning (require-up-to-date is OFF here)",
          verdict_of("behind") == MERGEABLE)
    check("an UNRECOGNISED GitHub value is `not_computed`, never `mergeable`",
          verdict_of("some_new_state_github_added") == NOT_COMPUTED)
    check("an ABSENT mergeable_state is `not_computed`, never `mergeable`",
          verdict_of(None) == NOT_COMPUTED)
    check("a FAILED read is `unreadable` even when a string is in hand",
          verdict_of("clean", read_ok=False) == UNREADABLE)
    check("`unreadable` is NOT folded into `not_computed`",
          verdict_of("clean", read_ok=False) != verdict_of(None))
    check("case does not change the verdict", verdict_of("DIRTY") == CONFLICTED)

    # --- merge_possible is tri-state; `None` is not False --------------------
    check("mergeable  -> merge_possible True", merge_possible(MERGEABLE) is True)
    check("conflicted -> merge_possible False", merge_possible(CONFLICTED) is False)
    check("blocked    -> merge_possible False", merge_possible(CHECKS_BLOCKING) is False)
    check("not_computed -> merge_possible None (we do not know, not False)",
          merge_possible(NOT_COMPUTED) is None)
    check("unreadable   -> merge_possible None", merge_possible(UNREADABLE) is None)

    # --- THE MOTIVATING CASE, in both directions -----------------------------
    green_dirty = grade({"mergeable_state": "dirty"}, [ok, ok, ok, ok, ok])
    check("5 green checks + dirty is CONFLICTED, never mergeable",
          green_dirty["merge_verdict"] == CONFLICTED)
    check("5 green checks + dirty is flagged MISLEADING",
          green_dirty["misleading"] is True)
    check("the misleading row SAYS it reads as ready",
          "reads" in green_dirty["why"] and "READY" in green_dirty["why"])
    green_clean = grade({"mergeable_state": "clean"}, [ok, ok, ok, ok, ok])
    check("5 green checks + clean is NOT flagged misleading (the control)",
          green_clean["misleading"] is False
          and green_clean["merge_verdict"] == MERGEABLE)
    red_dirty = grade({"mergeable_state": "dirty"}, [ok, bad])
    check("RED checks + dirty is conflicted but NOT misleading — it does not "
          "look ready to anyone", red_dirty["merge_verdict"] == CONFLICTED
          and red_dirty["misleading"] is False)
    pend_dirty = grade({"mergeable_state": "dirty"}, [ok, run])
    check("PENDING checks + dirty is not misleading either",
          pend_dirty["misleading"] is False)
    # ⚠️ ADDED AFTER A MUTATION RUN FOUND ITS ABSENCE. Widening `_LOOKS_DONE` to
    # include `cancelled` passed the whole suite until this case existed — the
    # one planted defect of ten that went unnoticed. A cancelled check produced
    # NO VERDICT and this repo already refuses to read it as a pass, so a
    # cancelled+dirty PR must not be counted as one that looks ready.
    can_dirty = grade({"mergeable_state": "dirty"},
                      [{"name": "guards", "status": "completed",
                        "conclusion": "cancelled"}])
    check("CANCELLED checks + dirty is conflicted and NOT misleading "
          "(a cancelled check is not a pass)",
          can_dirty["merge_verdict"] == CONFLICTED
          and can_dirty["check_state"] == "cancelled"
          and can_dirty["misleading"] is False)
    zero_dirty = grade({"mergeable_state": "dirty"}, [])
    check("ZERO checks + dirty is conflicted and NOT misleading "
          "(no_checks never reads as green)",
          zero_dirty["merge_verdict"] == CONFLICTED
          and zero_dirty["misleading"] is False
          and zero_dirty["check_state"] == "no_checks")

    # --- not measuring is not measuring --------------------------------------
    no_checks_fetched = grade({"mergeable_state": "dirty"})
    check("omitting `checks` leaves check_state NULL, not 'green'",
          no_checks_fetched["check_state"] is None)
    check("omitting `checks` leaves `misleading` None — NOT False, because we "
          "did not look", no_checks_fetched["misleading"] is None)

    # --- a held PR must not be paged on --------------------------------------
    held = grade({"mergeable_state": "blocked"}, [ok])
    check("a `blocked` PR is checks_blocking, NOT conflicted",
          held["merge_verdict"] == CHECKS_BLOCKING)
    check("checks_blocking says it is the expected state of a held PR",
          "hold" in held["why"])

    # --- the summary keeps the two populations apart --------------------------
    s = summarise([green_dirty, green_clean, held, grade({"mergeable_state": "unknown"}),
                   grade({"mergeable_state": "dirty"})])
    check("the summary counts conflicted rows", s["conflicted"] == 2)
    check("the summary does NOT sum conflicted with checks_blocking",
          s["conflicted"] == 2 and s["by_verdict"][CHECKS_BLOCKING] == 1)
    check("an ungraded-for-misleading row inflates neither the count nor the "
          "claim of absence",
          s["misleading"] == 1 and s["misleading_ungraded"] == 1)
    check("every verdict has a key in by_verdict even at zero (so a reader can "
          "tell 'none' from 'not reported')",
          set(s["by_verdict"]) == set(ALL_STATES))

    # --- an unreadable row never reads as healthy -----------------------------
    unread = grade({"mergeable_state": "clean"}, [ok], read_ok=False)
    check("a failed read is `unreadable` even with clean+green in hand",
          unread["merge_verdict"] == UNREADABLE)
    check("`unreadable` says WE COULD NOT LOOK", "COULD NOT LOOK" in unread["why"])

    # --- prose is carried BESIDE the verdict, never instead of it -------------
    check("every recognised GitHub value has a note",
          all(merge_state_note(k) for k in _GITHUB_TO_VERDICT))
    check("an unrecognised value's note says so, rather than inventing one",
          "unrecognised" in (merge_state_note("zzz") or ""))
    check("no mergeable_state at all yields no note (rather than a fake one)",
          merge_state_note(None) is None)

    if not quiet:
        print(f"\n{'FAIL' if fails else 'PASS'}: "
              f"{len(fails)} failure(s) in the pr-mergeability policy")
        for f in fails:
            print(f"  FAIL {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
