"""Neutral, dependency-free normalisation of a ``closed_at``-style value.

Single source of truth shared by the WRITER (``src/runtime/order_monitor.py``
close paths) and the READERS (``src/web/api/_closed_at.py`` → /performance,
/stats, /trades/closed, /pnl/history). The reconciler-filled close path receives
Bybit's ``updatedTime`` / ``execTime`` as a **raw epoch-milliseconds string**
(e.g. ``"1782128223798"``); the ``trades.closed_at`` column contract is ISO-8601
(every consumer does ``datetime(closed_at)`` / ``substr(...,1,10)``, which yields
NULL / a garbage day for a bare ms integer). Normalising at the writer keeps the
column ISO so every consumer agrees; the reader-side guard remains for already-
written ms rows until they are migrated.

Pure stdlib so ``src/runtime`` can import it without a layering violation (it must
never import ``src/web``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def normalize_closed_at_value(value: Any) -> Optional[str]:
    """Render a ``closed_at``-style value as an ISO-8601 UTC string, converting
    a raw epoch-milliseconds string/number to ISO. ISO-8601 inputs (and SQLite
    ``CURRENT_TIMESTAMP``) pass through unchanged; empty / unparseable inputs
    return ``None``.

    Detection mirrors the SQL guard in ``src/web/api/_closed_at.py``: an
    all-digit value of >= 12 characters is treated as epoch-ms.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 12:
        try:
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return s


# ---------------------------------------------------------------------------
# SQL builders (neutral layer so both the web readers AND src/runtime consumers
# — e.g. the AI-analyst insights generator — share one close-time expression
# without src/runtime importing src/web). Re-exported by src/web/api/_closed_at.py
# under their established names so existing import sites keep working.
# ---------------------------------------------------------------------------


def closed_at_norm_sql(col: str) -> str:
    """SQLite expression normalising a ``closed_at``-style column *col* to a
    ``datetime()``-parseable value.

    Detects an all-digit, >=12-char value as epoch-ms and converts it
    (``CAST(... AS INTEGER)/1000`` then ``'unixepoch'``); anything else
    (ISO-8601, SQLite ``CURRENT_TIMESTAMP``) flows through the plain
    ``datetime()`` parse unchanged. Idempotent and side-effect free.
    """
    return (
        f"CASE WHEN {col} IS NOT NULL AND {col} <> '' "
        f"AND {col} GLOB '[0-9]*' AND NOT {col} GLOB '*[^0-9]*' "
        f"AND length({col}) >= 12 "
        f"THEN datetime(CAST({col} AS INTEGER)/1000, 'unixepoch') "
        f"ELSE datetime({col}) END"
    )


def close_time_sql(closed_at_col: str, updated_at_col: str, timestamp_col: str) -> str:
    """The canonical close-time expression: epoch-ms-aware ``closed_at`` first,
    then the ``order_packages.updated_at`` join, then the open ``timestamp`` —
    mirroring the wire ``closedAt`` derivation. All three are wrapped so the
    result is a uniform ``datetime()`` value safe to compare / ORDER BY."""
    return (
        f"COALESCE({closed_at_norm_sql(closed_at_col)}, "
        f"datetime({updated_at_col}), datetime({timestamp_col}))"
    )
