#!/usr/bin/env python3
"""Grade the TRAINER VM's order-flow capture on its DATA's freshness.

WHY THIS EXISTS (OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED)
====================================================================================
The operator DECIDED on 2026-08-29 to keep the trainer VM for the L2 order-flow
capture. That makes the box a STATED DEPENDENCY for a **forward-only** stream
nothing can re-derive: a silent stall is not recoverable by noticing late — the
minutes are simply gone. Nothing watched it.

⚠️ THE ONE THING THIS MUST NOT DO IS GRADE `ActiveState`.
The capture catches its own poll errors and continues, so `active (running)` is
reachable while it writes nothing, and `journalctl` shows `-- No entries --` for
a healthy service and a wedged one alike. `ict-trainer-publish.timer`'s mirror
mtime advances normally with the capture dead. Every cheap signal on that box is
green through the exact failure this watches. So the observable is the **mtime of
the capture's own output file**, and nothing else.

FOUR STATES, NEVER COLLAPSED
============================
    fresh       we read the file and its data is within the threshold
    stale       we read the file and its data is older than the threshold
    absent      we looked and the file is not there
    unreadable  WE COULD NOT LOOK (ssh died, stat errored, no observation)

`unreadable` is emphatically NOT `fresh` and NOT `stale`. Collapsing it into
`fresh` makes a broken watcher indistinguishable from a healthy capture — which
is the state this row is already in and the precise failure the alarm exists to
end. Collapsing it into `stale` pages the operator for a network blip, which is
the desensitized-alarm P1 this repo has paid for twice. It is its own state and
it alerts under its own name.

THE THRESHOLD IS CHOSEN, NOT MEASURED
=====================================
`DEFAULT_THRESHOLD_SECONDS = 1800` (30 min = 6 missed 5m bars).

Basis, and it is one observation, not a distribution: measured 2026-09-02T18:41:08Z
via trainer relay #10837, the file's mtime was 67 seconds old and its newest rows
were the 18:30:00Z and 18:35:00Z bars — i.e. the writer lands on the 5m boundary
and the observed lag was ~1 minute. 6 missed bars is therefore far outside normal
operation while staying well clear of a single slow write. It has NOT been
validated against a distribution of write latencies, because none has been
collected; do not quote it as a tuned value.

WHAT A PASS DOES NOT ESTABLISH
==============================
A `fresh` verdict says the file was WRITTEN recently. It does not say the rows
are correct, that the bars are contiguous, or that no earlier gap exists — a
restart that lost an hour leaves a perfectly fresh mtime behind it. Freshness is
not validity; this grades only freshness, and says so rather than implying more.
"""

from __future__ import annotations

import argparse
import json
import sys

# See the module docstring: CHOSEN against a single observation, not tuned.
DEFAULT_THRESHOLD_SECONDS = 1800

FRESH = "fresh"
STALE = "stale"
ABSENT = "absent"
UNREADABLE = "unreadable"

#: States that mean the watched stream is not known to be healthy. `unreadable`
#: is in here deliberately — "we could not look" must never pass silently, or the
#: watcher going blind becomes indistinguishable from the capture being fine.
ALERTING_STATES = (STALE, ABSENT, UNREADABLE)


def grade(read_state, mtime_epoch, now_epoch, threshold_seconds):
    """Return (state, age_seconds). `age_seconds` is None when unmeasurable.

    `read_state` is what the OBSERVER managed to do, not what it found:
        "read"    -> a stat succeeded and `mtime_epoch` is real
        "absent"  -> the observer looked and the path was not there
        anything else (incl. None) -> we could not look

    Age is deliberately NOT clamped at zero. A negative age means the file's
    mtime is in the future relative to the reference clock — clock skew between
    the trainer and the runner, or a bad plant — and silently clamping it to 0
    would render that as a perfectly fresh file. It grades `fresh` (it is not
    stale) but the caller can see the negative number and know something is off.
    """
    if read_state == "absent":
        return ABSENT, None
    if read_state != "read":
        return UNREADABLE, None
    if mtime_epoch is None or now_epoch is None:
        # An observer that claims "read" and supplies no mtime has not given us
        # a reading. Trusting the claim over the missing evidence is how a
        # broken observer reports green.
        return UNREADABLE, None
    try:
        age = int(now_epoch) - int(mtime_epoch)
    except (TypeError, ValueError):
        return UNREADABLE, None
    if age > int(threshold_seconds):
        return STALE, age
    return FRESH, age


def grade_observations(payload, threshold_seconds=DEFAULT_THRESHOLD_SECONDS):
    """Grade every target in an observation payload produced on the trainer."""
    now_epoch = payload.get("now_epoch")
    results = []
    for target in payload.get("targets", []):
        state, age = grade(
            target.get("read_state"),
            target.get("mtime_epoch"),
            now_epoch,
            threshold_seconds,
        )
        results.append(
            {
                "name": target.get("name"),
                "path": target.get("path"),
                "role": target.get("role", "watched"),
                "read_state": target.get("read_state"),
                "state": state,
                "age_seconds": age,
                "threshold_seconds": int(threshold_seconds),
                "size_bytes": target.get("size_bytes"),
            }
        )
    return {
        "now_epoch": now_epoch,
        "threshold_seconds": int(threshold_seconds),
        "results": results,
        "is_not": (
            "A `fresh` verdict says the file was WRITTEN recently. It does NOT "
            "say the rows are correct, that the bars are contiguous, or that no "
            "earlier gap exists — a restart that lost an hour leaves a fresh "
            "mtime behind it. Freshness is not validity."
        ),
    }


