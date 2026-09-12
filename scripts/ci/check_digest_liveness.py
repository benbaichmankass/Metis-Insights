#!/usr/bin/env python3
"""IS THE OPERATOR'S DIGEST STILL ARRIVING?

THE GAP THIS CLOSES
───────────────────
On 2026-09-02 the operator asked *"no pings for 3 hours?"* — it was four — and
NOTHING in the repo knew. That is the finding, more than the missing pings:
F6 makes operator notification the CONDITION the autonomy grant rests on, so
the precondition had been unmet all day and no mechanism could say so.

`render_due_list.src_red_crons` already reports scheduled runs whose latest
conclusion is not success. It cannot help here, because it grades the LATEST
run's CONCLUSION: a cron that simply never fires leaves a stale-but-successful
latest run and reads perfectly clean. **A missed slot and a quiet hour are
indistinguishable from it.** That is the state this guard exists to separate.

⚠️ AND THAT DETECTOR RIDES THE CARRIER IT WATCHES. `src_red_crons` runs inside
`due-list.yml` — itself a cron (`50 5 * * *`), itself measured landing 4h07m
late on 2026-09-02. A cron watchdog for crons cannot report its own carrier
dying. This guard runs in `run_guards.py`, on every PULL REQUEST, so it rides
`pull_request` — an event this repo has measured firing within seconds — and it
is not invokable from a prompt, a skill, or a checklist step. There is nothing
to remember and nothing to opt into.

⚠️ IT IS NOT INVOKED BY THE THING IT WATCHES. `work-digest.yml` neither calls
this guard nor writes anything it reads except the receipt itself, which is
committed in the same commit as the ping it attests.

SIX STATES, NEVER COLLAPSED
───────────────────────────
``fresh``       a digest landed inside the window.                       PASS
``never_ran``   no receipt exists at all. ⚠️ **NOT A FAILURE, AND THAT IS
                CORRECT rather than lenient** — it is the accurate reading until
                the first digest lands under the new carrier, and failing on it
                would red every PR in the repo the moment this merges, which is
                how a guard gets disabled instead of fixed. It ARMS ITSELF on
                the first landed digest: once a receipt exists, `stale` becomes
                reachable and there is no flag to unset. Same position
                `check_pr_queue_watch.py` and `check_drain_liveness.py` take,
                for the same reason.                                     PASS
``stale``       a receipt exists, its digest is older than the window, AND no
                fresher receipt exists on any open PR. The channel HAS worked
                and has STOPPED — the failure this exists for.            FAIL
``stale_unmerged``
                the digest is past the window on MAIN and a FRESHER receipt is
                sitting on an open PR. ⚠️ **THE CARRIER IS NOT BROKEN — THE
                MERGE QUEUE IS BACKED UP**, and those are opposite facts with
                opposite remedies. Reported LOUDLY, naming the PR that carries
                the fresher receipt, and it PASSES.                      PASS
``stale_unknown_carrier``
                the digest is past the window and we **COULD NOT LOOK** for a
                fresher receipt — no token, no network, an API error, a rate
                limit. Not evidence that the carrier is dead, and not evidence
                that it is alive. It FAILS, which is byte-for-byte today's
                behaviour, so an unreadable lookup can never become a quiet way
                to disarm this guard.                                    FAIL
``unreadable``  **WE COULD NOT LOOK.** Corrupt receipt, or one that will not
                date. Not evidence about the digest at all, and a corrupt
                watchdog receipt is itself a defect, so it fails LOUDLY rather
                than passing quietly.                                    FAIL

`never_ran` and `stale` both mean "no recent digest" and are kept apart because
collapsing them reports *"never wired up"* as *"it broke"*, sending a reader
after a regression that never happened.

⚠️ WHY `stale_unmerged` EXISTS: THE LOOP, WHICH IS THE FINDING
──────────────────────────────────────────────────────────────
`BL-20260908-A-BACKED-UP-MERGE-QUEUE-RED-LINES-EVERY-PR-VIA-DIGEST-LIVENESS-WHICH-MAKES-THE-QUEUE-HARDER-TO-CLEAR`.
MEASURED 2026-09-08T13:05Z: main's receipt read 06:58:52Z, so this guard graded
`stale` at 6.1h and FAILED **on every open PR in the repo simultaneously** —
including the operator's twice-asked GLD close fix. **THE CARRIER WAS NOT
BROKEN.** Three fresher receipts existed and every one was sitting in an
unmerged PR (11:20:56Z, 09:28:41Z, 07:58:32Z).

The loop, not the stale file, is the finding: a slow queue makes a time-based
liveness guard go stale on main → the stale guard reds every PR → a repo where
nothing is green clears more slowly → the queue backs up further. It is
self-reinforcing and INVISIBLE FROM ANY SINGLE PR, because each one just looks
individually red for a reason that is nobody's diff.

⚠️ WIDENING THE WINDOW WOULD NOT FIX THIS AND IS THE WRONG DIRECTION — it
weakens the only thing watching whether the operator is still being told
anything, in order to work around a queue problem. The window is unchanged at
6h. What changed is that *"late because nothing ran"* and *"late because the
receipt is stuck in the queue"* are no longer one verdict.

⚠️ AND THE FIX ONLY EVER MAKES THIS GUARD *LESS* LIKELY TO FAIL, in exactly one
evidenced case: a fresher receipt was READ off an open PR head. Every other
path — including every failure of the lookup itself — keeps today's verdict.

⚠️ WHY THE WINDOW IS 6h AND NOT 2h — AND WHAT THAT COSTS
────────────────────────────────────────────────────────
The digest interval is 55 minutes, so 6h is ~6.5 cadences. A tighter window
would catch the operator's 4h gap sooner, and it would also RED A CONTRIBUTOR'S
PR because main happened to be quiet — punishing the one actor not at fault,
which is precisely the reasoning `check_pr_queue_watch.py` records for keeping
the PR backlog out of per-PR CI, and precisely how a guard gets ignored (this
repo has measured 202 of 376 CRITICALs in one window being a single un-latched
alarm).

So the two facts get two consequences: the digest's AGE is REPORTED on every PR
(visible always, whatever the verdict), and the guard only FAILS past 6h, by
which point the channel is not late — it is broken.

⚠️ **This guard is a backstop, not the fix.** What makes a 4h gap unlikely in
the first place is that `work-digest.yml` now also fires on `push: main` — and
main took 34 merges in the 6h to 2026-09-02T18:36Z, one every ~10.6 minutes.
The honest boundary: while main is genuinely quiet there is no push to ride, so
neither the carrier nor this guard can manufacture a heartbeat. A guaranteed
quiet-period heartbeat needs a carrier this repo does not own.

WHY A RECEIPT AND NOT THE RUN HISTORY
─────────────────────────────────────
Reading `actions/runs` needs a network call and a token from a guard that must
be fast and deterministic — and `api.github.com` is 403 from a Claude Code
sandbox anyway.

⚠️ THIS PARAGRAPH SAID "fast, OFFLINE and deterministic" UNTIL 2026-09-12 AND
THAT WORD IS NOW WRONG — corrected rather than left to mislead the next reader.
The guard makes ONE bounded network read, and only on the path where it was
about to FAIL: see `look_for_fresher_receipt`. The happy path is unchanged and
touches no socket, so a normal PR pays nothing. The reasoning above still holds
for the RUN HISTORY specifically, which is a much heavier read and answers the
wrong question (a run that STARTED is not a digest that LANDED). More importantly a run that STARTED is not a digest that
LANDED: `work-digest.yml`'s 07:10Z scheduled run on 2026-09-02 CONCLUDED
FAILURE, which is a row in the run history and a ping the operator never got.
The receipt is written in the same commit as the queued ping, so it attests the
thing that matters.

⚠️ AND A CRON IS NOT EVIDENCE OF A RUN. Read `generated_at`, never the cron
expression.

EXIT: 0 pass · 1 fail.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

from digest_due import RECEIPT, _parse_ts, read_receipt  # noqa: E402

# ⚠️ THE CARRIER VOCABULARY IS IMPORTED, NOT REDEFINED. `collapsed-state-guard`
#    requires a consumer OUTSIDE the producing module, and a second copy of
#    these three literals would be free to drift from the ones the lookup
#    actually returns — the two would then disagree while both looking right.
from digest_carrier import (  # noqa: E402
    CARRIER_FRESHER, CARRIER_NONE, CARRIER_UNKNOWN,
    look_for_fresher_receipt,
)

FRESH, STALE, NEVER_RAN, UNREADABLE = "fresh", "stale", "never_ran", "unreadable"
#: The digest is past the window on MAIN, but a FRESHER receipt is sitting on an
#: open PR — the carrier is working and the merge queue is backed up. PASSES.
STALE_UNMERGED = "stale_unmerged"
#: The digest is past the window and we COULD NOT LOOK for a fresher receipt.
#: FAILS, identically to `stale`, so a failed lookup can never disarm the guard.
STALE_UNKNOWN_CARRIER = "stale_unknown_carrier"

#: The receipt's path RELATIVE to the repo root, derived from `RECEIPT` rather
#: than written out again. A second literal here would be free to drift from the
#: one the guard actually reads, and the two would then disagree about which
#: file they are talking about while both looking right.
_REPO_ROOT = Path(__file__).resolve().parents[2]
try:
    RECEIPT_REL = str(Path(RECEIPT).resolve().relative_to(_REPO_ROOT))
except ValueError:  # a receipt outside the repo cannot be read off a PR head
    RECEIPT_REL = ""

#: Hours a digest may age before the channel counts as broken. See the module
#: docstring for why this is deliberately WIDER than the 55-minute cadence.
#: ⚠️ If the digest interval is ever raised, this must be raised with it, or a
#: healthy slower cadence fails the guard. The self-test pins the ordering so
#: the pair cannot silently invert.
DEFAULT_WINDOW_HOURS = 6.0


def grade(receipt: Optional[Dict[str, Any]], read_state: str, now: datetime,
          window_hours: float = DEFAULT_WINDOW_HOURS,
          carrier: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Grade the receipt. PURE — `carrier` is injected, never fetched here.

    `carrier` is the result of `look_for_fresher_receipt`, or None when no
    lookup was done. It is consulted ONLY on the path that would otherwise
    return `stale`, so the fresh path stays exactly as cheap and as offline as
    it has always been.
    """
    if read_state == "unreadable":
        return {"state": UNREADABLE, "ok": False, "age_hours": None,
                "why": ("the digest receipt exists and could not be read — this "
                        "is 'we could not look', NOT 'the digest is fine', and a "
                        "corrupt watchdog receipt is itself a defect")}
    if read_state == "absent" or receipt is None:
        return {"state": NEVER_RAN, "ok": True, "age_hours": None,
                "why": ("no digest receipt has ever landed. This PASSES: it is "
                        "the accurate reading until the first digest lands, and "
                        "the guard arms itself on that first receipt")}

    ts = _parse_ts(receipt.get("generated_at"))
    if ts is None:
        return {"state": UNREADABLE, "ok": False, "age_hours": None,
                "why": ("the receipt carries no parseable `generated_at`, so the "
                        "digest cannot be shown to be current, and the fail-safe "
                        "reading of a watchdog is stale")}

    age = (now - ts).total_seconds() / 3600.0
    if age < 0:
        return {"state": UNREADABLE, "ok": False, "age_hours": round(age, 2),
                "why": ("the receipt is stamped in the FUTURE — a broken clock or "
                        "a hand-edited receipt, not evidence of a recent digest")}
    if age > window_hours:
        found = (carrier or {}).get("state", CARRIER_UNKNOWN if carrier else None)
        if found == CARRIER_FRESHER:
            pr = (carrier or {}).get("pr")
            where = f"PR #{pr}" if pr else "an open PR"
            return {"state": STALE_UNMERGED, "ok": True, "age_hours": round(age, 2),
                    "carrier": carrier,
                    "why": (f"main's digest is {age:.1f}h old, past the "
                            f"{window_hours:g}h window — BUT a fresher receipt "
                            f"({(carrier or {}).get('generated_at')}) is sitting on "
                            f"{where}. THE CARRIER IS NOT BROKEN; THE MERGE QUEUE "
                            f"IS BACKED UP, and those have opposite remedies. "
                            f"Failing every PR here is the self-reinforcing loop "
                            f"this state exists to end — land the queue, do not "
                            f"go looking for a dead cron")}
        if found == CARRIER_UNKNOWN:
            return {"state": STALE_UNKNOWN_CARRIER, "ok": False,
                    "age_hours": round(age, 2), "carrier": carrier,
                    "why": (f"the last digest reached the operator {age:.1f}h ago, "
                            f"past the {window_hours:g}h window, and we COULD NOT "
                            f"LOOK for a fresher receipt on an open PR "
                            f"({(carrier or {}).get('detail')}). That is not "
                            f"evidence the carrier is alive, so this fails exactly "
                            f"as it did before the lookup existed")}
        return {"state": STALE, "ok": False, "age_hours": round(age, 2),
                "carrier": carrier,
                "why": (f"the last digest reached the operator {age:.1f}h ago, past "
                        f"the {window_hours:g}h window. The channel has worked and "
                        f"has STOPPED — F6 makes this notification the condition "
                        f"the autonomy grant rests on, so it is not cosmetic")}
    return {"state": FRESH, "ok": True, "age_hours": round(age, 2),
            "why": f"last digest {age:.1f}h ago, inside the {window_hours:g}h window"}



