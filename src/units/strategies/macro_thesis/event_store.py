"""M28 P2 — point-in-time event + thesis-link store (append-only JSONL).

Persists the two halves of the event-outcome subsystem (M28-P0 schema §2):

- **``macro_events``** — the calendar/outcome log: one row per known event
  (``scheduled``), and a NEW row when it ``resolves`` (carrying ``realized_outcome``).
- **``thesis_event_links``** — the decision rules binding a thesis to an event
  (its ``on_outcome`` rule list).

Both are append-only JSONL (like ``valuation_store`` / the soak logs), so the
point-in-time invariant holds: a resolution is a new line, never an overwrite —
a backtest reconstructs exactly which events had resolved as-of any past instant.

The event FEED (populating scheduled events from the free government release
calendars) and the RESOLVER inputs run off-VM like the FRED feed; the live sleeve
reads these. Kept as JSONL (not a ``trade_journal.db`` table) for now — the
DB-backed *operational* thesis store is a P3 concern when the thesis engine goes
live. Best-effort, never raises, no order path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

from .event_resolver import resolve_event_for_theses

logger = logging.getLogger(__name__)

EVENTS_LOG_NAME = "macro_events.jsonl"
EVENT_LINKS_LOG_NAME = "thesis_event_links.jsonl"


def _log_path(name: str, path: Optional[Any]):
    if path is not None:
        from pathlib import Path
        return Path(path)
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / name


def _append_jsonl(rows: Iterable[dict], p) -> int:
    written = 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in rows or []:
                try:
                    fh.write(json.dumps(row, default=str) + "\n")
                    written += 1
                except (TypeError, ValueError):
                    continue
    except OSError as exc:
        logger.warning("event_store: append failed (%s)", exc)
    return written


def _read_jsonl(p, *, newest_first: bool = True, limit: Optional[int] = None) -> list[dict]:
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    if newest_first:
        out.reverse()
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def write_events(rows: Iterable[dict], *, path: Optional[Any] = None) -> int:
    """Append event rows (scheduled or resolved) to the point-in-time log."""
    return _append_jsonl(rows, _log_path(EVENTS_LOG_NAME, path))


def read_events(*, path: Optional[Any] = None, limit: Optional[int] = None) -> list[dict]:
    """All event rows, newest-first."""
    return _read_jsonl(_log_path(EVENTS_LOG_NAME, path), limit=limit)


def read_latest_events(*, path: Optional[Any] = None) -> dict[str, dict]:
    """Newest row per ``event_id`` by ``observed_at`` — so a resolved event
    supersedes its earlier scheduled row (the current state of each event)."""
    latest: dict[str, dict] = {}
    for row in read_events(path=path):  # newest-first
        eid = row.get("event_id")
        if eid is None:
            continue
        prev = latest.get(eid)
        if prev is None or str(row.get("observed_at", "")) > str(prev.get("observed_at", "")):
            latest[eid] = row
    return latest


def read_events_by_status(status: str, *, path: Optional[Any] = None) -> list[dict]:
    """Latest-state events filtered by ``status`` (``scheduled`` / ``resolved`` / …)."""
    return [e for e in read_latest_events(path=path).values() if e.get("status") == status]


# ---------------------------------------------------------------------------
# Thesis ↔ event links
# ---------------------------------------------------------------------------


def write_event_links(rows: Iterable[dict], *, path: Optional[Any] = None) -> int:
    """Append thesis↔event decision-rule links."""
    return _append_jsonl(rows, _log_path(EVENT_LINKS_LOG_NAME, path))


def read_event_links(*, path: Optional[Any] = None, event_id: Optional[str] = None) -> list[dict]:
    """All links (newest-first), optionally filtered to one ``event_id``."""
    links = _read_jsonl(_log_path(EVENT_LINKS_LOG_NAME, path))
    if event_id is not None:
        links = [x for x in links if x.get("event_id") == event_id]
    return links


# ---------------------------------------------------------------------------
# Tie-in: resolve all resolved events against their linked theses (observe-only).
# ---------------------------------------------------------------------------


def resolve_all(
    *, events_path: Optional[Any] = None, links_path: Optional[Any] = None
) -> list[dict]:
    """For every currently-**resolved** event, the would-be actions across its
    linked theses (via :func:`event_resolver.resolve_event_for_theses`).

    Observe-only — returns ``[{thesis_id, event_id, action, matched_rule}, …]``;
    the gated P3 executor decides whether to enact. Never raises."""
    out: list[dict] = []
    for event in read_events_by_status("resolved", path=events_path):
        links = read_event_links(path=links_path, event_id=event.get("event_id"))
        out.extend(resolve_event_for_theses(event, links))
    return out
