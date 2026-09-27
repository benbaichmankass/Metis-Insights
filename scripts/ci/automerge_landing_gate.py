#!/usr/bin/env python3
"""Never arm auto-merge on a PR whose OWN landing declaration says hold.

THE MEASURED DEFECT (E63, PR #12858, 2026-09-24T13:30:09Z)
------------------------------------------------------------------------------
`claude-pr-automerge.yml` opened PR #12858, whose HEAD commit's own message was
*"Integration within the manager surface: landing declaration (hold, R12) ..."*
and whose `.github/pr-landing/tender-mayer-2g1vtq.json` read

    {"tier": 1, "landing": "hold", "hold_reason": "changes_landing_machinery", ...}

The run log for that exact push (job 107654502596, 2026-09-24T13:30:21Z) reads:

    arming gate: attached — 1 check run(s) other than `open-and-automerge` ...
    auto-merge enabled on #12858 — merges when CI is green

`pr-landing-guard` (`scripts/ci/check_pr_landing.py`) separately graded that
same head `declared_hold` and exited **0** — correctly, because a hold is a
valid, non-blocking declaration; a human is meant to merge it by hand. Auto-merge
then waited only on required checks going green, which they did, and
`github-actions[bot]` squash-merged it at 13:53:00Z with no human read.

**The root cause is not that some check failed to fire — it is that nothing in
`claude-pr-automerge.yml` ever reads `.github/pr-landing/<slug>.json` at all.**
The request gate (did this branch touch its arming file?) and the arming gate
(did a real check attach?) are both blind to the PR's own stated intent to
land itself vs. hold for a human. A PR can therefore be armed and merged while
declaring, in its own committed record, that it must not be.

WHY THE OTHER TWO GATES DID NOT CATCH IT
------------------------------------------------------------------------------
This branch (`claude/tender-mayer-2g1vtq`) is a reused MANAGER LANE branch:
PR #12838 merged from it at 12:59:20Z, PR #12854 merged from it at 13:25:51Z,
and PR #12858 was then opened from a commit still carrying a STALE copy of
`.github/pr-automerge-requests/tender-mayer-2g1vtq.txt` — literally the text
"armed by session_...for PR #12838", never rewritten for this new PR. Because
`main`'s tip had moved on (PR #12854 rewrote that same shared file to "...2026-09-24
decisions batch"), the branch's stale blob no longer matched `main`'s tip, so
the request gate's own "differs from `main`" check read as "this branch just
asked" when in truth nobody had touched the file since #12838. That half of
the bug is fixed separately, in `claude-pr-automerge.yml`, by comparing against
the PR's merge-base rather than `main`'s ever-moving tip. **This module is the
other, independent half: even a genuine, deliberate arming request must still
be refused outright when the PR's own landing declaration says hold.** Neither
gate is a substitute for the other.

THE THIRD LANDING VALUE (2026-09-25)
------------------------------------------------------------------------------
`check_pr_landing.py` R16 added `landing: "mandate"` — the one Tier-3 shape that
lands itself, a mandate-authorized Stage-2 roster CUT graded clause by clause by
`scripts/ci/check_mandate_autoland.py`. This gate cannot re-derive those clauses
(it is pure, and they need two git trees), so the caller passes their verdict in
as `autoland_ok` and the gate refuses unless it is exactly `True`.

⚠️ `None` IS NOT `False`, AND COLLAPSING THEM WOULD BE THE WHOLE BUG. A caller
that never evaluated the clauses — `claude-pr-automerge.yml`, which sees only
`claude/**` branches and has no idea what a mandate is — must produce
`mandate_unchecked`, not a silent arming. The two refuse identically today; they
are separate so that a green log never reads as "the clauses passed" when
nothing looked.

THE RULE
------------------------------------------------------------------------------
`arm` is True only when `.github/pr-landing/<slug>.json`, read at the pushed
head, is absent (permissive default for branches that predate the declaration
convention entirely — `pr-landing-guard` itself grandfathers exactly this case
as `undeclared_predates_guard`) or present with `landing == "self"`. Anything
else — `"hold"`, a typo, a null, a missing field — REFUSES. There is no
override: the cheapest way past this gate must be to change the declaration
to `"self"`, which is the one act that also makes `pr-landing-guard`'s own
R5/R10/R13 checks start applying for real.

STATES, NEVER COLLAPSED
------------------------------------------------------------------------------
  ``self``      declaration found, parsed, `landing == "self"` — arm.
  ``not_self``  declaration found, parsed, `landing` is anything else
                (`"hold"`, missing, wrong type) — REFUSE.
  ``absent``    no declaration file at the pushed head at all — arm. This is
                the permissive default `pr-landing-guard` itself uses for
                branches that predate the whole convention; this gate does not
                invent a stricter rule than the one already enforced as a
                required check.
  ``mandate``           declaration says `landing: "mandate"` AND the caller
                        passed `autoland_ok: true` — arm. The one Tier-3 route.
  ``mandate_refused``   `landing: "mandate"` and the autoland clauses FAILED.
  ``mandate_unchecked`` ⚠️ **we could not look** — `landing: "mandate"` and the
                        caller passed no verdict at all. REFUSE.
  ``unreadable``  ⚠️ **we could not look** — the read errored (anything other
                than a clean 404) or the blob was not valid JSON. REFUSE. A
                failed read must never be waved through as "no declaration".

Run ``--self-test`` to plant the #12858 shape and prove the gate refuses it.
"""
from __future__ import annotations

