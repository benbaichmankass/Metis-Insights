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

# The normaliser AND the SQL builders live in the neutral src.utils layer so the
# WRITER (src/runtime/order_monitor.py) and the READERS (here + the src/runtime
# AI-analyst insights generator) share one implementation without src/runtime
# importing src/web. Re-exported here under their established names so the
# existing web import sites (/performance, /stats, /trades/closed, /pnl/history)
# keep working unchanged.
from src.utils.closed_at import (  # noqa: F401
    close_time_sql,
    closed_at_norm_sql,
    normalize_closed_at_value,
)

__all__ = ["closed_at_norm_sql", "close_time_sql", "normalize_closed_at_value"]
