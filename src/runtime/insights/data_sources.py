"""Read-only data joins for the AI Analyst.

The generator hands these row-bundles to the LLM with the explicit
instruction "every claim must cite one of the ids below." That keeps
the model from hallucinating trades — every sentence in the output is
anchored to a real ``trade_id`` / ``order_package_id`` / ``signal_id``.

Each function returns a plain ``dict`` shaped:

  {
    "window": {"start": "<iso>", "end": "<iso>"},
    "row_counts": {"trades": N, "order_packages": M, ...},
    "rows": [...],   # the actual cite-able rows
    "meta": {...},   # any extra context (e.g. strategy config)
  }

so the generator can drop ``row_counts`` straight into the response
envelope's ``row_counts`` field.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.utils.paths import (
    artifacts_dir,
    runtime_logs_dir,
    trade_journal_db_path,
)

logger = logging.getLogger(__name__)

# Cap the number of rows we hand the LLM per endpoint to keep tokens
# bounded and the prompt cacheable. The summary endpoint sees only
# aggregates plus a handful of representative recent rows; the
# strategy and recent endpoints surface the raw rows the operator
# actually cares about.
_SUMMARY_RECENT_ROWS = 10
_STRATEGY_RECENT_ROWS = 20
_RECENT_DEFAULT_LIMIT = 20


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _connect_ro() -> sqlite3.Connection:
    """Open the trade journal in read-only mode.

    ``mode=ro`` ensures the generator can never accidentally mutate the
    money DB even if a query is malformed.
    """
    uri = f"file:{trade_journal_db_path()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_query(
    conn: sqlite3.Connection, sql: str, params: tuple = ()
) -> list[dict[str, Any]]:
    """Run a SELECT and return list[dict].

    Returns ``[]`` on any sqlite error (missing table on a fresh DB,
    schema drift, etc.) — the generator must work against an empty DB
    without erroring; the prose just says "no data".
    """
    try:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        logger.warning("insights.data_sources: query failed: %s", exc)
        return []


def _read_strategy_scores(window_start_iso: str | None) -> list[dict[str, Any]]:
    """Read score rows from ``comms/claude_strategy_scores.jsonl``.

    READ-ONLY. The analyst never writes here — that's the
    operator-invoked ``/health-review`` skill's job.
    """
    # The scores file is anchored to the repo root, not runtime_logs.
    # Cheaper to walk the file lazily than to load the whole thing —
    # but the file is small (one line per scored package), so a full
    # read is fine.
    path = Path("comms/claude_strategy_scores.jsonl")
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                # Skip the _meta header row.
                if row.get("_meta") is not None:
                    continue
                # Window filter: keep rows whose reviewed_at >= window_start.
                if window_start_iso is not None:
                    reviewed_at = row.get("reviewed_at") or ""
                    if reviewed_at < window_start_iso:
                        continue
                rows.append(row)
    except OSError as exc:
        logger.warning("insights.data_sources: scores read failed: %s", exc)
    return rows


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    """Tail-read a JSONL file, returning the last ``limit`` parsed rows.

    Returns ``[]`` if the file doesn't exist or is empty. Skips
    unparseable lines silently — partial writes are expected for a
    file being appended to live.
    """
    if not path.exists() or limit <= 0:
        return []
    try:
        with path.open(encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("insights.data_sources: tail failed for %s: %s", path, exc)
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit * 2 :]:  # over-read to handle bad lines
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out[-limit:]


# ---------------------------------------------------------------------------
# Per-endpoint joiners
# ---------------------------------------------------------------------------


def summary_data() -> dict[str, Any]:
    """Overall-system view, 24h rolling.

    Returns counts + a representative tail of recent rows. The LLM
    summarises; it does not list every trade.
    """
    now = _utc_now()
    start = now - timedelta(hours=24)
    window = {"start": start.isoformat(), "end": now.isoformat()}

    conn = _connect_ro()
    try:
        trades = _safe_query(
            conn,
            """
            SELECT id, strategy_name, symbol, direction, pnl, status,
                   exit_reason, opened_at, closed_at, account_id,
                   order_package_id
            FROM trades
            WHERE created_at >= ?
              AND (is_backtest = 0 OR is_backtest IS NULL)
              AND (is_demo = 0 OR is_demo IS NULL)
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (start.isoformat(), _SUMMARY_RECENT_ROWS),
        )
        order_packages = _safe_query(
            conn,
            """
            SELECT order_package_id, strategy_name, symbol, direction,
                   confidence, status, close_reason, created_at, updated_at
            FROM order_packages
            WHERE created_at >= ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (start.isoformat(), _SUMMARY_RECENT_ROWS),
        )
        total_trades_24h = _safe_query(
            conn,
            "SELECT COUNT(*) AS n FROM trades WHERE created_at >= ? "
            "AND (is_backtest = 0 OR is_backtest IS NULL) "
            "AND (is_demo = 0 OR is_demo IS NULL)",
            (start.isoformat(),),
        )
        total_packages_24h = _safe_query(
            conn,
            "SELECT COUNT(*) AS n FROM order_packages WHERE created_at >= ?",
            (start.isoformat(),),
        )
    finally:
        conn.close()

    audit_tail = _tail_jsonl(runtime_logs_dir() / "signal_audit.jsonl", 200)

    return {
        "window": window,
        "row_counts": {
            "trades": int((total_trades_24h[0] if total_trades_24h else {"n": 0})["n"]),
            "order_packages": int(
                (total_packages_24h[0] if total_packages_24h else {"n": 0})["n"]
            ),
            "audit_events": len(audit_tail),
            "signals": sum(
                1 for row in audit_tail if row.get("side") in {"buy", "sell"}
            ),
        },
        "rows": {
            "recent_trades": trades,
            "recent_packages": order_packages,
            "audit_tail_sample": audit_tail[-20:],
        },
    }


def recent_data(limit: int = _RECENT_DEFAULT_LIMIT) -> dict[str, Any]:
    """Last N closed trades joined to their order packages + scores."""
    limit = max(1, min(50, int(limit)))
    now = _utc_now()

    conn = _connect_ro()
    try:
        trades = _safe_query(
            conn,
            """
            SELECT t.id, t.strategy_name, t.symbol, t.direction,
                   t.entry_price, t.exit_price, t.stop_loss,
                   t.take_profit_1, t.position_size, t.pnl, t.exit_reason,
                   t.opened_at, t.closed_at, t.account_id,
                   t.order_package_id,
                   op.confidence, op.signal_logic
            FROM trades t
            LEFT JOIN order_packages op
              ON op.order_package_id = t.order_package_id
            WHERE t.status = 'closed'
              AND (t.is_backtest = 0 OR t.is_backtest IS NULL)
              AND (t.is_demo = 0 OR t.is_demo IS NULL)
            ORDER BY datetime(t.closed_at) DESC
            LIMIT ?
            """,
            (limit,),
        )
    finally:
        conn.close()

    # Pull the matching strategy scores. The jsonl is small enough to
    # iterate; we just key by order_package_id.
    score_rows = _read_strategy_scores(window_start_iso=None)
    scores_by_pkg = {
        row.get("order_package_id"): row
        for row in score_rows
        if row.get("order_package_id")
    }
    for trade in trades:
        pkg_id = trade.get("order_package_id")
        if pkg_id and pkg_id in scores_by_pkg:
            trade["claude_score"] = scores_by_pkg[pkg_id]

    start_iso = trades[-1]["closed_at"] if trades else None
    end_iso = trades[0]["closed_at"] if trades else None
    return {
        "window": {"start": start_iso, "end": end_iso},
        "row_counts": {"trades": len(trades), "requested_limit": limit},
        "rows": {"trades": trades},
        "meta": {"generated_at": now.isoformat()},
    }


def strategy_data(name: str, days: int = 7) -> dict[str, Any]:
    """Per-strategy session view, rolling ``days``."""
    days = max(1, min(30, int(days)))
    now = _utc_now()
    start = now - timedelta(days=days)
    window = {"start": start.isoformat(), "end": now.isoformat()}

    conn = _connect_ro()
    try:
        trades = _safe_query(
            conn,
            """
            SELECT id, symbol, direction, pnl, status, exit_reason,
                   opened_at, closed_at, account_id, order_package_id
            FROM trades
            WHERE strategy_name = ?
              AND created_at >= ?
              AND (is_backtest = 0 OR is_backtest IS NULL)
              AND (is_demo = 0 OR is_demo IS NULL)
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (name, start.isoformat(), _STRATEGY_RECENT_ROWS),
        )
        packages = _safe_query(
            conn,
            """
            SELECT order_package_id, symbol, direction, confidence,
                   status, close_reason, created_at, updated_at
            FROM order_packages
            WHERE strategy_name = ?
              AND created_at >= ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (name, start.isoformat(), _STRATEGY_RECENT_ROWS),
        )
        agg_rows = _safe_query(
            conn,
            """
            SELECT COUNT(*) AS n,
                   COUNT(CASE WHEN status='closed' THEN 1 END) AS closed,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(pnl) AS total_pnl
            FROM trades
            WHERE strategy_name = ?
              AND created_at >= ?
              AND (is_backtest = 0 OR is_backtest IS NULL)
              AND (is_demo = 0 OR is_demo IS NULL)
            """,
            (name, start.isoformat()),
        )
    finally:
        conn.close()

    agg = agg_rows[0] if agg_rows else {}
    return {
        "window": window,
        "row_counts": {
            "trades": int(agg.get("n") or 0),
            "trades_closed": int(agg.get("closed") or 0),
            "wins": int(agg.get("wins") or 0),
            "losses": int(agg.get("losses") or 0),
            "order_packages": len(packages),
        },
        "rows": {"trades": trades, "packages": packages},
        "meta": {
            "strategy_name": name,
            "total_pnl_window": float(agg.get("total_pnl") or 0.0),
        },
    }


def health_data() -> dict[str, Any]:
    """Latest health snapshot."""
    now = _utc_now()
    latest_path = artifacts_dir() / "health" / "latest.json"
    snapshot: dict[str, Any] | None = None
    age_seconds: int | None = None
    if latest_path.exists():
        try:
            with latest_path.open(encoding="utf-8") as fh:
                snapshot = json.load(fh)
            age_seconds = max(
                0, int(now.timestamp() - latest_path.stat().st_mtime)
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("insights.data_sources: health read failed: %s", exc)
            snapshot = None

    return {
        "window": {
            "start": None,
            "end": (snapshot or {}).get("timestamp"),
        },
        "row_counts": {
            "checks": len(((snapshot or {}).get("checks") or {})),
        },
        "rows": {"snapshot": snapshot},
        "meta": {
            "path": str(latest_path),
            "present": snapshot is not None,
            "age_seconds": age_seconds,
        },
    }
