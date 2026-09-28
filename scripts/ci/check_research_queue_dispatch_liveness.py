#!/usr/bin/env python3
"""research-queue-dispatch's OWN liveness, graded from a committed receipt.

Filed under RQ-OPS-2 (PI-20260928-RQOPS2-0001): the dispatcher cron can go
silent the same way any scheduled job can — the schedule itself stops firing,
or fires but a bug makes every run a no-op — and nothing upstream of this
script would notice, because a cron's own non-firing is invisible by
construction (the same lesson check_cadence_liveness.py's docstring records
for check_manager_queue_watch.py's 479-firing, zero-receipt Routine).

THE RECEIPT, NOT THE LIVE API. `research-queue-dispatch.yml` writes
`docs/claude/work/research-queue-dispatch-receipt.json` on every run that
reaches the "Grade and dispatch" step successfully — fired or dry, cron or
manual — via the SAME commit-to-main path the stamps use, but with
`verify-merged: false`: the receipt is low-stakes (worth being a little
stale) and must not itself become a new 30-minute wait to cancel on.

THREE STATES, matching check_cadence_liveness.py's vocabulary:
  fresh      receipt exists, newer than --max-age-hours (default 48).
  stale      receipt exists, older than that. It ran and stopped.
  never_ran  no receipt has ever been committed. Different from stale:
             "never wired" and "broke" send a reader to different places.

A SECOND, NON-BLOCKING METRIC: the newest `last_dispatched_at` across
committed `research/queue/*.yaml`. This is reported, never gated — a queue
with nothing currently due is a healthy queue doing nothing, not a stale one,
so folding it into the alarm would manufacture false positives on ordinary
quiet days. Only the receipt (proof the WORKFLOW itself is still running)
gates the alarm.

Run `--self-test` to plant a stale receipt and a fresh (quiet-control) one
against a scratch path and assert both are read correctly. Bare `--check`
grades the real committed receipt.

EXIT: 0 fresh · 1 stale or never_ran past the grace window · 2 could not read
the tree at all.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
RECEIPT_PATH = REPO / "docs" / "claude" / "work" / "research-queue-dispatch-receipt.json"
QUEUE_DIR = REPO / "research" / "queue"
DEFAULT_MAX_AGE_HOURS = 48


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def grade_receipt(receipt_path: Path, max_age_hours: float, *, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not receipt_path.exists():
        return {"state": "never_ran", "receipt_path": str(receipt_path)}
    try:
        payload = json.loads(receipt_path.read_text())
        ts = _parse_ts(payload["timestamp"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"state": "never_ran", "receipt_path": str(receipt_path),
                "reason": f"unreadable: {exc}"}
    age_hours = (now - ts).total_seconds() / 3600.0
    state = "fresh" if age_hours <= max_age_hours else "stale"
    return {
        "state": state,
        "receipt_path": str(receipt_path),
        "timestamp": payload["timestamp"],
        "age_hours": round(age_hours, 2),
        "run_id": payload.get("run_id"),
        "conclusion": payload.get("conclusion"),
        "fired": payload.get("fired"),
    }


def newest_unit_activity(queue_dir: Path) -> Optional[dict]:
    """Informational only — see module docstring. Returns None if the
    directory is unreadable or no unit has ever been dispatched."""
    if not queue_dir.is_dir():
        return None
    best: Optional[tuple[datetime, str]] = None
    for path in sorted(queue_dir.glob("*.yaml")):
        try:
            import yaml  # local import: keep this module importable without pyyaml for --self-test-only use
            doc = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        stamp = doc.get("last_dispatched_at")
        if not stamp:
            continue
        try:
            ts = _parse_ts(str(stamp))
        except ValueError:
            continue
        if best is None or ts > best[0]:
            best = (ts, doc.get("id", path.stem))
    if best is None:
        return None
    return {"unit_id": best[1], "last_dispatched_at": best[0].isoformat()}


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        now = datetime.now(timezone.utc)

        never = grade_receipt(tmp / "absent.json", DEFAULT_MAX_AGE_HOURS, now=now)
        assert never["state"] == "never_ran", never

        stale_path = tmp / "stale.json"
        stale_path.write_text(json.dumps({
            "run_id": "1", "conclusion": "success",
            "timestamp": (now - timedelta(hours=72)).isoformat(),
        }))
        stale = grade_receipt(stale_path, DEFAULT_MAX_AGE_HOURS, now=now)
        assert stale["state"] == "stale", stale
        assert stale["age_hours"] > DEFAULT_MAX_AGE_HOURS, stale

        # Quiet control: a receipt inside the window must NOT alarm, so the
        # detector's positive (stale, above) is not the only thing proven —
        # it must also stay quiet on a healthy one.
        fresh_path = tmp / "fresh.json"
        fresh_path.write_text(json.dumps({
            "run_id": "2", "conclusion": "success",
            "timestamp": (now - timedelta(hours=6)).isoformat(),
        }))
        fresh = grade_receipt(fresh_path, DEFAULT_MAX_AGE_HOURS, now=now)
        assert fresh["state"] == "fresh", fresh

        # Boundary: exactly at the edge must not flip on float noise.
        edge_path = tmp / "edge.json"
        edge_path.write_text(json.dumps({
            "run_id": "3", "conclusion": "success",
            "timestamp": (now - timedelta(hours=DEFAULT_MAX_AGE_HOURS - 0.01)).isoformat(),
        }))
        edge = grade_receipt(edge_path, DEFAULT_MAX_AGE_HOURS, now=now)
        assert edge["state"] == "fresh", edge

        # Unreadable content is never_ran, not a crash.
        junk_path = tmp / "junk.json"
        junk_path.write_text("{not json")
        junk = grade_receipt(junk_path, DEFAULT_MAX_AGE_HOURS, now=now)
        assert junk["state"] == "never_ran", junk

    print("check_research_queue_dispatch_liveness: self-test OK "
          "(never_ran / stale / fresh / boundary / unreadable all graded correctly)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    p.add_argument("--receipt-path", type=Path, default=RECEIPT_PATH)
    args = p.parse_args(argv)

    if args.self_test:
        try:
            return run_self_test()
        except AssertionError as exc:
            print(f"check_research_queue_dispatch_liveness: SELF-TEST FAILED: {exc}", file=sys.stderr)
            return 2

    try:
        receipt = grade_receipt(args.receipt_path, args.max_age_hours)
    except OSError as exc:
        print(f"check_research_queue_dispatch_liveness: could not read the tree: {exc}", file=sys.stderr)
        return 2
    activity = newest_unit_activity(QUEUE_DIR)

    summary = {"receipt": receipt, "newest_unit_activity": activity}
    print(json.dumps(summary, indent=2))

    if receipt["state"] == "never_ran":
        print("::error::research-queue-dispatch has never committed a liveness receipt at "
              f"{args.receipt_path} — ALARM (never_ran).", file=sys.stderr)
        return 1
    if receipt["state"] == "stale":
        print(f"::error::research-queue-dispatch's last receipt is {receipt['age_hours']}h old "
              f"(> {args.max_age_hours}h) — ALARM (stale).", file=sys.stderr)
        return 1
    print(f"research-queue-dispatch liveness: fresh ({receipt['age_hours']}h old)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
