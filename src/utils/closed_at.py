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
