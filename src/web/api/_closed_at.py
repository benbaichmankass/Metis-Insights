"""Shared ``closed_at`` normalisation for the trade-read endpoints.

The reconciler-filled close path (``order_monitor._close_trade_from_order_status``
+ the Bybit closed-pnl recovery) writes Bybit's ``updatedTime`` — a **raw
epoch-milliseconds string** like ``"1781839121796"`` — straight into the
``trades.closed_at`` column, while every other writer fills it with an
ISO-8601 string. ``datetime("1781839121796")`` returns NULL in SQLite (a bare
numeric is read as a Julian day, out of range), so an unguarded
``datetime(closed_at)`` in a window/since filter or ORDER BY silently drops
every cleanly reconciler-closed trade from the result — the "missing recent
closes" / "24h P&L wrongly $0" bug (real-money ``bybit_2`` reconciler closes
vanished from ``/api/bot/performance`` + ``/api/bot/stats`` pnl24h while
``/api/bot/trades/closed`` — which already guards this — showed them).

This module is the single source of truth for that normalisation so the three
readers can't drift. It is a pure **read-path** guard; it does NOT rewrite the
column (the writer-side correctness fix in ``order_monitor`` is tracked
separately).
"""
from __future__ import annotations

# The pure value normaliser lives in the neutral src.utils layer so the WRITER
# (src/runtime/order_monitor.py) and these READERS share one implementation
# without runtime importing web. Re-exported here under its established name.
from src.utils.closed_at import normalize_closed_at_value

__all__ = ["closed_at_norm_sql", "close_time_sql", "normalize_closed_at_value"]


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


# ``normalize_closed_at_value`` is imported from src.utils.closed_at above
# (single source of truth; re-exported for the existing import sites).
