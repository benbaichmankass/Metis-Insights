#!/usr/bin/env python3
"""Workplan §0.WS-5 — macro-producer cron **liveness** monitor.

The "silently-skipped scheduled job" failure class made concrete: a scheduled
producer whose cron quietly stops firing (a workflow disabled after 60 days of
repo inactivity, a broken commit-to-main hop, a renamed secret) leaves its
append-only ledger frozen — and *nothing notices*, because the ledger still
exists and still reads cleanly. The gate downstream just keeps replaying a log
that stopped growing. This is the exact shape of the incidents the "if you see
something, say something" directive exists to kill (a stale data feed that
everyone walks past).

This monitor is the dead-man switch for that: it reads the newest ``observed_at``
across one or more producer ledgers and reports STALE when the freshest row is
older than a per-ledger threshold. It is **read-only** — it never fetches, never
mutates a ledger, never touches a VM. The alerting (Telegram + a GitHub issue)
lives in the ``macro-producer-liveness`` workflow that invokes this script; the
script's job is the honest verdict and a non-zero exit on staleness.

Scope today: the ONE cron-scheduled macro producer,
``comms/macro/valuation_snapshots.jsonl`` (the daily FRED value snapshot, fired by
``.github/workflows/macro-valuation-snapshot.yml`` at 07:30 UTC). The COT and
backfill producers are dispatch/issue-driven one-shots with no schedule, so they
cannot "silently skip a cron" and are deliberately out of scope. The check is
generic (``--ledger PATH:MAX_AGE_HOURS``) so a future scheduled producer is one
flag away, not a rewrite.

Freshness basis: the maximum ``observed_at`` over all rows (the ledger is
append-only and NOT guaranteed sorted, so we scan for the max rather than trust
the last line). ``observed_at`` is an ISO-8601 UTC stamp (``...Z`` /
``+00:00`` / naive all accepted, treated as UTC) — the same convention the diag
audit-query path uses.

Exit codes:
    0  every configured ledger is FRESH (or, with --allow-missing, absent)
    1  at least one ledger is STALE, missing (default), or unreadable
    2  a usage / argument error

Stdlib-only (mirrors the other stdlib ops scripts) so it runs on a bare
GitHub-hosted runner with no repo install.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Daily producer → allow 2 missed runs before we call it stale. Generous enough
# that a single skipped cron (or a slow FRED publish) is not a false alarm, tight
# enough that a genuinely-dead producer surfaces within ~2 days.
DEFAULT_MAX_AGE_HOURS = 48.0

DEFAULT_LEDGERS: tuple[tuple[str, float], ...] = (
    ("comms/macro/valuation_snapshots.jsonl", DEFAULT_MAX_AGE_HOURS),
)


def _parse_iso_utc(raw: str) -> datetime | None:
    """Parse an ISO-8601 stamp to an aware UTC datetime; None if unparseable."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    # Normalise a trailing Z to +00:00 for fromisoformat.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def newest_observed_at(path: Path) -> tuple[datetime | None, int]:
    """Return (max observed_at across all rows, row_count).

    Append-only ledgers are not guaranteed sorted, so we scan every row for the
    max stamp rather than trust the last line. A row with no / unparseable
    ``observed_at`` contributes to the count but not the freshness max.
    """
    newest: datetime | None = None
    rows = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            dt = _parse_iso_utc(obj.get("observed_at", ""))
            if dt is None:
                continue
            if newest is None or dt > newest:
                newest = dt
    return newest, rows


