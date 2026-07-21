"""Tests for the shared real-schema sqlite fixture.

S-067 follow-up #1 — verifies the fixture produces a DB with the
canonical production schema (so any future column rename in
``src/units/db/database.py`` would also fail any test that uses the
fixture, instead of silently being out-of-sync).
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.fixtures.real_schema_db import (
    insert_order_package,
    insert_trade,
)


# Mirror the canonical schema columns asserted by these tests against
# what production code creates. Update this list ONLY when production
# adds/removes columns; that's exactly the schema-drift sentinel we
# want.
_EXPECTED_TRADES_COLS = {
    "id", "timestamp", "symbol", "direction", "entry_price",
    "exit_price", "stop_loss", "take_profit_1", "take_profit_2",
    "take_profit_3", "position_size", "setup_type", "killzone",
    "bias", "entry_reason", "exit_reason", "pnl", "pnl_percent",
    "status", "notes", "is_backtest", "strategy_name", "account_id",
    "created_at",
    # Added in feat(shadow) #1538: marks trades executed on demo/paper accounts
    # so PnL/stats queries can exclude them from live-account reporting.
    "is_demo",
    # Added 2026-06-15 (account_class convention): the paper/real-money
    # funding category mirrored from config/accounts.yaml::account_class.
    # Single source of truth for the paper/real reporting axis; is_demo is
    # kept in sync for back-compat.
    "account_class",
    # Added in PR #2046 (2026-05-26): many-to-one back-reference from a
    # trade row to the order_packages decision that produced it. Closes
    # the "(unlinked)" gap in the orphan-reconciler sweep ping for the
    # secondary legs of a multi-leg fanout (demo mirror, intent_reduce
    # flip leg, multi-account fanout). The legacy one-way
    # ``order_packages.linked_trade_id`` link still carries the
    # "primary entry trade" — written only by the real-money primary
    # leg of the fanout.
    "order_package_id",
    # Added 2026-06-16 (P1-A, dashboard-truth-and-persistence audit): the
    # canonical close timestamp, written as a real column on every close path
    # (P1-B) so close time stops being a read-time derivation
    # (order_packages.updated_at -> notes.closed_at JSON -> open time). NULL
    # until a close path stamps it; open rows and never-opened terminal rows
    # (rejected/exchange_rejected) legitimately leave it NULL.
    "closed_at",
    # Added 2026-06-24 (orphan-flap hardening #4): explicit reconcile state so
    # ORPHAN is a queryable, flagged terminal status rather than inferred from
    # setup_type/strategy_name/status. NULL=unspecified; 'unreconciled' (orphan
    # to resolve) / 'reconciled' (tied to its real package) / 'superseded'
    # (phantom flap dup void-flagged by the historical reconciliation pass).
    "reconcile_status",
    # Added 2026-06-29 (M18 P0a, capital-allocator cost capture): per-trade
    # transaction cost so the allocator's EV scorer has a cost feature + a
    # future learned ranker gets net-R labels. The close path stamps a
    # fixed-model 'estimate'; a broker-truth writer upgrades cost_source to
    # 'broker' + fills maker/funding. NULL on pre-migration + backtest rows.
    "fee_taker_usd", "fee_maker_usd", "funding_paid_usd", "cost_source",
    # Added 2026-07-17 (Slice B / B0, MB-20260629-ALLOC-COSTCAP): the broker's
    # entry orderId promoted from notes.trade_id to a first-class, indexed join
    # key, so the broker-truth cost sweep ties a trade to its exchange_fills
    # rows exactly. Observability-only; NULL on pre-migration rows.
    "broker_order_id",
    # Added 2026-07-21 (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP): this trade's own
    # tracked Bybit Partial-tpsl leg id(s), captured at entry so
    # modify_open_order can amend the SPECIFIC leg in place instead of always
    # adding a new one. NULL on pre-migration / non-Bybit / Full-mode /
    # ambiguous-capture rows.
    "sl_order_id", "tp_order_id",
}

_EXPECTED_ORDER_PACKAGES_COLS = {
    "order_package_id", "strategy_name", "symbol", "direction",
    "entry", "sl", "tp", "confidence", "signal_logic", "created_at",
    "updated_at", "status", "linked_trade_id", "close_reason", "meta",
    "model_scores",
    # ExitPlan layer (dynamic-take-profit consistency, P1): the static
    # exit-structure plan captured at signal time + its evolving state.
    "exit_plan", "exit_plan_state",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_make_canonical_db_creates_trades_table_with_full_schema(real_schema_db):
    db = real_schema_db()
    conn = sqlite3.connect(str(db))
    try:
        assert _columns(conn, "trades") == _EXPECTED_TRADES_COLS
    finally:
        conn.close()


def test_make_canonical_db_creates_order_packages_with_full_schema(real_schema_db):
    db = real_schema_db()
    conn = sqlite3.connect(str(db))
    try:
        assert _columns(conn, "order_packages") == _EXPECTED_ORDER_PACKAGES_COLS
    finally:
        conn.close()


def test_factory_pre_populates_trades(real_schema_db):
    db = real_schema_db(trades=[
        {"timestamp": "2026-05-09T10:00:00Z", "symbol": "BTCUSDT",
         "direction": "long", "entry_price": 60000.0,
         "position_size": 0.001, "status": "open", "is_backtest": 0},
    ])
    conn = sqlite3.connect(str(db))
    try:
        rows = list(conn.execute(
            "SELECT symbol, direction, status FROM trades"
        ))
    finally:
        conn.close()
    assert rows == [("BTCUSDT", "long", "open")]


def test_factory_pre_populates_order_packages(real_schema_db):
    db = real_schema_db(order_packages=[
        {"order_package_id": "pkg-1", "linked_trade_id": 42,
         "updated_at": "2026-05-09T10:42:00Z"},
    ])
    conn = sqlite3.connect(str(db))
    try:
        rows = list(conn.execute(
            "SELECT order_package_id, linked_trade_id FROM order_packages"
        ))
    finally:
        conn.close()
    assert rows == [("pkg-1", 42)]


def test_insert_trade_returns_row_id(real_schema_db):
    db = real_schema_db()
    rid = insert_trade(
        db, timestamp="2026-05-09T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, position_size=0.001,
        status="open", is_backtest=0,
    )
    assert rid == 1
    rid2 = insert_trade(
        db, timestamp="2026-05-09T10:01:00Z", symbol="ETHUSDT",
        direction="short", entry_price=3000.0, position_size=0.1,
        status="open", is_backtest=0,
    )
    assert rid2 == 2


def test_insert_order_package_requires_id(real_schema_db):
    db = real_schema_db()
    with pytest.raises(ValueError, match="order_package_id"):
        insert_order_package(db, linked_trade_id=1, updated_at="2026-05-09T10:00:00Z")


def test_factory_creates_isolated_dbs(real_schema_db):
    db1 = real_schema_db(name="a.db")
    db2 = real_schema_db(name="b.db")
    assert db1 != db2
    insert_trade(
        db1, timestamp="2026-05-09T10:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, position_size=0.001,
        status="open", is_backtest=0,
    )
    conn = sqlite3.connect(str(db2))
    try:
        rows = list(conn.execute("SELECT COUNT(*) FROM trades"))
    finally:
        conn.close()
    assert rows == [(0,)]


def test_make_canonical_db_creates_signals_table(real_schema_db):
    """Signals + backtest_results + strategy_versions are also created
    by the canonical builder; assert at least signals exists since the
    S-034 SQL log lives there."""
    db = real_schema_db()
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cur}
    finally:
        conn.close()
    assert "signals" in tables
    assert "backtest_results" in tables
    assert "strategy_versions" in tables