def render(verdict: Dict[str, Any]) -> str:
    age = verdict["age_hours"]
    age_s = "n/a" if age is None else f"{age}h"
    head = "digest-liveness: OK" if verdict["ok"] else "digest-liveness: FAIL"
    out = (f"{head} state={verdict['state']} last_digest_age={age_s}\n"
           f"  {verdict['why']}")
    carrier = verdict.get("carrier")
    if carrier:
        # The lookup's own reading is printed even when it did not change the
        # verdict, so `we looked and found nothing` is visible rather than
        # inferred from a silent FAIL.
        out += f"\n  carrier lookup: {carrier.get('state')}"
        for k in ("pr", "generated_at", "heads_checked", "unreadable_heads", "detail"):
            if carrier.get(k) not in (None, 0):
                out += f" {k}={carrier[k]}"
    return out


def _self_test() -> int:
    now = datetime(2026, 9, 2, 18, 0, 0, tzinfo=timezone.utc)
    fails = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)

    def stamped(hours_ago: float):
        return {"generated_at": (now - timedelta(hours=hours_ago))
                .strftime("%Y-%m-%dT%H:%M:%SZ")}

    # Both directions: a planted defect fires and a clean input stays quiet.
    check("recent digest is fresh", grade(stamped(1), "read", now)["state"] == FRESH)
    check("recent digest passes", grade(stamped(1), "read", now)["ok"] is True)
    check("old digest is stale", grade(stamped(9), "read", now)["state"] == STALE)
    check("old digest FAILS", grade(stamped(9), "read", now)["ok"] is False)

    # The boundary sits where the constant says it does.
    check("just inside the window passes",
          grade(stamped(DEFAULT_WINDOW_HOURS - 0.1), "read", now)["ok"] is True)
    check("just outside the window fails",
          grade(stamped(DEFAULT_WINDOW_HOURS + 0.1), "read", now)["ok"] is False)

    # The four states are genuinely distinct, and the two that both mean
    # "no recent digest" have OPPOSITE verdicts -- that is the whole design.
    check("absent grades never_ran", grade(None, "absent", now)["state"] == NEVER_RAN)
    check("never_ran PASSES (arms itself)", grade(None, "absent", now)["ok"] is True)
    check("unreadable grades unreadable",
          grade(None, "unreadable", now)["state"] == UNREADABLE)
    check("unreadable FAILS loudly", grade(None, "unreadable", now)["ok"] is False)
    check("never_ran and stale are not the same state", NEVER_RAN != STALE)
    check("the original four states are still distinct",
          len({FRESH, STALE, NEVER_RAN, UNREADABLE}) == 4)

    # An undateable or future receipt must never read as fresh.
    check("garbage stamp fails",
          grade({"generated_at": "nope"}, "read", now)["state"] == UNREADABLE)
    check("missing stamp fails", grade({}, "read", now)["state"] == UNREADABLE)
    check("future stamp fails", grade(stamped(-3), "read", now)["state"] == UNREADABLE)

    # The window knob binds.
    check("window knob binds (tighter)",
          grade(stamped(3), "read", now, window_hours=1)["ok"] is False)
    check("window knob binds (wider)",
          grade(stamped(3), "read", now, window_hours=48)["ok"] is True)


    # ── THE LOOP THIS GUARD USED TO DRIVE ──────────────────────────────────
    # Every control below plants the 2026-09-08 condition: main's digest past
    # the window. What separates them is ONLY what the carrier lookup found,
    # and the verdicts must differ — that is the whole change.
    old = stamped(9)
    check("stale with NO carrier lookup still FAILS (today's behaviour, unchanged)",
          grade(old, "read", now)["ok"] is False)
    check("...and it is still named `stale`",
          grade(old, "read", now)["state"] == STALE)

    fresher = {"state": CARRIER_FRESHER, "pr": 11377,
               "generated_at": "2026-09-08T11:20:56Z"}
    check("a fresher receipt on an open PR grades stale_unmerged",
          grade(old, "read", now, carrier=fresher)["state"] == STALE_UNMERGED)
    check("...and it PASSES — the carrier works, the QUEUE is backed up",
          grade(old, "read", now, carrier=fresher)["ok"] is True)
    check("...and the report NAMES the PR carrying it, or a reader cannot act",
          "11377" in grade(old, "read", now, carrier=fresher)["why"])

    none_fresher = {"state": CARRIER_NONE, "detail": "checked 2 heads"}
    check("no fresher receipt anywhere still grades stale",
          grade(old, "read", now, carrier=none_fresher)["state"] == STALE)
    check("...and still FAILS — this is the failure the guard exists for",
          grade(old, "read", now, carrier=none_fresher)["ok"] is False)

    unknown = {"state": CARRIER_UNKNOWN, "detail": "no token"}
    check("a lookup that COULD NOT LOOK is its own state",
          grade(old, "read", now, carrier=unknown)["state"] == STALE_UNKNOWN_CARRIER)
    check("...and it FAILS, so a broken lookup can never disarm the guard",
          grade(old, "read", now, carrier=unknown)["ok"] is False)
    check("could_not_look is NOT collapsed into no_fresher_receipt",
          grade(old, "read", now, carrier=unknown)["state"]
          != grade(old, "read", now, carrier=none_fresher)["state"])

    # THE CARRIER IS CONSULTED ONLY ON THE STALE PATH. A fresh digest must not
    # change verdict because a lookup happened to return something.
    check("a FRESH digest ignores the carrier entirely",
          grade(stamped(1), "read", now, carrier=fresher)["state"] == FRESH)
    check("never_ran ignores the carrier entirely",
          grade(None, "absent", now, carrier=fresher)["state"] == NEVER_RAN)
    check("unreadable ignores the carrier entirely",
          grade(None, "unreadable", now, carrier=fresher)["state"] == UNREADABLE)

    check("six states distinct",
          len({FRESH, STALE, NEVER_RAN, UNREADABLE, STALE_UNMERGED,
               STALE_UNKNOWN_CARRIER}) == 6)
    check("three carrier states distinct",
          len({CARRIER_FRESHER, CARRIER_NONE, CARRIER_UNKNOWN}) == 3)

    # THE LOOKUP FAILS CLOSED. Every error path must be `could_not_look`, never
    # `no_fresher_receipt` — the second would report *we did not look* as *the
    # carrier is dead*, and would do it while FAILING the PR for the wrong
    # reason. Exercised without a socket by handing it no credentials.
    import os as _os
    saved = {k: _os.environ.pop(k, None) for k in
             ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORY")}
    try:
        r = look_for_fresher_receipt(now, "docs/claude/work/WORK-DIGEST.json")
        check("no credentials -> could_not_look, NOT no_fresher_receipt",
              r["state"] == CARRIER_UNKNOWN)
        check("...and it says why", bool(r.get("detail")))
        r2 = look_for_fresher_receipt(now, "x.json", token="t", repo="o/r",
                                      timeout=0.001)
        check("an unreachable API -> could_not_look, NOT no_fresher_receipt",
              r2["state"] == CARRIER_UNKNOWN)
    finally:
        for k, v in saved.items():
            if v is not None:
                _os.environ[k] = v

    # The receipt path is single-sourced off RECEIPT, so the lookup and the read
    # can never disagree about which file they mean.
    check("the receipt's relative path is derived, not written out twice",
          RECEIPT_REL and str(RECEIPT).endswith(RECEIPT_REL))

    # THE PAIR MUST NOT INVERT. This guard's window has to stay wider than the
    # producer's own interval, or a perfectly healthy cadence fails CI.
    from digest_due import DEFAULT_INTERVAL_MINUTES
    check("guard window is wider than the digest interval",
          DEFAULT_WINDOW_HOURS * 60 > DEFAULT_INTERVAL_MINUTES)

    if fails:
        print("check_digest_liveness self-test FAILED:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("check_digest_liveness self-test OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    ap.add_argument("--receipt", default=str(RECEIPT))
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-carrier-lookup", action="store_true",
                    help="never look for a fresher receipt on an open PR; grade "
                         "exactly as this guard did before 2026-09-12")
    ap.add_argument("--receipt-rel", default=RECEIPT_REL,
                    help="repo-relative path of the receipt, used when reading it "
                         "off an open PR head")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    receipt, read_state = read_receipt(Path(a.receipt))
    now = datetime.now(timezone.utc)
    verdict = grade(receipt, read_state, now, a.window_hours)

    # ⚠️ THE LOOKUP IS SECOND, AND ONLY ON THE FAILING PATH. Grading first and
    #    re-grading only when the answer is `stale` is what keeps a healthy PR
    #    offline and free — and it means a lookup that hangs or 403s can only
    #    ever affect a PR that was already going to fail.
    if verdict["state"] == STALE and not a.no_carrier_lookup:
        main_ts = _parse_ts((receipt or {}).get("generated_at"))
        if main_ts is not None and a.receipt_rel:
            carrier = look_for_fresher_receipt(main_ts, a.receipt_rel)
            verdict = grade(receipt, read_state, now, a.window_hours, carrier)

    print(render(verdict))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
