#!/usr/bin/env python3
"""Never arm auto-merge on a head that no check has attached to.

THE MEASURED DEFECT, and it is two facts that only bite together.

(1) A PR opened by `claude-pr-automerge` is born with ZERO attached checks.
    GitHub deliberately suppresses workflow triggers for actions taken with the
    built-in ``GITHUB_TOKEN`` (recursion prevention), and this workflow opens the
    PR with exactly that token — so the ``pull_request`` event never fires and
    the required checks are never created. This is not inferred: it is written
    out in `.github/workflows/pr-opener.yml`'s own header, isolated there on
    PR #10079 and re-measured on #10683, where a head pushed with the caller's
    own credentials STILL returned ``total_count: 0``.

(2) Arming is not a request to merge — it IS the merge. So arming a PR whose
    head carries no checks hands the outcome to whatever GitHub decides about a
    head nothing is measuring, and a commit pushed a moment later is a coin
    flip. Six instances were observed on 2026-09-08 alone (#11356, #11392,
    #11395, #11407, #11419, #11424); the 2026-09-12 one dropped the commit
    carrying a LIVE lane's registry row, and that lane ran unrecorded on `main`
    for nine minutes.

⚠️ THE LOAD-BEARING SUBTLETY, AND A NAIVE GATE GETS IT EXACTLY BACKWARDS.
A zero-check PR does not report zero check runs. It reports **one** — this
workflow's own ``open-and-automerge`` job, which is green because it succeeded
at opening the PR. So ``len(check_runs) > 0`` is TRUE on precisely the PR this
gate exists to refuse, and a gate written that way would pass every single time
while reading like a real test. The self-job is therefore excluded BY NAME, and
a planted defect that stops excluding it must fail the suite.

⚠️ AND `cancelled` IS NOT AN ATTACHED CHECK ANY MORE THAN IT IS A PASS. A check
that was created and then cancelled tells us the machinery exists, which is the
question here — so it DOES count as attached. What it must never do is count as
*passing*; that is `ci_settle`'s question, not this one, and the two are kept
apart deliberately.

THREE STATES, NEVER COLLAPSED:

  ``attached``       at least one check run that is not this workflow's own job
                     exists on the head — arm.
  ``none_attached``  the list was read and holds nothing but (or less than) the
                     self job — REFUSE. The remedy is a push that fires the
                     `pull_request` event, or the PAT-opened PR this change
                     also ships.
  ``unreadable``     ⚠️ **we could not look** — the API call failed, or the
                     payload was not a list. REFUSE. Folding this into
                     ``none_attached`` would be harmless today and wrong in
                     principle; folding it into ``attached`` would arm on an
                     unread head, which is the failure itself.

``arm`` is True for ``attached`` and nothing else. There is no override flag,
deliberately: the cheapest way past this gate must be to make the checks attach.

Run ``--self-test`` to plant each defect and prove the gate fails.
"""
from __future__ import annotations

import argparse
import json
import sys

ATTACHED = "attached"
NONE_ATTACHED = "none_attached"
UNREADABLE = "unreadable"
ALL_STATES = (ATTACHED, NONE_ATTACHED, UNREADABLE)

# The job name in `.github/workflows/claude-pr-automerge.yml`. Its check run is
# always present on a PR this workflow opened, and is never evidence that the
# repo's required checks attached.
SELF_JOB = "open-and-automerge"


def grade(check_runs, *, checks_read_ok: bool) -> dict:
    """Pure. `check_runs` is whatever the API returned; nothing is trusted."""
    if not checks_read_ok:
        return {
            "state": UNREADABLE, "arm": False, "attached": None, "self_only": None,
            "why": "the check-run list could not be READ, which is 'we did not "
                   "look' and never 'nothing is running'. Refusing to arm: an "
                   "unread head is exactly the head this gate exists to protect.",
        }
    if not isinstance(check_runs, list):
        return {
            "state": UNREADABLE, "arm": False, "attached": None, "self_only": None,
            "why": f"the check-run payload is {type(check_runs).__name__}, not a "
                   "list, so no count can be taken from it. A non-array is "
                   "refused rather than read as a zero — the same false-zero "
                   "this repo already paid for once in the pr-queue watcher.",
        }
    # An entry with no usable name is NOT an attachment. Caught by this module's
    # own self-test before it shipped: `[{}, {"name": ""}]` produced two empty
    # strings, both of which are `!= SELF_JOB`, so a truthiness test on the
    # remainder graded a PR with two nameless stubs as fully attached — the
    # arming-anyway outcome this gate exists to refuse, reached by the tidiest
    # line in the file.
    names = [str((c or {}).get("name") or "").strip()
             for c in check_runs if isinstance(c, dict)]
    named = [n for n in names if n]
    foreign = [n for n in named if n != SELF_JOB]
    if not foreign:
        return {
            "state": NONE_ATTACHED, "arm": False,
            "attached": 0, "self_only": bool(named),
            "why": "NOTHING has attached to this head except this workflow's own "
                   f"`{SELF_JOB}` job ({len(named)} named run(s) seen). That is the "
                   "signature of a PR opened under GITHUB_TOKEN: GitHub's "
                   "recursion prevention suppressed the `pull_request` event, so "
                   "the required checks were never created. Arming here is not a "
                   "request to merge, it IS the merge, and it would be a merge "
                   "nothing measured. REMEDY: open the PR under a PAT (this "
                   "workflow now does), or push one ordinary commit so the "
                   "checks attach, then let the run fire again.",
        }
    return {
        "state": ATTACHED, "arm": True,
        "attached": len(foreign), "self_only": False,
        "why": f"{len(foreign)} check run(s) other than `{SELF_JOB}` are attached "
               f"to this head ({', '.join(sorted(set(foreign))[:6])}), so GitHub "
               "has something real to wait for before it merges.",
    }


