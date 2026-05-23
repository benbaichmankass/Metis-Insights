"""Read-only DB explorer — GET /api/bot/db/tables, /api/bot/db/table/{name}.

Tier-1 read surface for the dashboard's Data Explorer tab. Browses the
operational ``trade_journal.db`` ONLY — no secrets live there
(credentials are env/config and redacted elsewhere); the tables are the
same trade/signal/backtest telemetry already exposed piecemeal by the
other dashboard routes.

Safety contract (read-only, injection-free):
  * SELECT only. No writes, no DDL, no ``ATTACH``, no arbitrary SQL.
  * Table and column identifiers are validated against the **live
    schema** (``sqlite_master`` / ``PRAGMA table_info``) before use —
    never interpolated from raw user input — so there is no
    SQL-injection surface on identifiers.
  * Filter values are bound parameters.
  * Results are capped (``MAX_LIMIT``) and paginated; every list view
    also returns ``total`` so the UI can page.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(os.environ.get("TRADE_JOURNAL_DB", str(_REPO_ROOT / "trade_journal.db")))

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# Comparison operators the filter accepts → SQL. Bound to a value param.
_FILTER_OPS: Dict[str, str] = {
    "eq": "=", "ne": "!=", "gt": ">", "lt": "<",
    "gte": ">=", "lte": "<=", "like": "LIKE",
}


def _connect() -> sqlite3.Connection:
    # read-only URI so even a bug can't mutate the journal.
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _list_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _columns(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    # ``table`` MUST already be validated against _list_tables before here.
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [{"name": r["name"], "type": r["type"]} for r in rows]


def _json_safe(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return repr(value)
    return value


@router.get("/db/tables")
async def db_tables() -> Dict[str, Any]:
    """List every table in trade_journal.db with its columns + row count."""
    if not _DB_PATH.exists():
        return {"present": False, "db": _DB_PATH.name, "tables": []}
    try:
        conn = _connect()
        try:
            out: List[Dict[str, Any]] = []
            for name in _list_tables(conn):
                try:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except sqlite3.Error:
                    count = None
                out.append({"name": name, "rows": count, "columns": _columns(conn, name)})
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("db_explorer: tables read failed")
        return {"present": False, "db": _DB_PATH.name, "tables": []}
    return {"present": True, "db": _DB_PATH.name, "tables": out}


@router.get("/db/table/{table}")
async def db_table(
    table: str,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    order_by: Optional[str] = Query(None, max_length=64),
    order_dir: str = Query("desc"),
    filter_col: Optional[str] = Query(None, max_length=64),
    filter_op: str = Query("eq"),
    filter_val: Optional[str] = Query(None, max_length=256),
) -> Dict[str, Any]:
    """Return one page of *table*, optionally filtered + ordered.

    404 on an unknown table. Unknown order/filter columns are ignored
    (rather than erroring) so a stale UI selection degrades gracefully.
    """
    if not _DB_PATH.exists():
        raise HTTPException(status_code=404, detail="database not present")
    try:
        conn = _connect()
        try:
            tables = _list_tables(conn)
            if table not in tables:
                raise HTTPException(status_code=404, detail=f"unknown table: {table}")
            colnames = {c["name"] for c in _columns(conn, table)}

            params: List[Any] = []
            where = ""
            if filter_col in colnames and filter_val is not None and filter_op in _FILTER_OPS:
                op = _FILTER_OPS[filter_op]
                val = f"%{filter_val}%" if filter_op == "like" else filter_val
                where = f' WHERE "{filter_col}" {op} ?'
                params.append(val)

            total = conn.execute(
                f'SELECT COUNT(*) FROM "{table}"{where}', params
            ).fetchone()[0]

            order = ""
            if order_by in colnames:
                direction = "ASC" if str(order_dir).lower() == "asc" else "DESC"
                order = f' ORDER BY "{order_by}" {direction}'

            sql = f'SELECT * FROM "{table}"{where}{order} LIMIT ? OFFSET ?'
            rows = conn.execute(sql, [*params, limit, offset]).fetchall()
            data = [{k: _json_safe(r[k]) for k in r.keys()} for r in rows]
            columns = _columns(conn, table)
        finally:
            conn.close()
    except HTTPException:
        raise
    except sqlite3.Error:
        logger.exception("db_explorer: table read failed")
        raise HTTPException(status_code=503, detail="db read error")
    return {
        "table": table,
        "columns": columns,
        "rows": data,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