import argparse
import json
import sys

SELF = "self"
NOT_SELF = "not_self"
ABSENT = "absent"
UNREADABLE = "unreadable"
MANDATE = "mandate"
MANDATE_REFUSED = "mandate_refused"
MANDATE_UNCHECKED = "mandate_unchecked"
ALL_STATES = (SELF, NOT_SELF, ABSENT, UNREADABLE, MANDATE, MANDATE_REFUSED,
              MANDATE_UNCHECKED)

#: The `landing:` value `check_pr_landing.py` R16 routes to
#: `scripts/ci/check_mandate_autoland.py`. Spelled here rather than imported so
#: this module stays pure and dependency-free.
MANDATE_LANDING = "mandate"


def grade(*, found: bool, parse_ok: bool, landing, autoland_ok=None) -> dict:
    """Pure. Nothing here talks to GitHub — the caller already did that.

    `autoland_ok` is the `check_mandate_autoland.py` verdict for a
    `landing: "mandate"` PR: `True` (every clause held), `False` (a clause
    failed), or `None` (the caller did not evaluate them — never read as pass).
    """
    if not found:
        return {
            "state": ABSENT, "arm": True, "landing": None,
            "why": "no .github/pr-landing/<slug>.json at the pushed head. "
                   "Permissive by design: pr-landing-guard itself grandfathers "
                   "a branch with no declaration as undeclared_predates_guard, "
                   "and this gate does not invent a stricter rule than the "
                   "required check already enforces.",
        }
    if not parse_ok:
        return {
            "state": UNREADABLE, "arm": False, "landing": None,
            "why": "the declaration file exists but could not be read as JSON "
                   "(a failed API read, or a malformed blob). That is 'we did "
                   "not look', never 'it says self' — refusing to arm.",
        }
    if landing == MANDATE_LANDING:
        if autoland_ok is True:
            return {
                "state": MANDATE, "arm": True, "landing": landing,
                "why": "landing == \"mandate\" and every clause of "
                       "scripts/ci/check_mandate_autoland.py held: a removal-only "
                       "Stage-2 roster cut, each leg carrying a committed evidence "
                       "record and a firing record under a GRANTED derisk_only "
                       "mandate, live account and mirror cut together, authored by "
                       "the workflow. The route is also gated on `autoland: true` "
                       "in config/mandates.yaml, which only the operator writes.",
            }
        if autoland_ok is False:
            return {
                "state": MANDATE_REFUSED, "arm": False, "landing": landing,
                "why": "landing == \"mandate\" but check_mandate_autoland.py "
                       "REFUSED this diff. The declaration asks for the Tier-3 "
                       "auto-land route and the diff does not qualify for it — "
                       "which is the route working, not failing.",
            }
        return {
            "state": MANDATE_UNCHECKED, "arm": False, "landing": landing,
            "why": "landing == \"mandate\" and this caller passed NO autoland "
                   "verdict, so the clauses that authorize a Tier-3 self-merge "
                   "were never evaluated here. That is 'we could not look', "
                   "never 'they passed' — refusing to arm. Only the route that "
                   "can run scripts/ci/check_mandate_autoland.py over both git "
                   "trees may arm this shape.",
        }
    if landing == SELF:
        return {
            "state": SELF, "arm": True, "landing": landing,
            "why": "landing == \"self\" — the PR's own declaration asks to "
                   "land itself.",
        }
    return {
        "state": NOT_SELF, "arm": False, "landing": landing,
        "why": f"landing == {landing!r}, not \"self\". The PR's own "
               f"declaration says it must not land itself — most commonly "
               f"landing == \"hold\" — and no arming file, check-run "
               f"attachment or anything else may override that. This is the "
               f"E63 refusal: PR #12858 declared landing: \"hold\" "
               f"(hold_reason: changes_landing_machinery) and was armed and "
               f"merged anyway because nothing ever read this field.",
    }


