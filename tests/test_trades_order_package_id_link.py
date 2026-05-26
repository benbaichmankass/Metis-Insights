"""Many-to-one trade → package link via ``trades.order_package_id``.

Pin for the wiring fix that closes the "(unlinked)" gap in the orphan-
reconciler sweep notification. Before the column, a single decision
that fanned out into multiple trade rows (real entry + demo mirror +
``intent_reduce`` flip leg + multi-account fanout) could only stamp
**one** of them onto the package's ``linked_trade_id`` slot — the rest
showed up as ``Package: (unlinked)`` in the orphan-reconciliation ping
and could not be cascaded by ``_resolve_linked_package_id`` (which
read from that one slot). The new column stores a many-to-one back-
reference on every trade row, so the reconciler can resolve a package
for any leg.

This test pins:

1. The schema migration is additive + idempotent (running
   ``create_tables`` against an old schema adds the column; running
   it again is a no-op).
2. ``insert_trade`` accepts the new field and persists it.
3. ``_resolve_linked_package_id`` resolves all legs of a multi-leg
   fanout via the new column.
4. The legacy fallback (``order_packages.linked_trade_id``) still
   resolves trade rows whose ``order_package_id`` is NULL — covering
   historical rows that pre-date the migration.
"""
from __future__ import annotations

import sqlite3

from src.runtime.order_monitor import _resolve_linked_package_id
from src.units.db.database import Database, _migrate_add_order_package_id


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_adds_column_to_pre_existing_schema(tmp_path):
    """A DB that pre-dates the column gets it added on the next open."""
    db_path = tmp_path / "trade_journal.db"
    # Hand-craft a trades table without ``order_package_id``.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE trades ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "timestamp TEXT NOT NULL,"
        "symbol TEXT NOT NULL,"
        "direction TEXT NOT NULL,"
        "entry_price REAL NOT NULL,"
        "position_size REAL NOT NULL,"
        "status TEXT,"
        "account_id TEXT NOT NULL DEFAULT 'live',"
        "is_demo BOOLEAN DEFAULT 0,"
        "strategy_name TEXT,"
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.commit()
    assert "order_package_id" not in _columns(conn, "trades")
    conn.close()

    # Open via the Database class — should trigger the additive migration.
    Database(db_path=str(db_path))
    conn = sqlite3.connect(str(db_path))
    try:
        assert "order_package_id" in _columns(conn, "trades")
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    """Running ``create_tables`` (and the migration) twice is safe."""
    db_path = tmp_path / "trade_journal.db"
    Database(db_path=str(db_path))
    # Second open re-runs ``create_tables`` from ``__init__`` and should
    # not re-add the column.
    Database(db_path=str(db_path))
    conn = sqlite3.connect(str(db_path))
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(trades)")]
        assert cols.count("order_package_id") == 1
        # Run the migration helper directly on a fresh cursor — should
        # report False (no-op) because the column is already present.
        added = _migrate_add_order_package_id(conn.cursor())
        assert added is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writer + resolver
# ---------------------------------------------------------------------------


def _make_db(tmp_path):
    db_path = tmp_path / "trade_journal.db"
    return Database(db_path=str(db_path))


def _insert_leg(db, *, account_id, setup_type, is_demo, pkg_id):
    return db.insert_trade({
        "timestamp": "2026-05-26T07:13:04+00:00",
        "symbol": "BTCUSDT",
        "direction": "short",
        "entry_price": 76671.3,
        "stop_loss": 77075.22,
        "take_profit_1": 76065.41,
        "position_size": 0.002,
        "setup_type": setup_type,
        "entry_reason": f"{setup_type} signal",
        "status": "open",
        "is_backtest": 0,
        "is_demo": int(is_demo),
        "strategy_name": "ict_scalp_5m",
        "account_id": account_id,
        "notes": "{}",
        "order_package_id": pkg_id,
    })


def _insert_package(db, *, pkg_id, linked_trade_id=None):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": "ict_scalp_5m",
        "symbol": "BTCUSDT",
        "direction": "short",
        "entry": 76671.3,
        "sl": 77075.22,
        "tp": 76065.41,
        "confidence": 0.9875,
        "status": "open",
        "linked_trade_id": linked_trade_id,
        "meta": {},
    })


def test_insert_trade_persists_order_package_id(tmp_path):
    db = _make_db(tmp_path)
    pkg_id = "pkg-canonical-link-001"
    trade_id = _insert_leg(
        db,
        account_id="bybit_2",
        setup_type="ict_scalp_5m",
        is_demo=False,
        pkg_id=pkg_id,
    )
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT order_package_id FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == pkg_id


def test_resolver_finds_package_for_every_leg_of_a_fanout(tmp_path):
    """Reproduces the live 2026-05-26 07:13Z fanout.

    A single ict_scalp_5m short produced three trade rows:
      * 1724 — bybit_1 demo mirror (``is_demo=1``)
      * 1725 — bybit_2 intent_reduce flip leg (``setup_type=intent_reduce``)
      * 1726 — bybit_2 real entry (the "primary")
    Pre-fix, only 1726 resolved to the package — the other two showed
    up as ``(unlinked)`` in the reconciler ping. With the new column
    stamped on every row, all three resolve.
    """
    db = _make_db(tmp_path)
    pkg_id = "pkg-6ad338849aa345a2"
    demo_id = _insert_leg(
        db, account_id="bybit_1", setup_type="ict_scalp_5m",
        is_demo=True, pkg_id=pkg_id,
    )
    reduce_id = _insert_leg(
        db, account_id="bybit_2", setup_type="intent_reduce",
        is_demo=False, pkg_id=pkg_id,
    )
    primary_id = _insert_leg(
        db, account_id="bybit_2", setup_type="ict_scalp_5m",
        is_demo=False, pkg_id=pkg_id,
    )
    # Only the primary leg writes the legacy ``linked_trade_id`` slot.
    _insert_package(db, pkg_id=pkg_id, linked_trade_id=primary_id)

    assert _resolve_linked_package_id(db, demo_id) == pkg_id
    assert _resolve_linked_package_id(db, reduce_id) == pkg_id
    assert _resolve_linked_package_id(db, primary_id) == pkg_id


def test_resolver_legacy_fallback_when_trade_row_lacks_column(tmp_path):
    """Pre-migration trade rows have ``order_package_id`` NULL — the
    resolver falls back to ``order_packages.linked_trade_id``.
    """
    db = _make_db(tmp_path)
    pkg_id = "pkg-legacy-001"
    # Insert a trade row WITHOUT order_package_id (simulates a row
    # written before the writer change shipped).
    trade_id = db.insert_trade({
        "timestamp": "2026-05-20T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_price": 80000.0,
        "stop_loss": 79500.0,
        "take_profit_1": 80500.0,
        "position_size": 0.001,
        "setup_type": "vwap",
        "entry_reason": "vwap signal",
        "status": "open",
        "is_backtest": 0,
        "is_demo": 0,
        "strategy_name": "vwap",
        "account_id": "bybit_2",
        "notes": "{}",
    })
    # Confirm the column is NULL.
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT order_package_id FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is None

    # Stand up a package that points back at the trade row via the
    # legacy one-way link.
    _insert_package(db, pkg_id=pkg_id, linked_trade_id=trade_id)

    assert _resolve_linked_package_id(db, trade_id) == pkg_id


def test_resolver_returns_none_when_no_link(tmp_path):
    db = _make_db(tmp_path)
    trade_id = _insert_leg(
        db, account_id="bybit_2", setup_type="vwap",
        is_demo=False, pkg_id=None,
    )
    assert _resolve_linked_package_id(db, trade_id) is None
