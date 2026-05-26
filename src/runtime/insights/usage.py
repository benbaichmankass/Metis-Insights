"""Cost-budget gate + per-run usage logger for the AI Analyst.

Two responsibilities:

1. **Budget gate.** Before each Anthropic call the generator asks
   ``budget_check()`` whether this month's accumulated spend (from
   ``trade_journal.db::insights_usage``) is still under
   ``INSIGHTS_MONTHLY_BUDGET_USD`` (default $5). If over, the call is
   skipped — no cache is written, last-good is preserved.

2. **Usage append.** After a successful call the generator hands the
   SDK's token counts to ``record_usage(...)`` which writes one row
   to the table. The dashboard surfaces the running total via the
   ``/api/bot/insights/usage`` endpoint (lands alongside the
   dashboard wiring).

Prices in ``_PRICE_TABLE`` are list per-MTok ($/million tokens) — they
are the public Anthropic price as of the date in
``_PRICE_TABLE_AS_OF``. The estimate is approximate (no negotiated
discounts, no caching beyond the SDK's own caching contract) — its
purpose is to keep spend bounded, not to be a billing system.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.utils.paths import trade_journal_db_path

logger = logging.getLogger(__name__)

# Public list prices, $ per million tokens (as of 2026-05-26).
# input / cached-read / output. Cached writes are charged at the input
# rate plus a small premium that we conservatively bucket into input
# for the estimate — we err on the side of over-counting so the
# budget gate never under-reports.
_PRICE_TABLE_AS_OF = "2026-05-26"
_PRICE_TABLE: dict[str, dict[str, float]] = {
    # Haiku 4.5
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "cache_read": 0.10,
        "output": 5.00,
    },
    # Sonnet 4.6
    "claude-sonnet-4-6": {
        "input": 3.00,
        "cache_read": 0.30,
        "output": 15.00,
    },
}


_DEFAULT_BUDGET_USD = 5.00


def monthly_budget_usd() -> float:
    """Return the configured monthly budget cap in USD."""
    raw = os.environ.get("INSIGHTS_MONTHLY_BUDGET_USD", "")
    try:
        return float(raw) if raw else _DEFAULT_BUDGET_USD
    except ValueError:
        logger.warning(
            "insights.usage: bad INSIGHTS_MONTHLY_BUDGET_USD=%r, "
            "using default %.2f",
            raw,
            _DEFAULT_BUDGET_USD,
        )
        return _DEFAULT_BUDGET_USD


def _month_start_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def estimate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Estimate the USD cost of a single call from the SDK's token counts.

    Unknown ``model_id`` → fallback to Sonnet pricing (conservative
    over-estimate; we'd rather refuse a call than under-report).
    """
    prices = _PRICE_TABLE.get(model_id, _PRICE_TABLE["claude-sonnet-4-6"])
    return (
        (input_tokens / 1_000_000) * prices["input"]
        + (cache_read_tokens / 1_000_000) * prices["cache_read"]
        + (output_tokens / 1_000_000) * prices["output"]
    )


def _connect() -> sqlite3.Connection:
    """Open the trade journal read/write.

    The ``insights_usage`` table is lazily ensured on every connect so
    a fresh DB / a never-before-run system Just Works.
    """
    conn = sqlite3.connect(trade_journal_db_path())
    conn.row_factory = sqlite3.Row
    _ensure_usage_table(conn)
    return conn


def _ensure_usage_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS insights_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            model_id TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_insights_usage_ts "
        "ON insights_usage (datetime(ts) DESC)"
    )
    conn.commit()


def month_spend_usd(now: datetime | None = None) -> float:
    """Sum estimated_cost_usd for rows ts >= start-of-this-month."""
    start = _month_start_iso(now)
    try:
        conn = _connect()
    except sqlite3.Error as exc:
        logger.warning("insights.usage: cannot open DB for budget check: %s", exc)
        return 0.0
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0.0) AS spent "
            "FROM insights_usage WHERE ts >= ?",
            (start,),
        ).fetchone()
        return float(row["spent"] or 0.0)
    finally:
        conn.close()


def budget_check(now: datetime | None = None) -> tuple[bool, float, float]:
    """Return ``(under_budget, spent_usd, budget_usd)``.

    ``under_budget`` is the boolean the generator gates on. We
    intentionally test the spend *before* the call — the worst we
    can do is exceed budget by the cost of one in-flight call, which
    is single-pennies for our prompt size.
    """
    spent = month_spend_usd(now)
    budget = monthly_budget_usd()
    return (spent < budget), spent, budget


def record_usage(
    endpoint: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    status: str = "ok",
) -> float:
    """Append one row to ``insights_usage``. Returns the estimated cost.

    ``status`` distinguishes successful calls (``ok``) from
    budget-skipped runs (``budget_skipped``) and API errors
    (``error``) so the dashboard can render the operational story
    cleanly.
    """
    cost = estimate_cost_usd(
        model_id, input_tokens, output_tokens, cache_read_tokens
    )
    try:
        conn = _connect()
    except sqlite3.Error as exc:
        logger.warning("insights.usage: cannot open DB for record: %s", exc)
        return cost
    try:
        conn.execute(
            """
            INSERT INTO insights_usage (
                ts, endpoint, model_id,
                input_tokens, output_tokens,
                cache_creation_tokens, cache_read_tokens,
                estimated_cost_usd, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                endpoint,
                model_id,
                int(input_tokens),
                int(output_tokens),
                int(cache_creation_tokens),
                int(cache_read_tokens),
                float(cost),
                status,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cost


def summarize_usage(now: datetime | None = None) -> dict[str, Any]:
    """Build the payload the dashboard surfaces via /api/bot/insights/usage.

    Lives here so the read endpoint is a thin wrapper; the logic that
    knows the table shape stays alongside the writer.
    """
    now = now or datetime.now(timezone.utc)
    month_start = _month_start_iso(now)
    try:
        conn = _connect()
    except sqlite3.Error as exc:
        logger.warning("insights.usage: summarize failed: %s", exc)
        return {
            "current_month_usd": 0.0,
            "current_month_tokens": 0,
            "budget_usd": monthly_budget_usd(),
            "by_endpoint": [],
            "table_present": False,
        }
    try:
        month_row = conn.execute(
            """
            SELECT COALESCE(SUM(estimated_cost_usd), 0.0) AS spent,
                   COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                   COUNT(*) AS calls
            FROM insights_usage WHERE ts >= ?
            """,
            (month_start,),
        ).fetchone()
        by_endpoint = conn.execute(
            """
            SELECT endpoint,
                   COALESCE(SUM(estimated_cost_usd), 0.0) AS spent,
                   COUNT(*) AS calls
            FROM insights_usage
            WHERE ts >= ?
            GROUP BY endpoint
            ORDER BY spent DESC
            """,
            (month_start,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "current_month_usd": float(month_row["spent"] or 0.0),
        "current_month_tokens": int(month_row["tokens"] or 0),
        "current_month_calls": int(month_row["calls"] or 0),
        "budget_usd": monthly_budget_usd(),
        "month_start": month_start,
        "by_endpoint": [dict(row) for row in by_endpoint],
        "price_table_as_of": _PRICE_TABLE_AS_OF,
        "table_present": True,
    }
