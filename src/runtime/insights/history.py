"""Persistent per-run history of every analyst output.

Each generator run writes the latest cache file (the router's source)
AND appends the same payload to ``trade_journal.db::insights_history``.
The cache is the "what does it say right now" surface; the history
table is the "what did it say two hours ago / yesterday" surface.

The table is lazy-created on first connect so existing DBs migrate
transparently — same pattern the rest of the schema uses.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.utils.paths import trade_journal_db_path

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(trade_journal_db_path())
    conn.row_factory = sqlite3.Row
    _ensure_history_table(conn)
    return conn


def _ensure_history_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS insights_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            strategy_name TEXT,
            model_id TEXT,
            grade TEXT,
            summary_md TEXT NOT NULL,
            signals_json TEXT,
            data_window_json TEXT,
            row_counts_json TEXT,
            payload_json TEXT NOT NULL
        )
        """
    )
    # Endpoint + recency are the access pattern (history?endpoint=summary&hours=24).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_insights_history_endpoint_ts "
        "ON insights_history (endpoint, datetime(generated_at) DESC)"
    )
    conn.commit()


def append_history(
    endpoint: str,
    payload: dict[str, Any],
    strategy_name: str | None = None,
) -> int:
    """Append one row. Returns the new row id (or 0 on DB error)."""
    try:
        conn = _connect()
    except sqlite3.Error as exc:
        logger.warning("insights.history: cannot open DB: %s", exc)
        return 0
    try:
        cur = conn.execute(
            """
            INSERT INTO insights_history (
                generated_at, endpoint, strategy_name, model_id, grade,
                summary_md, signals_json, data_window_json,
                row_counts_json, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("generated_at")
                or datetime.now(timezone.utc).isoformat(),
                endpoint,
                strategy_name,
                payload.get("model_id"),
                payload.get("grade"),
                payload.get("summary_md") or "",
                json.dumps(payload.get("signals") or []),
                json.dumps(payload.get("data_window")),
                json.dumps(payload.get("row_counts")),
                json.dumps(payload, default=str),
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def latest_payload(
    endpoint: str,
    strategy_name: str | None = None,
) -> dict[str, Any] | None:
    """Return the newest stored ``payload`` for an endpoint, or ``None``.

    The **DB-canonical fallback** for the read path (WC-5, dashboard-truth
    2026-06-16): ``insights_history`` is the durable source of truth for what
    the analyst last said; the cache file under ``runtime_logs/insights/`` is a
    derived hot-read. When the cache is missing/unreadable the router serves
    this row instead of a blank placeholder, so a wiped cache dir can't blank
    the dashboard. **No time window** — the most recent row regardless of age
    (unlike ``recent_history``, which is the windowed history view). Returns
    ``None`` when the table is empty/absent or on any DB error.
    """
    try:
        conn = _connect()
    except sqlite3.Error as exc:
        logger.warning("insights.history: cannot open DB for latest: %s", exc)
        return None
    try:
        if strategy_name is not None:
            cur = conn.execute(
                "SELECT payload_json FROM insights_history "
                "WHERE endpoint = ? AND strategy_name = ? "
                "ORDER BY datetime(generated_at) DESC LIMIT 1",
                (endpoint, strategy_name),
            )
        else:
            cur = conn.execute(
                "SELECT payload_json FROM insights_history "
                "WHERE endpoint = ? "
                "ORDER BY datetime(generated_at) DESC LIMIT 1",
                (endpoint,),
            )
        row = cur.fetchone()
        if not row or not row["payload_json"]:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    finally:
        conn.close()


def recent_history(
    endpoint: str,
    hours: int = 24,
    limit: int = 50,
    strategy_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return newest-first rows for an endpoint within the last ``hours``.

    Used by the read endpoint that lands alongside the dashboard
    wiring (PR after PR D). Public payload is the headline fields
    + the full ``payload_json`` so the consumer can drill into
    signals/data_window without a second query.
    """
    hours = max(1, min(7 * 24, int(hours)))  # cap at one week
    limit = max(1, min(500, int(limit)))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    try:
        conn = _connect()
    except sqlite3.Error as exc:
        logger.warning("insights.history: cannot open DB for read: %s", exc)
        return []
    try:
        if strategy_name is not None:
            cur = conn.execute(
                """
                SELECT id, generated_at, endpoint, strategy_name, model_id,
                       grade, summary_md, signals_json, data_window_json,
                       row_counts_json, payload_json
                FROM insights_history
                WHERE endpoint = ? AND strategy_name = ?
                  AND generated_at >= ?
                ORDER BY datetime(generated_at) DESC
                LIMIT ?
                """,
                (endpoint, strategy_name, cutoff, limit),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, generated_at, endpoint, strategy_name, model_id,
                       grade, summary_md, signals_json, data_window_json,
                       row_counts_json, payload_json
                FROM insights_history
                WHERE endpoint = ?
                  AND generated_at >= ?
                ORDER BY datetime(generated_at) DESC
                LIMIT ?
                """,
                (endpoint, cutoff, limit),
            )
        rows: list[dict[str, Any]] = []
        for row in cur.fetchall():
            d = dict(row)
            # Decode the json sub-fields for the consumer.
            for key in ("signals_json", "data_window_json", "row_counts_json",
                        "payload_json"):
                if d.get(key):
                    try:
                        d[key.removesuffix("_json")] = json.loads(d[key])
                    except json.JSONDecodeError:
                        pass
            rows.append(d)
        return rows
    finally:
        conn.close()
