"""M28 — point-in-time TradeThesis store (append-only JSONL).

Persists the :class:`~.thesis.TradeThesis` objects the P3 generation engine
produces, mirroring :mod:`event_store` / :mod:`valuation_store`. Append-only
JSONL: every lifecycle move (draft → active → … → closed) is a **new line**
with a fresh ``updated_at``, never an in-place overwrite — so
:func:`read_latest_theses` reconstructs the current state of each thesis and a
backtest replays exactly what was known as-of any past instant (schema §1a,
observe-only mode logs the would-be transition then re-writes the row).

Kept as JSONL (not yet the ``trade_journal.db::macro_theses`` table) for the
observe-only phase — the DB-backed operational store lands with the live P3
executor. Best-effort, never raises, no order path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

from .thesis import TradeThesis

logger = logging.getLogger(__name__)

THESES_LOG_NAME = "macro_theses.jsonl"


def _log_path(name: str, path: Optional[Any]):
    if path is not None:
        from pathlib import Path
        return Path(path)
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / name


def _as_row(thesis: Any) -> Optional[dict]:
    """Coerce a TradeThesis (or an already-shaped dict) to a plain row."""
    if isinstance(thesis, TradeThesis):
        return thesis.to_dict()
    if isinstance(thesis, dict):
        return thesis
    return None


def write_theses(theses: Iterable[Any], *, path: Optional[Any] = None) -> int:
    """Append thesis rows (``TradeThesis`` or dict) to the point-in-time log.

    Returns the number written. A non-serializable / non-thesis item is skipped,
    never raised."""
    p = _log_path(THESES_LOG_NAME, path)
    written = 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for t in theses or []:
                row = _as_row(t)
                if row is None:
                    continue
                try:
                    fh.write(json.dumps(row, default=str) + "\n")
                    written += 1
                except (TypeError, ValueError):
                    continue
    except OSError as exc:
        logger.warning("thesis_store: append failed (%s)", exc)
    return written


def read_thesis_records(*, path: Optional[Any] = None, limit: Optional[int] = None) -> list[dict]:
    """All thesis rows as raw dicts, newest-first (append order reversed)."""
    p = _log_path(THESES_LOG_NAME, path)
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
    out.reverse()
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


def read_latest_theses(*, path: Optional[Any] = None) -> dict[str, TradeThesis]:
    """Newest row per ``thesis_id`` by ``updated_at`` → the current state of each
    thesis, as :class:`TradeThesis` objects. A later lifecycle line supersedes
    the earlier one (the point-in-time invariant)."""
    latest: dict[str, dict] = {}
    for row in read_thesis_records(path=path):  # newest-first
        tid = row.get("thesis_id")
        if tid is None:
            continue
        prev = latest.get(tid)
        if prev is None or str(row.get("updated_at", "")) > str(prev.get("updated_at", "")):
            latest[tid] = row
    return {tid: TradeThesis.from_dict(row) for tid, row in latest.items()}


def read_theses_by_status(status: str, *, path: Optional[Any] = None) -> list[TradeThesis]:
    """Latest-state theses filtered by ``status`` (``active`` / ``closed`` / …)."""
    return [t for t in read_latest_theses(path=path).values() if t.status == status]


def read_open_theses(*, path: Optional[Any] = None) -> list[TradeThesis]:
    """The non-terminal theses (``draft`` / ``active`` / ``invalidated``) — the
    live book the sleeve is still managing. Convenience over the status filter."""
    return [t for t in read_latest_theses(path=path).values() if not t.is_terminal()]
