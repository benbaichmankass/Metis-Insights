#!/usr/bin/env python3
"""IS THE TRAINER CAPTURE WATCHER ACTUALLY RUNNING?

`.github/workflows/trainer-capture-watch.yml` is the alarm on the trainer VM's
forward-only order-flow capture. This is the thing that makes it a GUARD rather
than a cron somebody hopes is firing.

WHY A GUARD AND NOT A CRON
==========================
A cron is not evidence of a run. `probes.yml`'s first-ever scheduled run fired
~4h50m LATE and once instead of daily; `session-reaper.yml` failed five times
before its first success. And the thing being watched is FORWARD-ONLY: a silent
stall is not recoverable by noticing late, the minutes are simply gone. A
watcher that quietly stopped would leave the capture in exactly the unmonitored
state the open item was filed for, while everything looked fine.

So the watcher writes a dated receipt on every graded run and this grades that
receipt's AGE, in `run_guards.py`, on every PR. A watcher that stops announces
itself in everybody's CI instead of going quiet. Read `generated_at`, never the
cron expression.

FOUR STATES, NEVER COLLAPSED
============================
``fresh``       a run was recorded inside the window.                     PASS
``never_ran``   no receipt exists at all. ⚠️ NOT A FAILURE TODAY, AND THAT IS
                CORRECT rather than lenient -- it is the accurate reading until
                the workflow first fires, and failing on it would red every PR
                in the repo the moment this merges, which is how a guard gets
                disabled instead of fixed. `check_pr_queue_watch.py` and
                `check_drain_liveness.py` take the same position for the same
                reason. The guard ARMS ITSELF on the first successful run: once
                a receipt exists, `stale` becomes reachable and there is no flag
                to unset.                                                 PASS
``stale``       the receipt exists and its newest run is older than the window.
                The watcher HAS run and has STOPPED -- the failure this exists
                for.                                                      FAIL
``unreadable``  WE COULD NOT LOOK. The receipt is corrupt or undateable. Not
                evidence about the watcher at all, and a corrupt watchdog
                receipt is itself a defect, so it fails LOUDLY rather than
                passing quietly.                                          FAIL

⚠️ WHAT A `fresh` VERDICT DOES NOT ESTABLISH
============================================
That the CAPTURE is healthy. This grades the WATCHER's liveness and nothing
else. The capture's own verdict is carried in the receipt's `capture_state` for
a reader, and it is escalated by the workflow run (which fails, and pages) --
never by reddening a contributor's unrelated PR. Two different facts, two
different consequences, deliberately.

⚠️ AND `capture_state` IN THE RECEIPT IS A DATED SNAPSHOT, NOT A LIVE READ.
It says what the capture looked like when the watcher last ran. Re-run the
workflow rather than quoting the receipt's age-old verdict as current.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

RECEIPT_PATH = "docs/claude/work/TRAINER-CAPTURE-WATCH.json"

# The watcher runs every 6 hours. 15h ≈ two consecutive missed runs plus slack
# for the LATE-firing behaviour `probes.yml` demonstrated (~4h50m late on its
# first scheduled run). CHOSEN, not tuned: a tighter window would fail CI on
# GitHub's own scheduling jitter, which is how a guard gets disabled instead of
# fixed.
DEFAULT_MAX_AGE_HOURS = 15

FRESH = "fresh"
NEVER_RAN = "never_ran"
STALE = "stale"
UNREADABLE = "unreadable"

#: States that fail CI. `never_ran` is deliberately absent — see the docstring.
FAILING_STATES = (STALE, UNREADABLE)


def _parse_iso(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def grade_receipt(receipt, now, max_age_hours=DEFAULT_MAX_AGE_HOURS):
    """Grade a receipt dict (or None when the file is absent).

    Returns (state, detail).
    """
    if receipt is None:
        return NEVER_RAN, (
            "no receipt at %s — the watcher has never recorded a run. Correct "
            "until it first fires; the guard arms itself on that run." % RECEIPT_PATH
        )
    if not isinstance(receipt, dict):
        return UNREADABLE, "receipt is not a JSON object"
    generated_at = _parse_iso(receipt.get("generated_at"))
    if generated_at is None:
        # Undateable is UNREADABLE, never fresh: a receipt that cannot be dated
        # cannot be shown to be current, and the fail-safe reading of a watchdog
        # is that we do not know it ran.
        return UNREADABLE, (
            "receipt carries no parseable `generated_at` (%r) — it cannot be "
            "dated, so it cannot be shown to be current"
            % (receipt.get("generated_at"),)
        )
    age = now - generated_at
    age_hours = age.total_seconds() / 3600.0
    if age > timedelta(hours=max_age_hours):
        return STALE, (
            "newest recorded run is %.1fh old (window %dh) — the watcher has "
            "run before and has STOPPED" % (age_hours, max_age_hours)
        )
    return FRESH, "newest recorded run is %.1fh old (window %dh)" % (
        age_hours,
        max_age_hours,
    )


def load_receipt(path=RECEIPT_PATH):
    """Return (receipt_or_None, hard_error_or_None)."""
    if not os.path.exists(path):
        return None, None
    try:
        with open(path) as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def write_receipt(grade_json_path, run_url, path=RECEIPT_PATH, now=None):
    """Write the dated liveness receipt from a grader payload."""
    now = now or datetime.now(timezone.utc)
    capture_state = "unreadable"
    controls_ok = None
    summary = "grader produced no readable JSON"
    threshold_seconds = None
    try:
        with open(grade_json_path) as fh:
            graded = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        summary = "grader JSON unreadable: %s" % exc
    else:
        controls_ok = graded.get("controls_ok")
        summary = graded.get("summary", summary)
        threshold_seconds = graded.get("threshold_seconds")
        watched = [r for r in graded.get("results", []) if r.get("role") == "watched"]
        # No watched row is UNREADABLE, never fresh — an empty observation must
        # not read as a clean bill of health.
        if watched:
            capture_state = watched[0].get("state", "unreadable")
    payload = {
        "_doc": (
            "Dated liveness receipt for .github/workflows/trainer-capture-watch.yml, "
            "graded by scripts/ci/check_trainer_capture_watch.py in run_guards.py on "
            "every PR. READ `generated_at`, NEVER the cron expression. "
            "`capture_state` is a DATED SNAPSHOT of the trainer order-flow capture "
            "at the moment the watcher last ran — not a live read. "
            "`controls_ok` is whether the alarm PROVED it fires against planted "
            "staleness on that run; false means the capture's state was UNKNOWN, "
            "not good."
        ),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_url": run_url,
        "capture_state": capture_state,
        "controls_ok": controls_ok,
        "freshness_threshold_seconds": threshold_seconds,
        "summary": summary,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return payload


def _self_test():
    now = datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc)
    cases = [
        # (receipt, expected_state, label)
        (None, NEVER_RAN, "absent receipt passes — the guard arms on first run"),
        (
            {"generated_at": "2026-09-02T17:00:00Z"},
            FRESH,
            "1h old is fresh",
        ),
        (
            {"generated_at": "2026-09-02T03:30:00Z"},
            FRESH,
            "14.5h old is still inside the 15h window",
        ),
        (
            {"generated_at": "2026-09-02T02:00:00Z"},
            STALE,
            "16h old is stale — the watcher ran before and stopped",
        ),
        (
            {"generated_at": "not a timestamp"},
            UNREADABLE,
            "undateable is UNREADABLE, never fresh",
        ),
        (
            {},
            UNREADABLE,
            "a receipt with no generated_at cannot be shown current",
        ),
        (
            ["not", "an", "object"],
            UNREADABLE,
            "a non-object receipt is unreadable, not empty",
        ),
    ]
    failures = []
    for receipt, expected, label in cases:
        state, detail = grade_receipt(receipt, now)
        if state != expected:
            failures.append(
                "  FAIL %s: expected %s got %s (%s)" % (label, expected, state, detail)
            )
        else:
            print("  ok   %s -> %s" % (label, state))
    # The load-bearing invariant, asserted rather than assumed.
    if NEVER_RAN in FAILING_STATES:
        failures.append(
            "  FAIL never_ran must not fail CI — it would red every PR before "
            "the workflow first fires"
        )
    for must_fail in (STALE, UNREADABLE):
        if must_fail not in FAILING_STATES:
            failures.append("  FAIL %s must fail CI" % must_fail)
    if failures:
        print("\n".join(failures))
        return 1
    print("check_trainer_capture_watch self-test: OK (%d cases)" % len(cases))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--write-receipt", action="store_true")
    ap.add_argument("--grade-json", default="/tmp/grade.json")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--path", default=RECEIPT_PATH)
    ap.add_argument("--max-age-hours", type=int, default=DEFAULT_MAX_AGE_HOURS)
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.write_receipt:
        payload = write_receipt(args.grade_json, args.run_url, path=args.path)
        print("wrote %s (capture_state=%s controls_ok=%s)" % (
            args.path, payload["capture_state"], payload["controls_ok"]))
        return 0

    receipt, hard_error = load_receipt(args.path)
    if hard_error is not None:
        state, detail = UNREADABLE, "receipt unreadable: %s" % hard_error
    else:
        state, detail = grade_receipt(receipt, datetime.now(timezone.utc),
                                      args.max_age_hours)

    print("trainer-capture-watch liveness: %s" % state)
    print("  %s" % detail)
    if isinstance(receipt, dict):
        print("  capture_state (DATED SNAPSHOT, not a live read): %s"
              % receipt.get("capture_state"))
        print("  controls_ok (did the alarm prove it fires that run): %s"
              % receipt.get("controls_ok"))
    print("  IS NOT: this grades the WATCHER's liveness. A `fresh` verdict says "
          "nothing about whether the capture is healthy.")

    if state in FAILING_STATES:
        print("::error::trainer-capture-watch receipt is %s — %s" % (state, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