def check_ledger(
    path: Path,
    max_age_hours: float,
    *,
    now: datetime,
    allow_missing: bool,
) -> dict:
    """Evaluate one ledger. Returns a verdict dict (never raises on IO)."""
    result: dict = {
        "ledger": str(path),
        "max_age_hours": max_age_hours,
        "present": path.exists(),
        "rows": 0,
        "newest_observed_at": None,
        "age_hours": None,
        "status": "unknown",
        "detail": "",
    }
    if not path.exists():
        result["status"] = "missing_ok" if allow_missing else "missing"
        result["detail"] = f"ledger not found: {path}"
        return result
    try:
        newest, rows = newest_observed_at(path)
    except OSError as exc:
        result["status"] = "unreadable"
        result["detail"] = f"read error: {exc}"
        return result
    result["rows"] = rows
    if newest is None:
        result["status"] = "no_timestamp"
        result["detail"] = (
            f"{rows} row(s) but no parseable observed_at — cannot judge freshness"
        )
        return result
    age_hours = (now - newest).total_seconds() / 3600.0
    result["newest_observed_at"] = newest.isoformat(timespec="seconds")
    result["age_hours"] = round(age_hours, 2)
    if age_hours > max_age_hours:
        result["status"] = "stale"
        result["detail"] = (
            f"newest row is {age_hours:.1f}h old "
            f"(threshold {max_age_hours:.0f}h) — producer cron may have stopped firing"
        )
    else:
        result["status"] = "fresh"
        result["detail"] = f"newest row {age_hours:.1f}h old (≤ {max_age_hours:.0f}h)"
    return result


def _is_bad(status: str) -> bool:
    """A status that should drive a non-zero exit + an alert."""
    return status in {"stale", "missing", "unreadable", "no_timestamp"}


def _parse_ledger_arg(spec: str) -> tuple[str, float]:
    """Parse a ``PATH`` or ``PATH:MAX_AGE_HOURS`` --ledger value."""
    # rsplit on ':' once so a Windows-y path (unlikely here) or a path with no
    # colon still works; a trailing numeric field is the threshold.
    if ":" in spec:
        head, _, tail = spec.rpartition(":")
        try:
            return head, float(tail)
        except ValueError:
            # No numeric suffix — treat the whole thing as a path.
            return spec, DEFAULT_MAX_AGE_HOURS
    return spec, DEFAULT_MAX_AGE_HOURS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Macro-producer cron liveness monitor (workplan §0.WS-5). Reports "
            "STALE when a scheduled producer's ledger stops growing."
        )
    )
    parser.add_argument(
        "--ledger",
        action="append",
        default=None,
        metavar="PATH[:MAX_AGE_HOURS]",
        help=(
            "Producer ledger to check + optional per-ledger staleness threshold "
            f"in hours (default {DEFAULT_MAX_AGE_HOURS:.0f}). Repeatable. "
            "Defaults to the daily valuation-snapshot producer."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root that relative ledger paths resolve against (default: cwd).",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help=(
            "Treat a not-yet-created ledger as OK rather than a failure "
            "(for the pre-first-run window)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the verdict as JSON (for machine consumption / the workflow).",
    )
    args = parser.parse_args(argv)

    root = Path(args.repo_root)
    if args.ledger:
        ledgers = [_parse_ledger_arg(spec) for spec in args.ledger]
    else:
        ledgers = list(DEFAULT_LEDGERS)

    now = datetime.now(timezone.utc)
    results = [
        check_ledger(
            root / rel,
            max_age,
            now=now,
            allow_missing=args.allow_missing,
        )
        for rel, max_age in ledgers
    ]
    bad = [r for r in results if _is_bad(r["status"])]

    if args.json:
        print(
            json.dumps(
                {
                    "generated_at": now.isoformat(timespec="seconds"),
                    "ok": not bad,
                    "results": results,
                },
                indent=2,
            )
        )
    else:
        for r in results:
            marker = "🚨" if _is_bad(r["status"]) else "✅"
            print(f"{marker} {r['ledger']} [{r['status']}] — {r['detail']}")
        if bad:
            names = ", ".join(Path(r["ledger"]).name for r in bad)
            print(f"\nFAIL: {len(bad)} producer(s) need attention: {names}")
        else:
            print(f"\nOK: all {len(results)} producer ledger(s) fresh")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
