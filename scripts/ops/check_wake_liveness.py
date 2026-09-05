#!/usr/bin/env python3
"""Is the MANAGER WAKE actually firing?

Operator directive, 2026-09-02, on the cron-Routine mechanism:

    "B is not done when the Routine exists. B is done when something WATCHES
    that it fired."

This is that watcher, for the wake. It exists because **a dead detector reads
exactly like a healthy one from every other surface**: the lease looks normal,
the checklist looks normal, no alarm fires — and the only thing that changes is
that nothing wakes the manager any more, which is the state this whole mechanism
was built to end. A wake that has silently stopped is strictly worse than no
wake, because it reads as covered.

⚠️ **THIS IS THE SECOND-ORDER PROBLEM AND IT DOES NOT TERMINATE.** This grader
is itself unwatched. That regress is not solved here and pretending otherwise
would be the failure this file exists to catch. What bounds it instead is that
the grader is CHEAP and runs where the wake does not: it reads one committed
file, so CI, a probe, a session start, or a human can all run it, and any of
those noticing is enough. The wake needs a mechanism because a silent manager
runs nothing; this needs only to be run by anything at all.

────────────────────────────────────────────────────────────────────────────
WHY IT READS A RECEIPT AND NOT THE ROUTINE'S OWN STATE
────────────────────────────────────────────────────────────────────────────

The obvious watcher calls ``list_triggers`` and checks ``next_run_at`` /
``last_run``. It cannot be built, and this is MEASURED on this account rather
than assumed — ``check_drain_liveness.py`` established it on 2026-09-02 over 10
listed Routines:

1. ``list_triggers`` is an ``mcp__*`` tool, so nothing outside a healthy Claude
   session can call it. A watcher that only runs inside a healthy session is not
   a watcher.
2. ``next_run_at: 0001-01-01`` is NOT the inert signature — 7 of those 10
   carried it and were the manager's working poke-only Routines. It means "no
   schedule attached".
3. ``last_run`` was absent on all ten, so it cannot separate *never fired* from
   *fired normally*.

What IS observable from anywhere is whether the wake left a trace in the repo.
So the wake writes one bounded receipt per fire — including fires that found
nothing to wake — and this grades that file's freshness.

────────────────────────────────────────────────────────────────────────────
FOUR STATES, NEVER COLLAPSED
────────────────────────────────────────────────────────────────────────────

``fresh``       a fire was recorded inside the window
``stale``       the receipt exists and its newest run is older than the window.
                The wake HAS fired before and has STOPPED — investigate a
                regression
``never_ran``   no receipt at all. **NOT the same as stale**: a Routine created
                and never fired even once, which needs a different fix (create
                or repair the Routine, not hunt a regression). This account
                already carries Routines that are enabled, syntactically
                correct, and have never fired
``unreadable``  **we could not look.** Corrupt or unreadable — not evidence
                about the wake at all

⚠️ ``never_ran`` and ``stale`` both fail and are deliberately distinct.
Collapsing them reports "it was never wired up" as "it broke", sending a reader
to look for a regression that never happened.

Tier-1: reads one committed JSON file. No network, no MCP, no VM, no credential.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

RECEIPT_PATH = REPO / "docs" / "claude" / "work" / "MANAGER-WAKE.json"

#: The wake's declared cadence is hourly (the Routine platform minimum). The
#: window is several multiples of that rather than 1h, for the reason
#: ``check_drain_liveness.py`` gives: a single missed fire is ordinary (stagger,
#: a queued runner, a rate limit), and an alarm that fires on one miss is the
#: desensitised-alarm bug class this repo names as its own. What this must catch
#: is a wake that has STOPPED, not one that was late.
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
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def grade(
    now: datetime | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    path: Path = RECEIPT_PATH,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)

    if not path.is_file():
        return {
            "state": NEVER_RAN,
            "detail": "No receipt file. The wake Routine has never fired even once — "
            "create or repair it. This is NOT a regression.",
            "newest_run": None,
            "age_hours": None,
            "window_hours": window_hours,
        }

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "state": UNREADABLE,
            "detail": f"Receipt exists and could not be parsed ({exc}). WE DID NOT LOOK — "
            "this says nothing about the wake.",
            "newest_run": None,
            "age_hours": None,
            "window_hours": window_hours,
        }

    runs = [r for r in doc.get("runs", []) if isinstance(r, dict)] if isinstance(doc, dict) else []
    stamps = [t for t in (_parse_iso(r.get("at")) for r in runs) if t is not None]
    if not stamps:
        # A receipt file with no usable run is `never_ran`, not `stale`: nothing
        # has ever been recorded, which is the create/repair fix, not the
        # investigate-a-regression fix.
        return {
            "state": NEVER_RAN,
            "detail": "Receipt exists but records no run with a readable timestamp.",
            "newest_run": None,
            "age_hours": None,
            "window_hours": window_hours,
        }

    newest = max(stamps)
    age_hours = (now - newest).total_seconds() / 3600.0
    state = FRESH if age_hours <= window_hours else STALE
    detail = (
        f"Newest fire {newest.isoformat().replace('+00:00', 'Z')}, {age_hours:.1f}h ago, "
        f"window {window_hours}h."
    )
    if state == STALE:
        detail += " The wake HAS fired before and has STOPPED — look for a regression."
    return {
        "state": state,
        "detail": detail,
        "newest_run": newest.isoformat().replace("+00:00", "Z"),
        "age_hours": round(age_hours, 2),
        "window_hours": window_hours,
        "runs_recorded": len(runs),
    }


def _self_test() -> int:
    import tempfile

    failures: list[str] = []
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        check("absent -> never_ran", grade(now, path=d / "nope.json")["state"], NEVER_RAN)

        bad = d / "bad.json"
        bad.write_text("{{{", encoding="utf-8")
        check("corrupt -> unreadable", grade(now, path=bad)["state"], UNREADABLE)

        empty = d / "empty.json"
        empty.write_text(json.dumps({"runs": []}), encoding="utf-8")
        check("no runs -> never_ran", grade(now, path=empty)["state"], NEVER_RAN)

        fresh = d / "fresh.json"
        fresh.write_text(
            json.dumps({"runs": [{"at": "2026-09-05T11:00:00Z", "outcome": "no_action"}]}),
            encoding="utf-8",
        )
        check("1h ago -> fresh", grade(now, path=fresh)["state"], FRESH)

        stale = d / "stale.json"
        stale.write_text(
            json.dumps({"runs": [{"at": "2026-09-04T12:00:00Z", "outcome": "poked"}]}),
            encoding="utf-8",
        )
        check("24h ago -> stale", grade(now, path=stale)["state"], STALE)

        # The NEWEST run decides, not the last element in the list.
        unordered = d / "unordered.json"
        unordered.write_text(
            json.dumps(
                {
                    "runs": [
                        {"at": "2026-09-05T11:30:00Z"},
                        {"at": "2026-09-01T00:00:00Z"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        check("newest wins -> fresh", grade(now, path=unordered)["state"], FRESH)

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("check_wake_liveness self-test: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    result = grade(window_hours=args.window_hours)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"{result['state']}: {result['detail']}")
    return 0 if result["state"] == FRESH else 1


if __name__ == "__main__":
    raise SystemExit(main())
