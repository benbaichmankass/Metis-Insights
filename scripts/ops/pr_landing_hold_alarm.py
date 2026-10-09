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

**Recorded decisions are accepted (operator 2026-10-09, "Accept recorded
decisions"; pipeline item PI-20261004-GCFA5DOR-0005).** A human APPROVED review
was never how the manager merges: it merges Tier-2/3 PRs under a recorded
operator decision, or under the 2026-09-27 data-backed standing authorization
(CLAUDE.md § "Permission tiers"), and the alarm paged for every one (84 holds
in one week). A landing declaration may therefore carry::

    "recorded_decision": {
        "kind": "operator_decision" | "standing_authorization",
        "ref": "<checklist row id | pipeline id | repo path> ...",
        "evidence": "<repo path of the evidence record>"   # required for standing_authorization
    }

It is accepted only if it RESOLVES -- a presence-only field would be cheaper
to lie to than to satisfy (docs/CLAUDE-RULES-CANONICAL.md § "WHAT ENFORCES THIS
RULE"). ``ref`` must contain at least one token naming a checklist row, a
pipeline item or an existing repo file; a ``standing_authorization`` must cite
2026-09-27 in ``ref`` and an ``evidence`` path that exists in the tree. A
citation that does not resolve is an ALARM like no citation at all. Anything
uncited still pages.

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
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

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


_DECISION_KINDS = ("operator_decision", "standing_authorization")
_STANDING_AUTH_DATE = "2026-09-27"
_EVIDENCE_ROOTS = ("comms/", "docs/", "research/", "runtime_logs/")


def make_resolver(root: str = ".") -> Callable[[str], bool]:
    """Default resolver: does a token name a checklist row, a pipeline item
    or an existing repo file? Reads the tree under ``root`` only."""
    pipeline_ids: Optional[set] = None

    def _pipeline_ids() -> set:
        nonlocal pipeline_ids
        if pipeline_ids is None:
            pipeline_ids = set()
            d = os.path.join(root, "docs/claude/work/pipeline")
            try:
                for name in os.listdir(d):
                    with open(os.path.join(d, name), encoding="utf-8") as fh:
                        m = re.search(r'"id":\s*"([^"]+)"', fh.read())
                    if m:
                        pipeline_ids.add(m.group(1))
            except OSError:
                pass
        return pipeline_ids

    def resolve(token: str) -> bool:
        token = token.strip().strip(".,;:()[]\"'`")
        if not token or ".." in token or token.startswith("/"):
            return False
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", token) and os.path.isfile(
                os.path.join(root, "docs/claude/work/checklist", token + ".json")):
            return True
        if re.fullmatch(r"(PI|BL|OI)-[A-Za-z0-9-]+", token) and token in _pipeline_ids():
            return True
        return "/" in token and os.path.isfile(os.path.join(root, token))

    return resolve


def _recorded_decision(decl: Dict[str, Any],
                       resolve: Callable[[str], bool]) -> "tuple[bool, str]":
    """(accepted, why). ``why`` explains a rejection, or names the accepted citation."""
    rd = decl.get("recorded_decision")
    if rd is None:
        return False, ""
    if not isinstance(rd, dict) or rd.get("kind") not in _DECISION_KINDS:
        return False, f"recorded_decision.kind must be one of {_DECISION_KINDS}"
    ref = rd.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return False, "recorded_decision.ref is empty"
    if not any(resolve(t) for t in re.split(r"[\s,;]+", ref)):
        return False, (f"recorded_decision.ref {ref!r} names no checklist row, "
                       "pipeline item or repo file that exists")
    if rd["kind"] == "standing_authorization":
        if _STANDING_AUTH_DATE not in ref:
            return False, (f"a standing_authorization must cite the {_STANDING_AUTH_DATE} "
                           "data-backed authorization in ref")
        ev = rd.get("evidence")
        if not (isinstance(ev, str) and ev.startswith(_EVIDENCE_ROOTS) and resolve(ev)):
            return False, ("a standing_authorization needs recorded_decision.evidence "
                           "= an existing evidence record path under comms/ docs/ research/ runtime_logs/")
    return True, f"{rd['kind']} {ref.strip()!r}"


def grade(
    decl: Optional[Dict[str, Any]],
    *,
    decl_unreadable: bool,
    reviews: Optional[List[Dict[str, Any]]],
    reviews_unreadable: bool,
    pr_number: Optional[int] = None,
    pr_title: Optional[str] = None,
    resolve: Optional[Callable[[str], bool]] = None,
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
    # A resolving recorded decision needs no review, so check it BEFORE the
    # reviews list: an unreadable reviews fetch must not hide a valid citation.
    accepted, why = _recorded_decision(decl, resolve or make_resolver())
    if accepted:
        return Verdict(CLEAN, f"tier {tier} landing=hold, merged under a recorded {why}",
                       pr_number, pr_title)
    reject = why  # "" when no recorded_decision was supplied
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
        "APPROVED review recorded"
        + (f" and its recorded_decision was rejected: {reject}" if reject else
           " and no resolving recorded_decision"),
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

    # --- recorded decisions (operator 2026-10-09): accepted ONLY if they resolve
    known = {"BREAKOUT1-RETIRE", "PI-20261004-GCFA5DOR-0005",
             "comms/strategy_evidence/x.json"}
    rs = lambda t: t.strip(".,;:()") in known  # noqa: E731
    hold3 = {"tier": 3, "landing": "hold"}

    def rd(**kw: Any) -> Dict[str, Any]:
        return dict(hold3, recorded_decision=kw)

    v = grade(rd(kind="operator_decision", ref="BREAKOUT1-RETIRE popup 2026-10-07"),
              decl_unreadable=False, reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("operator_decision citing a real checklist row, zero reviews -> CLEAN", v.state == CLEAN)

    v = grade(rd(kind="operator_decision", ref="BREAKOUT1-RETIRE"), decl_unreadable=False,
              reviews=None, reviews_unreadable=True, resolve=rs)
    check("valid recorded decision + unreadable reviews -> still CLEAN (citation needs no review)",
          v.state == CLEAN)

    v = grade(rd(kind="operator_decision", ref="NO-SUCH-ROW"), decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("operator_decision citing nothing that exists -> ALARM (presence-only is not enough)",
          v.state == ALARM and "rejected" in v.reason)

    v = grade(rd(kind="operator_decision", ref="   "), decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("empty ref -> ALARM", v.state == ALARM)

    v = grade(rd(kind="standing_authorization", ref="2026-09-27 PI-20261004-GCFA5DOR-0005",
                 evidence="comms/strategy_evidence/x.json"),
              decl_unreadable=False, reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("standing_authorization + 2026-09-27 + existing evidence record -> CLEAN", v.state == CLEAN)

    v = grade(rd(kind="standing_authorization", ref="PI-20261004-GCFA5DOR-0005"),
              decl_unreadable=False, reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("standing_authorization without the 2026-09-27 date or evidence -> ALARM", v.state == ALARM)

    v = grade(rd(kind="standing_authorization", ref="2026-09-27 PI-20261004-GCFA5DOR-0005",
                 evidence="comms/missing.json"),
              decl_unreadable=False, reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("standing_authorization whose evidence path does not exist -> ALARM", v.state == ALARM)

    v = grade(rd(kind="vibes", ref="BREAKOUT1-RETIRE"), decl_unreadable=False,
              reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("unknown recorded_decision.kind -> ALARM", v.state == ALARM)

    v = grade(hold3, decl_unreadable=False, reviews=no_reviews, reviews_unreadable=False, resolve=rs)
    check("uncited hold + zero reviews still ALARMS (the detector is intact)", v.state == ALARM)

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
    ap.add_argument("--repo-root", default=".", help="tree the recorded_decision "
                     "citation is resolved against (default: cwd)")
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
        resolve=make_resolver(args.repo_root),
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
