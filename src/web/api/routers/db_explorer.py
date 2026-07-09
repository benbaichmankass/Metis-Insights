"""Read-only DB explorer — GET /api/bot/db/tables, /api/bot/db/table/{name}.

Tier-1 read surface for the dashboard's Data Explorer tab. Browses the
**federated canonical store**: the live trader's ``trade_journal.db`` AND
the trainer-store sidecar ``trainer_store.db`` (trainer/ML lifecycle data
ingested from the trainer mirror — see ``src/units/db/trainer_store.py``).
Together they make every producer — live trader and trainer — queryable
from one place. No secrets live in either DB (credentials are
env/config and redacted elsewhere).

Each table in the listing carries a ``db`` field (``"trade_journal"`` or
``"trainer_store"``) so the UI can group them; the table-read endpoint
auto-routes by table name (or an explicit ``db`` query param).

Safety contract (read-only, injection-free):
  * SELECT only. No writes, no DDL, no ``ATTACH``, no arbitrary SQL.
  * Both DBs are opened ``mode=ro``.
  * Table and column identifiers are validated against the **live
    schema** (``sqlite_master`` / ``PRAGMA table_info``) before use.
  * Filter values are bound parameters.
  * Results are capped (``MAX_LIMIT``) and paginated; list views return
    ``total`` so the UI can page.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from src.units.db.trainer_store import build_if_stale
from src.utils.paths import trade_journal_db_path, trainer_store_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

# Module-level so tests can monkeypatch the live path. The trainer-store
# path is resolved alongside; federation only includes a DB whose file
# actually exists.
_DB_PATH = Path(trade_journal_db_path())
_TRAINER_STORE_DB = Path(trainer_store_db_path())

_DB_TRADE_JOURNAL = "trade_journal"
_DB_TRAINER_STORE = "trainer_store"

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# Comparison operators the filter accepts → SQL. Bound to a value param.
_FILTER_OPS: Dict[str, str] = {
    "eq": "=", "ne": "!=", "gt": ">", "lt": "<",
    "gte": ">=", "lte": "<=", "like": "LIKE",
}


def _federated_dbs() -> List[Tuple[str, Path]]:
    """Ordered (label, path) for every DB in the federated store that
    currently exists. ``trade_journal`` first so its tables win a name
    collision (there are none today, but the order is deterministic)."""
    # Refresh the trainer-store sidecar from the mirror if it changed
    # (no-op when there's no mirror, e.g. dev/CI).
    build_if_stale(db_path=str(_TRAINER_STORE_DB))
    out: List[Tuple[str, Path]] = []
    if _DB_PATH.exists():
        out.append((_DB_TRADE_JOURNAL, _DB_PATH))
    if _TRAINER_STORE_DB.exists():
        out.append((_DB_TRAINER_STORE, _TRAINER_STORE_DB))
    return out


def _connect(path: Path) -> sqlite3.Connection:
    # read-only URI so even a bug can't mutate the DB.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _list_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\' "
        "ORDER BY name"
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
        except Exception:  # noqa: BLE001  # allow-silent: best-effort display coercion; repr fallback
            return repr(value)
    return value


def _resolve_table_db(table: str, db: Optional[str]) -> Optional[Tuple[str, Path]]:
    """Return the (label, path) of the federated DB that owns *table*.

    Honours an explicit ``db`` selector; otherwise searches in federation
    order. Returns None when no DB has the table.
    """
    for label, path in _federated_dbs():
        if db and db != label:
            continue
        try:
            conn = _connect(path)
            try:
                if table in _list_tables(conn):
                    return (label, path)
            finally:
                conn.close()
        except sqlite3.Error:  # allow-silent: skip a federated DB that can't be opened/listed; try the next
            continue
    return None


@router.get("/db/tables")
def db_tables() -> Dict[str, Any]:
    """List every table across the federated store (trade_journal +
    trainer_store) with its columns + row count + owning ``db``."""
    dbs = _federated_dbs()
    if not dbs:
        return {"present": False, "db": _DB_PATH.name, "dbs": [], "tables": []}
    out: List[Dict[str, Any]] = []
    present_dbs: List[str] = []
    for label, path in dbs:
        try:
            conn = _connect(path)
        except sqlite3.Error:  # allow-silent: federation skips an unreadable DB; logged, other DBs still listed
            logger.exception("db_explorer: open failed for %s", path)
            continue
        present_dbs.append(label)
        try:
            for name in _list_tables(conn):
                try:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except sqlite3.Error:  # allow-silent: per-table COUNT is best-effort; null renders as "—"
                    count = None
                out.append({
                    "name": name, "rows": count,
                    "columns": _columns(conn, name), "db": label,
                })
        finally:
            conn.close()
    return {
        "present": bool(out),
        # Back-compat: ``db`` was the single trade_journal name pre-federation.
        "db": _DB_PATH.name,
        "dbs": present_dbs,
        "tables": out,
    }


@router.get("/db/table/{table}")
def db_table(
    table: str,
    db: Optional[str] = Query(None, max_length=32),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    order_by: Optional[str] = Query(None, max_length=64),
    order_dir: str = Query("desc"),
    filter_col: Optional[str] = Query(None, max_length=64),
    filter_op: str = Query("eq"),
    filter_val: Optional[str] = Query(None, max_length=256),
) -> Dict[str, Any]:
    """Return one page of *table* from whichever federated DB owns it.

    404 on an unknown table. Unknown order/filter columns are ignored
    (rather than erroring) so a stale UI selection degrades gracefully.
    """
    target = _resolve_table_db(table, db)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown table: {table}")
    label, path = target
    try:
        conn = _connect(path)
        try:
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
    except sqlite3.Error:  # allow-silent: tier-1 read; logged + surfaced as 503
        logger.exception("db_explorer: table read failed")
        raise HTTPException(status_code=503, detail="db read error")
    return {
        "table": table,
        "db": label,
        "columns": columns,
        "rows": data,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