def self_test_controls(graded):
    """Verify the alarm actually fires, using the run's own planted controls.

    THIS IS THE POINT OF THE WHOLE FILE, and it is why the controls are planted
    ON THE TRAINER and travel through the REAL transport rather than being a
    unit test: `clears_when` on the open item demands the alarm be SHOWN TO FIRE
    AGAINST PLANTED STALENESS, not merely deployed. A grader that has silently
    stopped firing — a threshold typo, a units mix-up, a refactor that made every
    path read `fresh` — passes a deployment check and fails this one.

    Two controls, because one is not enough:
      * `control_stale` — a backdated file that MUST grade `stale`. Proves the
        alarm can fire. Without it a permanently-silent alarm looks identical to
        a healthy capture.
      * `control_fresh` — a just-touched file that MUST grade `fresh`. Proves the
        alarm is not simply screaming at everything, which would be the
        desensitized-alarm failure and would make the stale control meaningless.

    Returns (ok, [failure strings]).
    """
    by_name = {r["name"]: r for r in graded["results"]}
    failures = []
    expectations = {"control_stale": STALE, "control_fresh": FRESH}
    for name, expected in expectations.items():
        row = by_name.get(name)
        if row is None:
            failures.append(
                f"{name}: MISSING from the observation — the control was never "
                f"planted or never made it back, so the alarm is UNPROVEN this run"
            )
            continue
        if row["state"] != expected:
            failures.append(
                f"{name}: expected {expected!r}, got {row['state']!r} "
                f"(age={row['age_seconds']}s, read_state={row['read_state']!r}) "
                f"— THE ALARM DID NOT BEHAVE AS SPECIFIED; its verdict on the "
                f"real capture cannot be trusted this run"
            )
    return (not failures), failures


def _watched(graded):
    return [r for r in graded["results"] if r.get("role") == "watched"]


def summarize(graded):
    watched = _watched(graded)
    if not watched:
        return "no watched target in the observation (we could not look)"
    parts = []
    for r in watched:
        age = "unknown" if r["age_seconds"] is None else f"{r['age_seconds']}s"
        parts.append(f"{r['name']} [{r['state']}] age={age} path={r['path']}")
    return "; ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observations", help="Path to the observation JSON.")
    ap.add_argument(
        "--threshold-seconds", type=int, default=DEFAULT_THRESHOLD_SECONDS
    )
    ap.add_argument("--json", dest="as_json", action="store_true")
    ap.add_argument(
        "--require-controls",
        action="store_true",
        help=(
            "Fail unless the planted controls proved the alarm fires this run. "
            "This is what makes a green mean 'the alarm works AND the capture is "
            "fresh' rather than only the second."
        ),
    )
    args = ap.parse_args(argv)

    if not args.observations:
        ap.error("--observations is required")

    try:
        with open(args.observations) as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - any failure here is "we could not look"
        # Deliberately NOT an empty result set. An unreadable observation file
        # graded as "no findings" is the silent-empty failure: it renders exactly
        # like a healthy capture.
        payload = {"now_epoch": None, "targets": [], "read_error": str(exc)}

    graded = grade_observations(payload, args.threshold_seconds)
    if payload.get("read_error"):
        graded["read_error"] = payload["read_error"]

    controls_ok, control_failures = self_test_controls(graded)
    graded["controls_ok"] = controls_ok
    graded["control_failures"] = control_failures

    watched = _watched(graded)
    # No watched row at all is UNREADABLE, never OK — an empty observation must
    # not read as a clean bill of health.
    alerting = [r for r in watched if r["state"] in ALERTING_STATES]
    capture_ok = bool(watched) and not alerting
    graded["capture_ok"] = capture_ok
    graded["alerting"] = alerting
    graded["summary"] = summarize(graded)

    if args.as_json:
        print(json.dumps(graded, indent=2, sort_keys=True))
    else:
        print(f"threshold_seconds={graded['threshold_seconds']}")
        print(f"controls_ok={controls_ok}")
        for f in control_failures:
            print(f"  CONTROL FAILURE: {f}")
        for r in graded["results"]:
            age = "unknown" if r["age_seconds"] is None else f"{r['age_seconds']}s"
            print(
                f"  [{r['role']}] {r['name']}: {r['state']} age={age} "
                f"read_state={r['read_state']} path={r['path']}"
            )
        print(f"summary: {graded['summary']}")
        print(f"IS NOT: {graded['is_not']}")

    if args.require_controls and not controls_ok:
        return 2
    return 0 if capture_ok else 1


if __name__ == "__main__":
    sys.exit(main())
