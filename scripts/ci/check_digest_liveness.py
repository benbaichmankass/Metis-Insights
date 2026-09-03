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

FOUR STATES, NEVER COLLAPSED
────────────────────────────
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
``stale``       a receipt exists and its digest is older than the window. The
                channel HAS worked and has STOPPED — the failure this exists
                for.                                                     FAIL
``unreadable``  **WE COULD NOT LOOK.** Corrupt receipt, or one that will not
                date. Not evidence about the digest at all, and a corrupt
                watchdog receipt is itself a defect, so it fails LOUDLY rather
                than passing quietly.                                    FAIL

`never_ran` and `stale` both mean "no recent digest" and are kept apart because
collapsing them reports *"never wired up"* as *"it broke"*, sending a reader
after a regression that never happened.

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
be fast, offline and deterministic — and `api.github.com` is 403 from a Claude
Code sandbox anyway. More importantly a run that STARTED is not a digest that
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

FRESH, STALE, NEVER_RAN, UNREADABLE = "fresh", "stale", "never_ran", "unreadable"

#: Hours a digest may age before the channel counts as broken. See the module
#: docstring for why this is deliberately WIDER than the 55-minute cadence.
#: ⚠️ If the digest interval is ever raised, this must be raised with it, or a
#: healthy slower cadence fails the guard. The self-test pins the ordering so
#: the pair cannot silently invert.
DEFAULT_WINDOW_HOURS = 6.0


def grade(receipt: Optional[Dict[str, Any]], read_state: str, now: datetime,
          window_hours: float = DEFAULT_WINDOW_HOURS) -> Dict[str, Any]:
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
        return {"state": STALE, "ok": False, "age_hours": round(age, 2),
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
    return (f"{head} state={verdict['state']} last_digest_age={age_s}\n"
            f"  {verdict['why']}")


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
    check("four states distinct", len({FRESH, STALE, NEVER_RAN, UNREADABLE}) == 4)

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
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    receipt, read_state = read_receipt(Path(a.receipt))
    verdict = grade(receipt, read_state, datetime.now(timezone.utc), a.window_hours)
    print(render(verdict))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
