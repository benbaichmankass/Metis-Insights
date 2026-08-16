#!/usr/bin/env python3
"""Compute the canonical backlog roll-up for the system review/report.

The three review backlogs are the source of truth for "how many open items /
how many drained this window":

- ``docs/claude/health-review-backlog.json``
- ``docs/claude/performance-review-backlog.json``
- ``docs/claude/ml-review-backlog.json``

Each is ``{"items": [{... "status", "resolved_at" ...}]}``. ``open`` and
``drained`` are therefore EXACTLY derivable from the files — they must never be
hand-entered into a report (the 2026-06-23 bug: a hand-assembled
``backlog_summary`` put the *total* count in ``open`` (health "132") and left
performance/ml ``null`` → "— open", when the real open counts were knowable).

This helper is the single computation the ``/system-review`` skill calls to fill
``consolidated.backlog_summary``. Stdlib-only (matches render_system_report.py)
so it runs without the bot venv.

Per backlog it returns:
  total    — every item in the file
  open     — anything is_open_status() does not positively recognise as CLOSED
             (kept_open / open / in_progress / partially-resolved / free-text / …)
  resolved — total − open
  drained  — items whose ``resolved_at`` falls within [since, now] (0 when --since omitted)

CLI:
  python3 scripts/reports/backlog_counts.py [--since ISO_TS] [--repo-root DIR]
  → prints {"health": {...}, "performance": {...}, "ml": {...}} to stdout.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

# THE POLARITY IS "CLOSED IS THE ALLOWLIST", NOT "OPEN IS THE ALLOWLIST"
# (corrected 2026-08-16, BL-20260816-BACKLOG-COUNTER-COUNTS-KEPT-OPEN-AS-RESOLVED).
#
# This module's docstring has always declared the intended rule — "unknown/
# missing → open (fail toward 'still needs attention' rather than silently
# closing an item)" — and the implementation did the exact opposite: it
# allowlisted OPEN statuses and treated everything unrecognised as RESOLVED.
# Field beats comment, except here the comment states the correct policy and
# the code was wrong, so the code moved.
#
# Measured on the live backlogs at the moment of the fix: **96 genuinely-open
# items were being reported as resolved** — health 47, performance 30, ml 19.
# The dominant cause is that `kept_open` was absent from the open set, and
# `kept_open` is the status the three review skills' own drain protocol
# MANDATES for a legitimately-deferred item (soaking / awaiting-data /
# Tier-3-awaiting-operator). So the one status that means "still open, and
# here is why" counted as closed. `/system-review` read `performance: 0 open`
# and `ml: 0 open` while 30 and 19 items sat parked, several of them gates
# MET and awaiting an operator decision. A roll-up that under-reports open
# work is worse than no roll-up: it is the review-coverage gate's own input.
#
# Two secondary causes the inversion also fixes:
#   * FREE-TEXT statuses. Exact-set membership missed
#     `"open — RECURRED 2026-08-14, …"` and `"open (criterion (2) MEASURED …)"`
#     — five health items whose status literally starts with "open".
#   * NOT-ACTUALLY-CLOSED terminals: `fix_landed_monitoring`,
#     `resolved_pending_live_verification`, `likely_already_fixed_unverified`,
#     `mitigated`, `root_cause_fixed_backlog_remains`. Each names remaining
#     work in its own name.

# A status is CLOSED only when its leading token is one of these AND the full
# string carries no open-signal (below). Everything else — including anything
# unrecognised — is OPEN.
CLOSED_TOKENS = {
    "resolved",
    "resolved_refuted",
    "resolved_verified_live",
    "resolved_hold",
    "superseded",
    "invalid",
    "wont_fix",
    "wontfix",
    "duplicate",
    "closed",
    "fixed",
    "measured_no_action",
    "measured_hypothesis_falsified",
}

# Substrings that keep an item OPEN even when its leading token looks closed —
# a status that says its own follow-up is outstanding is not a closed item.
# e.g. "resolved (guard); follow-up OPEN, see resolution_criteria (3)".
OPEN_SIGNALS = (
    "open",
    "pending",
    "unverified",
    "monitoring",
    "remains",
    "follow-up",
    "awaiting",
    "queued",
)


def is_open_status(raw: object) -> bool:
    """True when *raw* denotes still-open work. Unknown → True, by policy."""
    status = str(raw if raw is not None else "open").strip().lower()
    if not status:
        return True
    if any(sig in status for sig in OPEN_SIGNALS):
        return True
    # Leading token: the part before the first space, paren, em-dash, or ';'.
    token = status
    for sep in (" ", "(", "—", "-—", ";", ","):
        token = token.split(sep)[0]
    return token.strip() not in CLOSED_TOKENS

BACKLOGS = {
    "health": "docs/claude/health-review-backlog.json",
    "performance": "docs/claude/performance-review-backlog.json",
    "ml": "docs/claude/ml-review-backlog.json",
}


def _parse_ts(value: str | None) -> _dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _items(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items") or data.get("backlog") or data.get("entries") or []
        if isinstance(items, dict):
            items = list(items.values())
    else:
        items = []
    return [i for i in items if isinstance(i, dict)]


def _resolved_in_window(
    raw: object, since: _dt.datetime, now: _dt.datetime
) -> bool:
    """True if a resolved_at value falls within [since, now].

    Handles both full ISO timestamps (exact comparison) and DATE-ONLY values
    (``"2026-06-23"``) — for the latter the time is unknown, so we count it if
    its UTC day overlaps the window's day span. Date-only therefore degrades to
    day granularity: a 2-hour since-last window will count anything resolved on
    one of its days. Write a FULL timestamp in resolved_at for precise window
    attribution (see the module docstring).
    """
    if not isinstance(raw, str) or not raw.strip():
        return False
    s = raw.strip()
    date_only = len(s) == 10 and s[4] == "-" and "T" not in s
    r = _parse_ts(s)
    if r is None:
        return False
    if date_only:
        return since.date() <= r.date() <= now.date()
    return since <= r <= now


def count_one(path: Path, since: _dt.datetime | None, now: _dt.datetime) -> dict:
    items = _items(path)
    total = len(items)
    open_ = 0
    drained = 0
    for it in items:
        if is_open_status(it.get("status", "open")):
            open_ += 1
        elif since is not None and _resolved_in_window(it.get("resolved_at"), since, now):
            drained += 1
    return {
        "total": total,
        "open": open_,
        "resolved": total - open_,
        "drained": drained,
        "present": path.exists(),
    }


def compute(repo_root: Path, since: _dt.datetime | None = None) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        domain: count_one(repo_root / rel, since, now)
        for domain, rel in BACKLOGS.items()
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--since",
        help="ISO-8601 window start; items resolved at/after this count as 'drained'.",
    )
    ap.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Repo root (default: inferred from this script's location).",
    )
    args = ap.parse_args(argv)
    since = _parse_ts(args.since)
    if args.since and since is None:
        print(f"error: could not parse --since {args.since!r}", file=sys.stderr)
        return 2
    out = compute(Path(args.repo_root), since)
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
