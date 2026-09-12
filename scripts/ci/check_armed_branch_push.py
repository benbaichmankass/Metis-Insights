#!/usr/bin/env python3
#
# wiring: fired by `.github/workflows/armed-branch-push-watch.yml` on every push
# to a `claude/**` branch, and by NOTHING ELSE. It is a PURE grading policy with
# no network and no side effects; the workflow does the reading and the telling.
"""DID THE COMMIT YOU JUST PUSHED ACTUALLY LAND? — the armed-branch push watch.

THE DEFECT, MEASURED FIVE TIMES AND STILL UNOWNED
-------------------------------------------------
`claude-pr-automerge` arms native auto-merge on a pull request, and from that
moment the branch is **closed to new content** — but nothing says so, and
``git push`` reports success either way. A commit pushed to an armed branch
either rides the squash or is silently discarded, depending on a race the author
cannot see.

``BL-20260908-CLAUDE-PR-AUTOMERGE-OPENS-A-PR-WITH-NO-CI-AND-ARMS-A-RACE-THAT-DROPS-COMMITS-FOUR-SESSIONS-IN-ONE-DAY``
carries five instances. The two that pin the mechanism:

* **#11409** (2026-09-08) — auto-merge took head ``5590ef5b`` at 15:05:55Z while
  commit ``527a9b4c`` was *already pushed on the branch*. It did not reach
  `main`; verified by grep against a positive control, and re-landed as #11410.
* **#11846** (2026-09-12) — auto-merge squashed head ``63e55b8ef`` at 01:03:28Z;
  commit ``49653310e`` was pushed ~30 seconds LATER, to a branch whose PR was
  already merged. ``git push`` succeeded and nothing told anyone.

⚠️ **THE #11846 LOSS IS WHY THIS IS NOT A TIDINESS PROBLEM.** The dropped commit
carried the `SESSIONS.json` row and checklist item for a sub-session **that was
already spawned and running**. For roughly nine minutes a live lane existed on
the fleet with no row on `main` — verbatim the `MI-15-SESSIONS-REGISTRY-INCOMPLETE`
class the registry, the spawn gate and `handoff_check.py` all exist to prevent.
A manager arriving cold in that window would have found the lane invisible. It
also produced a false statement to the operator (*"the checklist is pushed"* —
true of the branch, false of `main`, and the operator's Workflow page reads
`main`).

WHY A DETECTOR AND NOT ONLY A DISCIPLINE
----------------------------------------
The row's own interim workaround is *"arm CI, verify four checks queued, then
add nothing"*. It is **unfollowable for a manager**, whose register pushes are
event-driven and cannot be batched behind an unknown-length CI run — and the
#11846 author had read both that guidance and CLAUDE.md's
``TREAT THE BRANCH AS CLOSED TO NEW CONTENT``, and pushed anyway, because a
*different* standing duty (the checklist is pushed BEFORE the manager answers)
demanded it. **The two rules are in direct conflict on an armed branch, and the
arming state is invisible at ``git push`` time.** That is the gap this closes:
not by adding a sixth restatement, but by making the state visible at the moment
it matters.

⚠️ **THIS PREVENTS NOTHING, AND SAYS SO.** It fires AFTER the push. Its value is
that #11846 went **nine minutes** undetected and was then found by hand; this
turns that into a failing run within seconds. The prevention half — opening the
PR under a PAT so CI attaches on the first head, and arming only after a check
exists — lives in `claude-pr-automerge.yml`, which `check_pr_landing.py::R12`
refuses to let a branch self-land. Different PR, different landing route.

SIX STATES, NEVER COLLAPSED
---------------------------
``no_pr``            the branch has no pull request we could find. Pushing is
                     safe, and this is the ordinary case for a branch being
                     built. Quiet.
``unarmed``          an OPEN pull request with auto-merge NOT enabled. Pushing
                     is safe: the branch is still open to content. Quiet.
``armed``            an OPEN pull request with auto-merge ENABLED. **WARN.** A
                     commit pushed now may or may not make the squash. Not an
                     error — arming is legitimate and the push may well land —
                     but the author is entitled to know the branch changed
                     meaning under them.
``dropped``          the pull request is MERGED and what merged is NOT what is
                     now at the head. **ERROR.** Committed work is sitting on a
                     branch that will never land, and a merged PR is finished
                     and must not be reused — the remedy is a FRESH branch.
``merged_contained`` the pull request is MERGED and the merged head IS the
                     pushed sha. Nothing was lost. Quiet.
``unknown``          **WE COULD NOT LOOK** — the pull-request read failed, or it
                     is merged and carries no head sha to compare. Never folded
                     into `no_pr`, which is a real observation that no PR
                     exists, nor into `merged_contained`, which is a real
                     observation that nothing was lost.

⚠️ CONTAINMENT IS DECIDED BY THE MERGED HEAD SHA, NOT BY ANCESTRY
-----------------------------------------------------------------
**This repo squash-merges**, so `merge_commit_sha` is a NEW commit that has none
of the branch's commits as ancestors. `git merge-base --is-ancestor` therefore
answers NO for *every* correctly-merged commit in the repo, and a detector built
on it would report `dropped` on all of them — a false alarm on the healthy case,
which is the desensitised-alarm P1 this repo already measured at 202 of 376
CRITICALs being one un-latched condition.

The same trap is recorded from the other direction on the board: MI-277 verified
its own close-out on `main` **"by CONTENT, not ancestry — the repo squash-merges,
so `merge-base --is-ancestor` correctly answers NO and would read as *never
landed*"**.

So the question asked here is the one the API can answer exactly: **is the head
GitHub squashed the head that is there now?** `pr.head.sha` is what was merged;
the pushed sha is what is there now. Equal means nothing was lost.

⚠️ A DIFFERENCE IS NOT ALWAYS A DROP, and the state name says what was observed
rather than what it means. A force-push back to an older commit also produces a
difference. Both deserve a human look, and neither is "fine".

Self-test:  python3 scripts/ci/check_armed_branch_push.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

NO_PR = "no_pr"
UNARMED = "unarmed"
ARMED = "armed"
DROPPED = "dropped"
MERGED_CONTAINED = "merged_contained"
UNKNOWN = "unknown"

ALL_STATES = (NO_PR, UNARMED, ARMED, DROPPED, MERGED_CONTAINED, UNKNOWN)

#: The states that must reach a human. `armed` is a WARNING and `dropped` an
#: ERROR, and they are deliberately different: arming is legitimate and usually
#: harmless, while a drop is committed work that will never land.
WARN_STATES = (ARMED,)
ERROR_STATES = (DROPPED,)

#: ⚠️ `unknown` is REPORTED but neither warns nor errors. A read failure is not
#: evidence about the branch, and paging on it would make an unreliable API into
#: a daily alarm — but returning it silently would make a permanently-blind
#: watcher indistinguishable from a quiet one, so it lands in the output.
EXIT_QUIET, EXIT_WARN, EXIT_ERROR = 0, 0, 1


def grade(pushed_sha: Optional[str], pr: Optional[Dict[str, Any]],
          pr_read_ok: bool = True) -> Dict[str, Any]:
    """PURE. The pushed sha and the branch's pull request in, one row out.

    ``pr`` is ``None`` when the branch has no pull request. ``pr_read_ok=False``
    means the LOOKUP ITSELF failed — which is `unknown`, never `no_pr`. The two
    are passed separately rather than inferred from ``is None`` for the reason
    `ci_settle` gives for the same split: *a successful read that legitimately
    returns nothing and a read that never happened are different facts.*
    """
    if not pr_read_ok:
        return _row(UNKNOWN, pushed_sha, pr,
                    "the pull-request lookup FAILED — WE COULD NOT LOOK. This "
                    "is not 'the branch has no PR', and it is certainly not "
                    "'nothing was lost'.")
    if not pushed_sha:
        return _row(UNKNOWN, pushed_sha, pr,
                    "no pushed sha was supplied, so there is nothing to compare "
                    "the merged head against.")
    if pr is None:
        return _row(NO_PR, pushed_sha, pr,
                    "the branch has no pull request, so nothing can have merged "
                    "under this push. Pushing is safe.")

    merged = bool(pr.get("merged") or pr.get("merged_at"))
    if merged:
        head = ((pr.get("head") or {}).get("sha")) or pr.get("head_sha")
        if not head:
            return _row(UNKNOWN, pushed_sha, pr,
                        "the pull request is MERGED and carries no head sha, so "
                        "whether this push is in it CANNOT be established. Not "
                        "graded as contained — that would assert an observation "
                        "nobody made.")
        if head == pushed_sha:
            return _row(MERGED_CONTAINED, pushed_sha, pr,
                        f"the pull request is merged and the merged head IS this "
                        f"push ({_short(head)}). Nothing was lost.")
        return _row(
            DROPPED, pushed_sha, pr,
            f"⚠️ THE PULL REQUEST IS ALREADY MERGED, and what merged is "
            f"{_short(head)} while the branch head is now {_short(pushed_sha)}. "
            f"THIS PUSH IS NOT IN `main`. A merged pull request is FINISHED and "
            f"must not be reused, so the remedy is a FRESH branch off the "
            f"current `main` carrying this commit — not another push here. "
            f"⚠️ `git push` reported success, which is a fact about the BRANCH "
            f"and not about `main`; do not report this work as landed. "
            f"(A force-push back to an older commit produces the same reading "
            f"and also deserves a look.)")

    state = str(pr.get("state") or "open").lower()
    if state != "open":
        return _row(UNKNOWN, pushed_sha, pr,
                    f"the pull request is `{state}` and not merged, so whether "
                    f"this push can ever land is not decidable from here.")

    if _auto_merge_enabled(pr):
        return _row(
            ARMED, pushed_sha, pr,
            "AUTO-MERGE IS ENABLED on this branch's pull request, so the branch "
            "is closed to new content: GitHub squashes whatever head is current "
            "the moment the required checks go green, and a commit pushed now "
            "may or may not be in it. This is a WARNING, not an error — the push "
            "may well land. It exists because the arming state is invisible at "
            "`git push` time, which is how five commits were lost.")
    return _row(UNARMED, pushed_sha, pr,
                "the pull request is open and auto-merge is NOT enabled, so the "
                "branch is still open to content. Pushing is safe.")


def _auto_merge_enabled(pr: Dict[str, Any]) -> bool:
    """⚠️ ABSENT and NULL both mean *not enabled*; anything else means enabled.

    GitHub returns `auto_merge: null` when it is off and an object when it is
    on. A truthiness test on the object is correct and a `in pr` test is not —
    the key is present-and-null on every unarmed PR.
    """
    return pr.get("auto_merge") is not None


def _short(sha: Optional[str]) -> str:
    return (sha or "")[:8] or "?"


def _row(state: str, pushed_sha: Optional[str], pr: Optional[Dict[str, Any]],
         why: str) -> Dict[str, Any]:
    return {
        "state": state,
        "pushed_sha": pushed_sha,
        "pr": (pr or {}).get("number"),
        "pr_state": (pr or {}).get("state"),
        "merged_head_sha": ((pr or {}).get("head") or {}).get("sha"),
        "auto_merge": _auto_merge_enabled(pr) if pr else None,
        "warn": state in WARN_STATES,
        "error": state in ERROR_STATES,
        "why": why,
    }


def render(row: Dict[str, Any], branch: str = "") -> str:
    icon = {DROPPED: "::error::", ARMED: "::warning::"}.get(row["state"], "")
    pr = f"#{row['pr']}" if row.get("pr") else "(no PR)"
    head = f" branch {branch}" if branch else ""
    return (f"{icon}armed-branch-push: {row['state'].upper()}{head} "
            f"{pr} head={_short(row['pushed_sha'])} — {row['why']}")


def exit_code(row: Dict[str, Any]) -> int:
    if row["error"]:
        return EXIT_ERROR
    return EXIT_QUIET


# ---------------------------------------------------------------------------
# SELF-TEST — the policy is pure precisely so it is arguable HERE rather than
# against a live push. Every case asserts in BOTH directions where a direction
# exists: the planted condition fires AND a clean input stays quiet.
# ---------------------------------------------------------------------------
def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    fails: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok   {label}")

    A, B = "63e55b8ef" + "0" * 31, "49653310e" + "0" * 31

    def pr(**kw):
        base = {"number": 11846, "state": "open", "merged": False,
                "head": {"sha": A}, "auto_merge": None}
        base.update(kw)
        return base

    # ── THE #11846 CASE, REPLAYED. Merged at head A; B pushed 30s later. ──
    drop = grade(B, pr(merged=True, state="closed"))
    check("a push to an ALREADY-MERGED PR whose merged head differs is `dropped`",
          drop["state"] == DROPPED)
    check("…and it is an ERROR, not a warning", drop["error"] and not drop["warn"])
    check("…and it says the remedy is a FRESH branch, not another push",
          "FRESH branch" in drop["why"])
    check("…and it names BOTH shas so the reader can check",
          _short(A) in drop["why"] and _short(B) in drop["why"])
    check("…and it says `git push` succeeding is not evidence the work landed",
          "not about `main`" in drop["why"])
    check("the exit code is non-zero so the run FAILS and reaches the alert channel",
          exit_code(drop) == EXIT_ERROR)
    check("and it renders as a GitHub ::error:: annotation",
          render(drop).startswith("::error::"))

    # ── THE CONTROL: the same PR, merged at the head that is actually there. ──
    fine = grade(A, pr(merged=True, state="closed"))
    check("a merged PR whose merged head IS the pushed sha is quiet",
          fine["state"] == MERGED_CONTAINED and not fine["error"] and not fine["warn"])
    check("…and exits 0", exit_code(fine) == EXIT_QUIET)

    # ⚠️ THE ANCESTRY TRAP, ASSERTED. A squash merge shares no commit with the
    # branch, so any ancestry-based containment test would report `dropped` on
    # the healthy case above. This asserts the policy is keyed on the HEAD SHA.
    check("containment is decided by the MERGED HEAD SHA, so a squash merge of "
          "the current head is NOT reported as a drop",
          grade(A, pr(merged=True, state="closed"))["state"] == MERGED_CONTAINED)

    # ⚠️ AND THE COMPARISON IS EXACT, NOT ABBREVIATED. Added after a mutation
    # run: replacing `head == pushed_sha` with a 4-character prefix comparison
    # passed the entire suite, because the two real shas in these fixtures
    # differ in their FIRST character. A loosened comparison is the direction
    # that manufactures `merged_contained` — i.e. reports a drop as safe — so
    # the property is pinned on shas that agree on everything but the tail.
    _p1 = "abc12345" + "1" * 32
    _p2 = "abc12345" + "2" * 32
    check("two shas sharing an 8-character prefix are NOT the same commit — "
          "containment is full-sha equality, never an abbreviation",
          grade(_p2, pr(merged=True, state="closed", head={"sha": _p1}))["state"]
          == DROPPED)

    # ── ARMED vs UNARMED, both directions ──────────────────────────────────
    armed = grade(B, pr(auto_merge={"merge_method": "SQUASH"}))
    check("an OPEN PR with auto-merge enabled is `armed`", armed["state"] == ARMED)
    check("…and WARNS rather than erroring — arming is legitimate",
          armed["warn"] and not armed["error"] and exit_code(armed) == EXIT_QUIET)
    check("…and renders as a ::warning:: annotation",
          render(armed).startswith("::warning::"))
    check("…and says the branch is closed to new content",
          "closed to new content" in armed["why"])

    unarmed = grade(B, pr())
    check("an OPEN PR with auto-merge OFF is `unarmed` and quiet",
          unarmed["state"] == UNARMED and not unarmed["warn"] and not unarmed["error"])
    check("⚠️ `auto_merge: null` is NOT enabled — the key is present-and-null on "
          "every unarmed PR, so a `in pr` test would arm every one of them",
          _auto_merge_enabled({"auto_merge": None}) is False
          and _auto_merge_enabled({}) is False
          and _auto_merge_enabled({"auto_merge": {"merge_method": "SQUASH"}}) is True)

    # ── NO PR is a real observation; a failed read is not ──────────────────
    none = grade(B, None)
    check("a branch with no PR is `no_pr` and quiet", none["state"] == NO_PR)
    blind = grade(B, None, pr_read_ok=False)
    check("a FAILED lookup is `unknown`, never `no_pr`", blind["state"] == UNKNOWN)
    check("…and `unknown` and `no_pr` are different states",
          blind["state"] != none["state"])
    check("…and `unknown` says WE COULD NOT LOOK", "COULD NOT LOOK" in blind["why"])
    check("…and does NOT fail the run (a flaky API must not become a daily alarm)",
          exit_code(blind) == EXIT_QUIET and not blind["error"])
    check("a merged PR with NO head sha is `unknown`, never `merged_contained`",
          grade(B, {"number": 1, "merged": True, "head": {}})["state"] == UNKNOWN)
    check("a CLOSED-unmerged PR is `unknown`, not `no_pr` and not `dropped`",
          grade(B, pr(state="closed"))["state"] == UNKNOWN)
    check("a missing pushed sha is `unknown`, never a pass",
          grade(None, pr())["state"] == UNKNOWN)

    # ── every state is reachable, so none is decorative ────────────────────
    reached = {
        grade(B, pr(merged=True, state="closed"))["state"],
        grade(A, pr(merged=True, state="closed"))["state"],
        grade(B, pr(auto_merge={"m": 1}))["state"],
        grade(B, pr())["state"],
        grade(B, None)["state"],
        grade(B, None, pr_read_ok=False)["state"],
    }
    check("all six states are reachable from a real input",
          reached == set(ALL_STATES))

    if not quiet:
        print(f"\n{'FAIL' if fails else 'PASS'}: "
              f"{len(fails)} failure(s) in the armed-branch-push policy")
        for f in fails:
            print(f"  FAIL {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--pushed-sha", default=None)
    ap.add_argument("--branch", default="")
    ap.add_argument("--pr-json", default=None,
                    help="Path to the branch's pull request as JSON (from `gh "
                         "api repos/OWNER/REPO/pulls?head=...`, first element), "
                         "or the literal string `none` when the lookup "
                         "SUCCEEDED and found no PR. ⚠️ OMITTING IT grades "
                         "`unknown` — we did not look — and NEVER `no_pr`.")
    args = ap.parse_args(argv)

    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    pr: Optional[Dict[str, Any]] = None
    read_ok = False
    if args.pr_json == "none":
        read_ok = True
    elif args.pr_json:
        try:
            from pathlib import Path
            raw = json.loads(Path(args.pr_json).read_text(encoding="utf-8"))
            if isinstance(raw, list):
                raw = raw[0] if raw else None
            pr = raw if isinstance(raw, dict) else None
            read_ok = True
        except (OSError, json.JSONDecodeError, IndexError) as exc:
            print(f"could not read --pr-json: {exc}", file=sys.stderr)
            read_ok = False

    row = grade(args.pushed_sha, pr, pr_read_ok=read_ok)
    print(render(row, args.branch))
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return exit_code(row)


if __name__ == "__main__":
    raise SystemExit(main())