# ─────────────────────────── self-test ────────────────────────────────────────
def _self_test(quiet: bool = False):
    fails = []

    def check(label, cond):
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok  {label}")

    SELF = [{"name": SELF_JOB, "conclusion": "success"}]
    REAL = SELF + [{"name": "guards"}, {"name": "pytest-run"}]

    # THE ONE THAT MATTERS: the self job alone must NOT arm.
    r = grade(SELF, checks_read_ok=True)
    check("the workflow's OWN job alone does not count as an attached check",
          r["state"] == NONE_ATTACHED and r["arm"] is False)
    check("…and that refusal says WHY, naming the token cause",
          "GITHUB_TOKEN" in r["why"] and "recursion" in r["why"])
    check("…and it records that something WAS seen, so it cannot read as an empty API",
          r["self_only"] is True)

    r = grade([], checks_read_ok=True)
    check("a genuinely empty list does not arm", r["state"] == NONE_ATTACHED and not r["arm"])
    check("…and is distinguishable from the self-only case", r["self_only"] is False)

    r = grade(REAL, checks_read_ok=True)
    check("real checks beside the self job DO arm", r["state"] == ATTACHED and r["arm"] is True)
    check("…and the self job is not counted among them", r["attached"] == 2)

    r = grade(None, checks_read_ok=False)
    check("an unread list is UNREADABLE, never none_attached",
          r["state"] == UNREADABLE and r["state"] != NONE_ATTACHED)
    check("…and never arms", r["arm"] is False)
    check("…and says 'we did not look' rather than implying nothing ran",
          "did not look" in r["why"])

    for bad in ({"check_runs": []}, "0", 0, None):
        r = grade(bad, checks_read_ok=True)
        check(f"a non-array payload ({type(bad).__name__}) is UNREADABLE, not a zero",
              r["state"] == UNREADABLE and r["arm"] is False)

    r = grade([{"name": SELF_JOB}, {"name": "guards", "conclusion": "cancelled"}],
              checks_read_ok=True)
    check("a CANCELLED check still counts as ATTACHED (the question is existence, "
          "not outcome — passing is ci_settle's job)",
          r["state"] == ATTACHED and r["arm"] is True)

    r = grade([None, "junk", {"name": "guards"}], checks_read_ok=True)
    check("malformed entries are skipped without taking the whole read down",
          r["state"] == ATTACHED and r["attached"] == 1)

    r = grade([{}, {"name": ""}], checks_read_ok=True)
    check("entries with no usable name do not manufacture an attachment",
          r["state"] == NONE_ATTACHED and not r["arm"])

    check("arm is True for exactly one state",
          [grade(x, checks_read_ok=k)["arm"]
           for x, k in ((REAL, True), (SELF, True), ([], True), (None, False))]
          == [True, False, False, False])

    reached = {grade(REAL, checks_read_ok=True)["state"],
               grade(SELF, checks_read_ok=True)["state"],
               grade(None, checks_read_ok=False)["state"]}
    check("all three states are reachable, so none is decorative",
          reached == set(ALL_STATES))

    if not quiet:
        print(f"\nautomerge-arming self-test: "
              f"{'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
        for f in fails:
            print(f"  FAIL  {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", help="JSON file: {check_runs, checks_read_ok}. "
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
        # We could not read our OWN input. That is `unreadable`, and it refuses.
        verdict = {"state": UNREADABLE, "arm": False, "attached": None,
                   "self_only": None,
                   "why": f"the gate's own input could not be parsed ({exc}). "
                          "Refusing to arm — a gate that cannot read its input "
                          "must not wave the merge through."}
    else:
        verdict = grade(payload.get("check_runs"),
                        checks_read_ok=bool(payload.get("checks_read_ok")))

    print(json.dumps(verdict, indent=2))
    print(f"automerge-arming: {verdict['state']} — "
          f"{'ARM' if verdict['arm'] else 'REFUSE'}", file=sys.stderr)
    return 0 if verdict["arm"] else 3


if __name__ == "__main__":
    sys.exit(main())
