#!/usr/bin/env python3
"""JC-CA-04 (docs/audits/code-audit-2026-09-27.md §6, `A09-hold-merge-unenforced`,
operator decision 2026-09-28: "Post-merge alarm", option B only).

**The finding.** Nothing mechanical stops a session merging a Tier-2/3 PR
declared ``landing: "hold"`` in its ``.github/pr-landing/<slug>.json``: it can
be green on every required check, and merge tools are pre-allowed. `hold`
means "a human decides" — it is not itself enforced.

**This is the detector, not the gate.** Option A (a branch-protection /
CODEOWNERS block on TIER3_PATHS) would enforce it; the operator chose option B
only: catch it AFTER THE FACT and page loudly, cheap and non-disruptive,
rather than add merge friction to every legitimate human-approved Tier-2/3
PR. `.github/workflows/pr-landing-hold-merge-alarm.yml` runs this on every
push to `main`, finds the PR(s) associated with the pushed commit(s) via the
GitHub API, reads each one's `.github/pr-landing/<slug>.json` declaration
(read from the pushed tree — the file is committed by the branch, same as
`check_pr_landing.py` reads it pre-merge) and its GitHub PR reviews, and pages
the operator via the `send-ping` system-action (docs/claude/system-actions.md)
when a tier 2/3 `hold` PR merged with no human `APPROVED` review recorded.

**Pure decision logic only — no network calls in this module.** The workflow
does every GitHub API call (finding the PR for a pushed commit, fetching the
declaration blob, fetching the reviews list) and hands the results to this
script as JSON, exactly the split `pr_mergeability.py` already uses ("the
policy is pure precisely so it is arguable HERE rather than against a live
queue").

**Three states, never collapsed** (docs/CLAUDE-RULES-CANONICAL.md §
"Collapsed states"): ``clean`` (no alarm — either out of scope, or a human
APPROVED review is on record), ``alarm`` (a real finding — page the
operator), ``unreadable`` (we could not establish one of the two facts this
needs — e.g. the declaration or the reviews list could not be fetched/parsed).
``unreadable`` must NEVER be read as `clean`: a post-merge page that silently
stays quiet because ITS OWN reads failed is the exact "desensitised alarm" bug
class docs/CLAUDE-RULES-CANONICAL.md's § "Green is not evidence" names.

Self-test:  python3 scripts/ops/pr_landing_hold_alarm.py --self-test
Real use:   python3 scripts/ops/pr_landing_hold_alarm.py \\
              --decl-json <path|MISSING|UNREADABLE> \\
              --reviews-json <path|UNREADABLE> \\
              --pr-number 13500 --pr-title "..."
Exit codes: 0 clean, 2 alarm (message on stdout), 3 unreadable, 1 self-test
failure.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

CLEAN = "clean"
ALARM = "alarm"
UNREADABLE = "unreadable"
STATES = (CLEAN, ALARM, UNREADABLE)

# Only these two landings are ever in scope. `landing: "self"` (R15,
# credential-backed self-land) and `landing: "mandate"` (R16, operator-armed
# mandate autoland) both already carry their own separation properties,
# checked pre-merge by check_pr_landing.py / check_mandate_autoland.py — this
# alarm is specifically for the THIRD case those checks do not enforce:
# `landing: "hold"`, which means "wait for a human" and is not itself wired
# to anything that waits.
_ALARM_LANDING = "hold"
_ALARM_TIERS = (2, 3)


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str
    pr_number: Optional[int] = None
    pr_title: Optional[str] = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"state must be one of {STATES}, got {self.state!r}")

    def ping_message(self) -> str:
        if self.state != ALARM:
            raise ValueError("ping_message() is only defined for an ALARM verdict")
        title = f" ({self.pr_title})" if self.pr_title else ""
        pr = f"#{self.pr_number}" if self.pr_number is not None else "an unknown PR"
        return f"{pr}{title} merged with no recorded approval: {self.reason}"


def _is_human_approval(review: Dict[str, Any]) -> bool:
    if review.get("state") != "APPROVED":
        return False
    user = review.get("user") or {}
    if user.get("type") == "Bot":
        return False
    login = str(user.get("login") or "")
    return not login.endswith("[bot]")


def grade(
    decl: Optional[Dict[str, Any]],
    *,
    decl_unreadable: bool,
    reviews: Optional[List[Dict[str, Any]]],
    reviews_unreadable: bool,
    pr_number: Optional[int] = None,
    pr_title: Optional[str] = None,
) -> Verdict:
    """The whole policy. Pure — every input is already fetched.

    ``decl`` is the parsed `.github/pr-landing/<slug>.json` for the merged
    PR's branch (``None`` with ``decl_unreadable=False`` means the file
    genuinely does not exist — itself worth an alarm, since a merged PR with
    no declaration means the pre-merge guard was bypassed, not that nothing
    is wrong). ``decl_unreadable=True`` means the fetch/parse itself failed —
    graded ``unreadable``, never folded into either other state.
    """
    if decl_unreadable:
        return Verdict(UNREADABLE, "could not read the merged PR's "
                        ".github/pr-landing declaration",
                        pr_number, pr_title)
    if decl is None:
        return Verdict(
            ALARM,
            "no .github/pr-landing declaration exists for this branch — "
            "check_pr_landing.py should have refused this merge; the guard "
            "itself may have been bypassed",
            pr_number, pr_title,
        )
    tier = decl.get("tier")
    landing = decl.get("landing")
    if tier not in _ALARM_TIERS or landing != _ALARM_LANDING:
        return Verdict(
            CLEAN,
            f"tier={tier!r} landing={landing!r} — out of this alarm's scope "
            "(only tier 2/3 + landing: \"hold\" is checked; \"self\" and "
            "\"mandate\" carry their own pre-merge separation checks)",
            pr_number, pr_title,
        )
    if reviews_unreadable:
        return Verdict(UNREADABLE, "could not read the merged PR's reviews list",
                        pr_number, pr_title)
    if reviews is None:
        return Verdict(UNREADABLE, "no reviews payload supplied", pr_number, pr_title)
    if any(_is_human_approval(r) for r in reviews):
        return Verdict(
            CLEAN,
            f"tier {tier} landing=hold, but a human APPROVED review is on record",
            pr_number, pr_title,
        )
    return Verdict(
        ALARM,
        f"tier {tier} PR declared landing: \"hold\" merged with no human "
        "APPROVED review recorded",
        pr_number, pr_title,
    )


# ---------------------------------------------------------------------------
# SELF-TEST — same shape as pr_mergeability.py::_self_test: every case that
# has a direction asserts BOTH ways (the planted condition fires AND a clean
# input beside it stays quiet), because one direction alone only proves the
# check runs, never that it discriminates.
# ---------------------------------------------------------------------------
def _self_test(quiet: bool = False) -> "tuple[bool, List[str]]":
    fails: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok   {label}")

    human_approval = [{"state": "APPROVED", "user": {"login": "benbaichmankass", "type": "User"}}]
    no_reviews: List[Dict[str, Any]] = []
    bot_approval = [{"state": "APPROVED", "user": {"login": "github-actions[bot]", "type": "Bot"}}]
    changes_requested = [{"state": "CHANGES_REQUESTED", "user": {"login": "benbaichmankass", "type": "User"}}]

    # --- the exact bug this exists to catch: tier 2/3 hold, no approval ----
    v = grade({"tier": 3, "landing": "hold"}, decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False)
    check("tier 3 + hold + zero reviews -> ALARM", v.state == ALARM)

    v = grade({"tier": 2, "landing": "hold"}, decl_unreadable=False,
              reviews=changes_requested, reviews_unreadable=False)
    check("tier 2 + hold + CHANGES_REQUESTED only -> ALARM (no APPROVED)", v.state == ALARM)

    # --- planted defect: a BOT's own approval must not count ----------------
    v = grade({"tier": 3, "landing": "hold"}, decl_unreadable=False,
              reviews=bot_approval, reviews_unreadable=False)
    check("tier 3 + hold + only a [bot] APPROVED review -> STILL ALARM "
          "(a bot approving itself is the self-land failure this exists to catch)",
          v.state == ALARM)

    # --- the clean case beside it, so the check above proves discrimination -
    v = grade({"tier": 3, "landing": "hold"}, decl_unreadable=False,
              reviews=human_approval, reviews_unreadable=False)
    check("tier 3 + hold + a human APPROVED review -> CLEAN", v.state == CLEAN)

    # --- out of scope: tier 1 never needs this gate --------------------------
    v = grade({"tier": 1, "landing": "hold"}, decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False)
    check("tier 1 + hold -> CLEAN (tier 1 self-lands under R5, no record needed)",
          v.state == CLEAN)

    # --- out of scope: landing self/mandate carry their own pre-merge checks -
    v = grade({"tier": 2, "landing": "self", "approved_by": "x"}, decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False)
    check("tier 2 + landing:self -> CLEAN (R15 already checked this pre-merge)",
          v.state == CLEAN)

    v = grade({"tier": 3, "landing": "mandate", "mandate": "MD-X"}, decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False)
    check("tier 3 + landing:mandate -> CLEAN (R16 already checked this pre-merge)",
          v.state == CLEAN)

    # --- missing declaration is itself an ALARM, never silently clean -------
    v = grade(None, decl_unreadable=False, reviews=no_reviews, reviews_unreadable=False)
    check("no declaration at all -> ALARM (the pre-merge guard should have refused this)",
          v.state == ALARM)

    # --- unreadable is its own state, never folded into clean OR alarm ------
    v = grade(None, decl_unreadable=True, reviews=None, reviews_unreadable=True)
    check("declaration fetch failed -> UNREADABLE, not CLEAN", v.state == UNREADABLE)
    check("declaration fetch failed -> UNREADABLE, not ALARM", v.state != ALARM)

    v = grade({"tier": 3, "landing": "hold"}, decl_unreadable=False,
              reviews=None, reviews_unreadable=True)
    check("declaration read OK but reviews fetch failed -> UNREADABLE (never "
          "silently CLEAN just because no APPROVED review was SEEN)",
          v.state == UNREADABLE)

    # --- the ping message names the PR and the reason -----------------------
    v = grade({"tier": 3, "landing": "hold"}, decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False,
              pr_number=13500, pr_title="flip account mode")
    msg = v.ping_message()
    check("ping message names the PR number", "#13500" in msg)
    check("ping message names the PR title", "flip account mode" in msg)

    if not quiet:
        print(f"\n{'FAIL' if fails else 'PASS'}: {len(fails)} failure(s) in "
              "the pr-landing-hold-merge-alarm policy")
        for f in fails:
            print(f"  FAIL {f}")
    return (not fails), fails


def _load_decl(raw: str) -> "tuple[Optional[Dict[str, Any]], bool]":
    """Returns (decl, unreadable). ``raw`` is a path, or the literal
    ``MISSING`` (file confirmed absent) or ``UNREADABLE`` (fetch/parse
    failed — the workflow could not tell)."""
    if raw == "MISSING":
        return None, False
    if raw == "UNREADABLE":
        return None, True
    try:
        with open(raw, "r", encoding="utf-8") as fh:
            return json.load(fh), False
    except (OSError, json.JSONDecodeError):
        return None, True


def _load_reviews(raw: str) -> "tuple[Optional[List[Dict[str, Any]]], bool]":
    if raw == "UNREADABLE":
        return None, True
    try:
        with open(raw, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None, True
    if not isinstance(data, list):
        return None, True
    return data, False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--decl-json", help="path to the fetched .github/pr-landing "
                     "declaration JSON, or the literal MISSING / UNREADABLE")
    ap.add_argument("--reviews-json", help="path to the fetched PR reviews JSON "
                     "array, or the literal UNREADABLE")
    ap.add_argument("--pr-number", type=int, default=None)
    ap.add_argument("--pr-title", default=None)
    args = ap.parse_args(argv)

    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    if not args.decl_json or not args.reviews_json:
        ap.error("--decl-json and --reviews-json are required outside --self-test")

    decl, decl_unreadable = _load_decl(args.decl_json)
    reviews, reviews_unreadable = _load_reviews(args.reviews_json)
    verdict = grade(
        decl, decl_unreadable=decl_unreadable,
        reviews=reviews, reviews_unreadable=reviews_unreadable,
        pr_number=args.pr_number, pr_title=args.pr_title,
    )
    print(f"pr-landing-hold-merge-alarm: state={verdict.state} reason={verdict.reason}")
    if verdict.state == ALARM:
        print(verdict.ping_message())
        return 2
    if verdict.state == UNREADABLE:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
