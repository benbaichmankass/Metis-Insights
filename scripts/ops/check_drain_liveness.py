#!/usr/bin/env python3
"""Is the decision push-back drain ACTUALLY RUNNING?

Operator directive, 2026-09-02, on the cron-Routine mechanism (B):

    "B is not done when the Routine exists. B is done when something WATCHES
    that it fired."

This is that watcher. It exists because **this account already carries Routines
that are enabled, syntactically correct, and have never fired** — and a decision
channel whose drain has silently stopped is WORSE than no channel: the operator
answers, the answer commits, nothing is ever delivered, and every surface still
reads healthy because `committed` is true.

────────────────────────────────────────────────────────────────────────────
WHY IT READS A RECEIPT AND NOT THE ROUTINE'S OWN STATE
────────────────────────────────────────────────────────────────────────────

The obvious watcher reads `list_triggers` and checks `next_run_at` / `last_run`.
It cannot be built, and the reason is not a limitation of effort:

1. **`list_triggers` is an ``mcp__*`` tool.** This check has to run somewhere
   that notices when the Routine is dead — CI, a probe, a runner. None of those
   have MCP. A watcher that only runs inside a healthy Claude session is not a
   watcher.

2. **`next_run_at: 0001-01-01` IS NOT THE INERT SIGNATURE, and using it as one
   would flag working machinery as dead.** MEASURED 2026-09-02 over this
   account's 10 listed Routines: **7 carry `0001-01-01`, and they are the
   manager's own poke-only session-bound Routines** — the mechanism that has
   been reliably waking sub-sessions all day. The routines documentation says
   plainly that a Routine with no schedule trigger *has* no next run time and
   that older clients rendered it as year 1. So that value means **"no schedule
   attached"**, which is CORRECT for a fire-only Routine.

3. **`last_run` is absent on all ten**, because `list_triggers` records no run
   for a Routine that wakes its own bound session. So it cannot separate
   *never fired* from *fired normally* for mechanism A either.

What CAN be observed from anywhere is whether the drain **left a trace in the
repo**. So the drain writes one bounded receipt per run — including runs that
found nothing to push — and this grades that file's freshness.

────────────────────────────────────────────────────────────────────────────
FOUR STATES, NEVER COLLAPSED
────────────────────────────────────────────────────────────────────────────

``fresh``       a run was recorded inside the window
``stale``       the file exists and its newest run is older than the window —
                the drain HAS run before and has stopped
``never_ran``   no receipt exists at all. **NOT the same as stale**: this is a
                Routine that was created and never fired even once, which is
                exactly the failure class this watcher was built for, and it
                needs a different fix (create/repair the Routine, not
                investigate why it stopped)
``unreadable``  **we could not look.** The file is corrupt or unreadable, which
                is not evidence about the drain at all

⚠️ ``never_ran`` and ``stale`` are deliberately distinct even though both fail.
Collapsing them would report *"it was never wired up"* as *"it broke"*, sending
a reader to look for a regression that never happened.

Tier-1: reads one committed JSON file. No network, no MCP, no VM, no credential.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

RECEIPT_PATH = REPO / "docs" / "claude" / "work" / "DECISION-DRAIN.json"

# The drain's declared cadence is hourly (the Routine minimum). The window is
# deliberately several multiples of that rather than 1h: a single missed fire is
# ordinary (stagger, a queued runner, a rate limit), and an alarm that fires on
# one miss is the desensitised-alarm P1 this repo names as its own bug class.
# What this must catch is a drain that has STOPPED, not one that was late.
DEFAULT_WINDOW_HOURS = 6.0

FRESH = "fresh"
STALE = "stale"
NEVER_RAN = "never_ran"
UNREADABLE = "unreadable"

LIVENESS_STATES: tuple[str, ...] = (FRESH, STALE, NEVER_RAN, UNREADABLE)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def grade(
    *, receipt: dict[str, Any] | None, read_error: str | None,
    window_hours: float, now: datetime | None = None,
) -> dict[str, Any]:
    """Grade drain liveness. **Pure** — the policy is arguable in tests."""
    ref = now or datetime.now(timezone.utc)

    if read_error is not None:
        return {"state": UNREADABLE, "ageHours": None, "lastRunAt": None,
                "runsRecorded": None, "detail": read_error}
    if receipt is None:
        return {"state": NEVER_RAN, "ageHours": None, "lastRunAt": None,
                "runsRecorded": 0,
                "detail": "no receipt file — the drain has never recorded a run"}

    last = _parse_iso(receipt.get("last_run_at"))
    runs = receipt.get("runs_recorded")
    runs = int(runs) if isinstance(runs, int) else None

    if last is None:
        # A receipt we cannot DATE cannot be shown to be fresh, and the
        # fail-safe reading of a liveness record is that it is not.
        return {"state": UNREADABLE, "ageHours": None, "lastRunAt": None,
                "runsRecorded": runs,
                "detail": "receipt exists but its last_run_at is unparsable"}

    age_hours = (ref - last).total_seconds() / 3600.0
    state = FRESH if age_hours <= window_hours else STALE
    return {
        "state": state,
        "ageHours": round(age_hours, 2),
        "lastRunAt": last.isoformat().replace("+00:00", "Z"),
        "runsRecorded": runs,
        "detail": (
            f"newest recorded run is {age_hours:.2f}h old "
            f"(window {window_hours}h)"
        ),
    }


def read_receipt(path: Path = RECEIPT_PATH) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not path.exists():
            return None, None
    except OSError as exc:
        return None, str(exc)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, str(exc)
    if not isinstance(doc, dict):
        return None, "receipt file is not a JSON object"
    return doc, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    receipt, err = read_receipt()
    verdict = grade(receipt=receipt, read_error=err, window_hours=args.window_hours)

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print(f"decision-drain liveness: {verdict['state']} — {verdict['detail']}")
        if verdict["runsRecorded"] is not None:
            print(f"  runs recorded: {verdict['runsRecorded']}")
        if verdict["state"] == NEVER_RAN:
            print("  ⚠️ The drain has NEVER recorded a run. This is not 'it broke' —\n"
                  "     it is a Routine that was created and never fired, which is the\n"
                  "     exact failure this check exists for. Verify the Routine exists\n"
                  "     and has a SCHEDULE trigger attached (a poke-only Routine has no\n"
                  "     cadence and will never fire on its own).")
        elif verdict["state"] == STALE:
            print("  ⚠️ The drain HAS run before and has stopped. A committed answer\n"
                  "     may be sitting undelivered. The pull path still works, so this\n"
                  "     is a degraded channel, not a lost decision.")
        elif verdict["state"] == UNREADABLE:
            print("  ⚠️ We could not look. This says NOTHING about the drain — do not\n"
                  "     read it as either healthy or broken.")

    # `fresh` passes. Every other state fails, including `unreadable`: a
    # liveness check that cannot read its own evidence must not report success.
    return 0 if verdict["state"] == FRESH else 1


if __name__ == "__main__":
    raise SystemExit(main())
