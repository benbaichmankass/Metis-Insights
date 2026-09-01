"""Read-only DB explorer — GET /api/bot/db/tables, /api/bot/db/table/{name}.

Tier-1 read surface for the dashboard's Data Explorer tab. Browses the
**federated canonical store**: the live trader's ``trade_journal.db`` AND
the trainer-store sidecar ``trainer_store.db`` (trainer/ML lifecycle data
ingested from the trainer mirror — see ``src/units/db/trainer_store.py``).
Together they make every producer — live trader and trainer — queryable
from one place.

⚠️ **The premise this docstring used to state — "no secrets live in either
DB" — was FALSE, and is why this route needed narrowing**
(``BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN``).
``trade_journal.db::device_tokens.token`` holds **raw FCM push tokens**, and a
``SELECT *`` on an unauthenticated route returned them in full, while the
dedicated ``/api/bot/devices`` route is token-gated AND deliberately returns
only ``token_suffix``. Do not restore that sentence. If you add a table, assume
it may hold a secret until you have checked column by column.

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

Exposure contract (default-deny — added 2026-09-01, operator decision):
  * **Table allowlist.** Only tables named in ``_TABLE_ALLOWLIST`` are listed
    or readable. ⚠️ **The inversion is the point:** a table added to the
    schema and NOT added to the allowlist is **invisible** (404) rather than
    exposed until someone notices. Adding a table to this file is a
    deliberate admission that it holds nothing secret.
  * **Column redaction.** Columns named in ``_REDACTED_COLUMNS`` are dropped
    from the schema listing AND never enter the SELECT projection, so a
    redacted value cannot leave SQLite. Because the filter/order validator
    resolves against the *visible* column set, a redacted column is also not
    filterable — which matters more than it looks: ``filter_state`` +
    ``total`` would otherwise be a working oracle for brute-forcing a secret
    one ``LIKE`` prefix at a time, without ever returning the row.
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

# ---------------------------------------------------------------------------
# Exposure contract — DEFAULT-DENY. See the module docstring.
# ---------------------------------------------------------------------------
# Every table the Data Explorer may list or read. A table NOT named here is
# invisible: absent from /db/tables and 404 from /db/table/{name}.
#
# ⚠️ ADDING A NAME HERE IS A SECURITY DECISION, not a formality. It asserts you
# have read that table's columns and none of them holds a credential, a token,
# or third-party PII. The default is deny precisely so that forgetting this
# file is the SAFE failure — the old behaviour exposed every new table the
# instant it was created, which is how `device_tokens` became world-readable
# on a public host without any decision ever being taken.
#
# Contents as of 2026-09-01: the 21 tables live on the host at that date, minus
# `device_tokens`. Verified against the live schema, not assumed from the repo.
_TABLE_ALLOWLIST: frozenset = frozenset({
    # --- trade_journal.db -------------------------------------------------
    "account_context_snapshots",
    "backtest_results",
    "balance_snapshots",
    "daily_risk_state",
    "insights_history",
    "insights_usage",
    "learning_progress",
    "order_packages",
    "position_telemetry",
    "prop_account_status",
    "prop_fills",
    "prop_tickets",
    "signals",
    "strategy_versions",
    "trades",
    # ⚠️ `device_tokens` is DELIBERATELY ABSENT — it holds raw FCM push tokens
    # in `token`. The dedicated /api/bot/devices route is the supported way to
    # see registered devices; it is token-gated and returns only a suffix.
    # Do not add it back. BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN.
    # --- trainer_store.db (sidecar) ---------------------------------------
    "backtest_sweeps",
    "dataset_builds",
    "db_pulls",
    "experiment_runs",
    "model_registry",
    "training_cycle",
})

# Per-table columns that are never listed and never selected, for tables that
# ARE allowlisted. Defence in depth behind the allowlist: if a future session
# allowlists `device_tokens` without reading the comment above, `token` still
# does not leave the process.
#
# ⚠️ Match on EXACT column names, never a substring/regex on "token"/"key".
# `insights_usage` has `input_tokens` / `output_tokens` / `cache_read_tokens`,
# which are LLM token COUNTS and entirely safe; a pattern-matcher would redact
# them and quietly break the cost dashboard.
_REDACTED_COLUMNS: Dict[str, frozenset] = {
    "device_tokens": frozenset({"token"}),
}


def _redacted_for(table: str) -> frozenset:
    """Columns to hide for *table* (empty when nothing is redacted)."""
    return _REDACTED_COLUMNS.get(table, frozenset())


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
    # DEFAULT-DENY: the allowlist is applied HERE, the single chokepoint every
    # caller goes through (`db_tables` for listing, `_resolve_table_db` for the
    # read). A table missing from `_TABLE_ALLOWLIST` is therefore not merely
    # hidden from the listing — it does not resolve, so the read 404s too.
    return [r["name"] for r in rows if r["name"] in _TABLE_ALLOWLIST]


def _columns(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    """Visible columns for *table* — redacted ones are omitted entirely.

    ``table`` MUST already be validated against _list_tables before here.

    Every caller derives from this: the schema listing, the row projection,
    and the filter/order validator. That is deliberate — a redacted column is
    consequently not selectable AND not filterable, so it cannot be inferred
    through ``total`` either.
    """
    hidden = _redacted_for(table)
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [
        {"name": r["name"], "type": r["type"]}
        for r in rows
        if r["name"] not in hidden
    ]


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

    404 on an unknown table. An unknown order/filter column is IGNORED rather
    than erroring, so a stale UI selection degrades gracefully — but the
    response now SAYS SO via ``filter_state`` / ``order_state``.

    **Why the echo exists** (BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN):
    the tolerance is right; the silence was not. A filter on a column that does
    not exist produced no ``WHERE``, so BOTH the ``COUNT`` and the ``SELECT``
    ran unfiltered and ``total`` came back as the whole table — indistinguishable
    from "the filter matched every row". Measured 2026-08-13 against the live
    journal: four different filters on a misspelled column each returned
    ``total: 4639``, the entire ``trades`` table. This route is on the
    diag-relay allowlist, so its consumers include analysis sessions that
    cannot see the query they got, not just a UI that can re-render.

    ``filter_state`` is three-valued and never collapsed:

    ``applied``                the filter formed a WHERE and ``total`` reflects it
    ``not_requested``          no ``filter_col`` was sent
    ``ignored_unknown_column`` / ``ignored_bad_op`` / ``ignored_no_value``
                               a filter WAS sent and DROPPED — ``total`` is the
                               unfiltered count and must not be read as a match

    A caller asserts ``filter_state == "applied"`` before trusting ``total``.
    """
    target = _resolve_table_db(table, db)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown table: {table}")
    label, path = target
    try:
        conn = _connect(path)
        try:
            columns = _columns(conn, table)
            colnames = {c["name"] for c in columns}
            if not columns:
                # Allowlisted but every column redacted. There is no valid
                # projection for that, and returning an empty-row page would
                # imply the table is empty. Treat it as not exposed.
                raise HTTPException(
                    status_code=404, detail=f"unknown table: {table}"
                )

            params: List[Any] = []
            where = ""
            # Resolve the filter's FATE explicitly, so the response can report
            # which of the mutually-exclusive outcomes actually happened rather
            # than leaving the caller to infer it from a row count.
            if filter_col is None:
                filter_state = "not_requested"
            elif filter_col not in colnames:
                filter_state = "ignored_unknown_column"
            elif filter_op not in _FILTER_OPS:
                filter_state = "ignored_bad_op"
            elif filter_val is None:
                filter_state = "ignored_no_value"
            else:
                filter_state = "applied"

            if filter_state == "applied":
                op = _FILTER_OPS[filter_op]
                val = f"%{filter_val}%" if filter_op == "like" else filter_val
                where = f' WHERE "{filter_col}" {op} ?'
                params.append(val)

            total = conn.execute(
                f'SELECT COUNT(*) FROM "{table}"{where}', params
            ).fetchone()[0]

            order = ""
            if order_by is None:
                order_state = "not_requested"
            elif order_by not in colnames:
                order_state = "ignored_unknown_column"
            else:
                order_state = "applied"
            if order_state == "applied":
                direction = "ASC" if str(order_dir).lower() == "asc" else "DESC"
                order = f' ORDER BY "{order_by}" {direction}'

            # ⚠️ EXPLICIT PROJECTION, not `SELECT *`. A redacted column must
            # never enter the result set in the first place — filtering it out
            # of `data` after the fetch would still have pulled the secret into
            # this process's memory, and one refactor of the row-building loop
            # would put it back on the wire. `colnames` is already
            # post-redaction (see `_columns`), so this projection is the
            # visible set by construction.
            projection = ", ".join(f'"{c["name"]}"' for c in columns)
            sql = f'SELECT {projection} FROM "{table}"{where}{order} LIMIT ? OFFSET ?'
            rows = conn.execute(sql, [*params, limit, offset]).fetchall()
            data = [{k: _json_safe(r[k]) for k in r.keys()} for r in rows]
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
        # Whether `total` is a FILTERED count. Read this before trusting it —
        # see the docstring. Echoed back alongside so a caller can see exactly
        # which filter the server resolved, not the one it believes it sent.
        "filter_state": filter_state,
        "filter_col": filter_col,
        "filter_op": filter_op,
        "filter_val": filter_val,
        "order_state": order_state,
        "order_by": order_by,
    }