# ─────────────────────────── self-test ────────────────────────────────────────
def _self_test(quiet: bool = False):
    fails = []

    def check(label, cond):
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok  {label}")

    # THE ONE THAT MATTERS: the exact #12858 shape must refuse.
    r = grade(found=True, parse_ok=True, landing="hold")
    check("a landing:\"hold\" declaration REFUSES to arm — the E63 shape",
          r["state"] == NOT_SELF and r["arm"] is False)
    check("…and the refusal names #12858 and hold_reason so a reader can find it",
          "12858" in r["why"] and "hold" in r["why"])

    r = grade(found=True, parse_ok=True, landing="self")
    check("landing:\"self\" arms", r["state"] == SELF and r["arm"] is True)

    r = grade(found=False, parse_ok=False, landing=None)
    check("no declaration file at all is ABSENT and arms (grandfathered, like "
          "pr-landing-guard's own undeclared_predates_guard)",
          r["state"] == ABSENT and r["arm"] is True)

    for bad_landing in (None, "", "Self", "SELF", "hold ", 1, True, ["self"]):
        r = grade(found=True, parse_ok=True, landing=bad_landing)
        check(f"a declared-but-not-exactly-\"self\" value ({bad_landing!r}) refuses",
              r["state"] == NOT_SELF and r["arm"] is False)

    # ── the mandate route (R16) ─────────────────────────────────────────────
    r = grade(found=True, parse_ok=True, landing="mandate", autoland_ok=True)
    check("landing:\"mandate\" with a PASSING autoland verdict arms",
          r["state"] == MANDATE and r["arm"] is True)
    r = grade(found=True, parse_ok=True, landing="mandate", autoland_ok=False)
    check("landing:\"mandate\" with a FAILING autoland verdict refuses",
          r["state"] == MANDATE_REFUSED and r["arm"] is False)
    r = grade(found=True, parse_ok=True, landing="mandate")
    check("landing:\"mandate\" with NO verdict is UNCHECKED, never armed — the "
          "shape claude-pr-automerge.yml would produce",
          r["state"] == MANDATE_UNCHECKED and r["arm"] is False)
    check("…and mandate_unchecked is not collapsed into mandate_refused",
          MANDATE_UNCHECKED != MANDATE_REFUSED
          and grade(found=True, parse_ok=True, landing="mandate")["state"]
          != grade(found=True, parse_ok=True, landing="mandate",
                   autoland_ok=False)["state"])
    check("a passing autoland verdict CANNOT arm a landing that is not \"mandate\"",
          grade(found=True, parse_ok=True, landing="hold", autoland_ok=True)["arm"] is False
          and grade(found=True, parse_ok=False, landing=None,
                    autoland_ok=True)["arm"] is False)
    for truthy in (1, "true", "yes", [1]):
        check(f"a merely TRUTHY autoland verdict ({truthy!r}) does not arm",
              grade(found=True, parse_ok=True, landing="mandate",
                    autoland_ok=truthy)["arm"] is False)

    r = grade(found=True, parse_ok=False, landing=None)
    check("an existing-but-unparseable declaration is UNREADABLE, never ABSENT",
          r["state"] == UNREADABLE and r["state"] != ABSENT)
    check("…and never arms", r["arm"] is False)
    check("…and says 'we did not look'", "did not look" in r["why"])

    check("arm is True for exactly three states (self, absent, mandate)",
          [grade(found=True, parse_ok=True, landing="self")["arm"],
           grade(found=False, parse_ok=False, landing=None)["arm"],
           grade(found=True, parse_ok=True, landing="mandate", autoland_ok=True)["arm"],
           grade(found=True, parse_ok=True, landing="hold")["arm"],
           grade(found=True, parse_ok=False, landing=None)["arm"],
           grade(found=True, parse_ok=True, landing="mandate", autoland_ok=False)["arm"],
           grade(found=True, parse_ok=True, landing="mandate")["arm"]]
          == [True, True, True, False, False, False, False])

    reached = {
        grade(found=True, parse_ok=True, landing="self")["state"],
        grade(found=True, parse_ok=True, landing="hold")["state"],
        grade(found=False, parse_ok=False, landing=None)["state"],
        grade(found=True, parse_ok=False, landing=None)["state"],
        grade(found=True, parse_ok=True, landing="mandate", autoland_ok=True)["state"],
        grade(found=True, parse_ok=True, landing="mandate", autoland_ok=False)["state"],
        grade(found=True, parse_ok=True, landing="mandate")["state"],
    }
    check("all seven states are reachable, so none is decorative",
          reached == set(ALL_STATES))

    if not quiet:
        print(f"\nautomerge-landing-gate self-test: "
              f"{'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
        for f in fails:
            print(f"  FAIL  {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", help="JSON file: {found, parse_ok, landing}. "
                                    "Default: read stdin.")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    raw = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except Exception as exc:
        verdict = {"state": UNREADABLE, "arm": False, "landing": None,
                   "why": f"the gate's own input could not be parsed ({exc}). "
                          "Refusing to arm — a gate that cannot read its input "
                          "must not wave the merge through."}
    else:
        verdict = grade(found=bool(payload.get("found")),
                        parse_ok=bool(payload.get("parse_ok")),
                        landing=payload.get("landing"),
                        # NOT coerced: a caller that omits the key means "not
                        # evaluated", and `bool(None)` would silently make that
                        # a refusal for the WRONG stated reason.
                        autoland_ok=payload.get("autoland_ok"))

    print(json.dumps(verdict, indent=2))
    print(f"automerge-landing-gate: {verdict['state']} — "
          f"{'ARM' if verdict['arm'] else 'REFUSE'}", file=sys.stderr)
    return 0 if verdict["arm"] else 3


if __name__ == "__main__":
    sys.exit(main())
