"""Tests for the intent-mode reduce-as-partial-close fix.

Pre-fix, an intent-mode ``reduce_only`` leg (direction = opposite of the
current net) was journaled as a NEW ``status='open'`` row, which the net
helper read as a phantom opposite-direction position, the reverse reconciler
then closed, and the DB net snapped back — an infinite reduce-churn loop.

The fix (``apply_intent_reduce_partial_close`` +
``_log_trade_to_journal(intent_reduce=True)``) models the reduce as a FIFO
partial close of the existing parent rows. These tests exercise the pure
helper directly against a temp sqlite ``trades`` table mirroring the columns
the code touches, plus one regression test that the full ``_log_trade_to_journal``
path still inserts a normal open row for a non-reduce leg.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest


# --------------------------------------------------------------------------
# Minimal in-memory Database stand-in mirroring the real Database surface the
# code under test calls: ``connect()`` (sqlite3.Row connection), ``insert_trade``
# and ``update_trade``. Notification hooks are intentionally absent (the real
# ones are best-effort + swallowed), so we exercise the SQL behaviour only.
# --------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    exit_price REAL,
    stop_loss REAL,
    take_profit_1 REAL,
    position_size REAL,
    setup_type TEXT,
    entry_reason TEXT,
    exit_reason TEXT,
    pnl REAL,
    pnl_percent REAL,
    status TEXT DEFAULT 'open',
    is_backtest INTEGER DEFAULT 0,
    account_class TEXT,
    is_demo INTEGER DEFAULT 0,
    strategy_name TEXT,
    account_id TEXT,
    notes TEXT,
    order_package_id TEXT,
    closed_at TEXT,
    broker_order_id TEXT
);
"""


class FakeDB:
    """Tiny Database shim backed by a single shared sqlite3 connection."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # The helper/journal writer both call db.connect() and then .close() the
    # returned object. Hand back the shared connection wrapped so .close() is a
    # no-op (closing the real one would drop the in-memory DB).
    def connect(self):
        conn = self._conn

        class _NoCloseProxy:
            def execute(self, *a, **k):
                return conn.execute(*a, **k)

            def cursor(self):
                return conn.cursor()

            def commit(self):
                return conn.commit()

            def close(self):
                pass  # keep the shared connection alive

        return _NoCloseProxy()

    def insert_trade(self, trade_data):
        if "account_id" not in trade_data:
            trade_data = {**trade_data, "account_id": "live"}
        cols = ", ".join(trade_data.keys())
        ph = ", ".join("?" for _ in trade_data)
        cur = self._conn.cursor()
        cur.execute(
            f"INSERT INTO trades ({cols}) VALUES ({ph})",
            list(trade_data.values()),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_trade(self, trade_id, updates):
        row = dict(updates or {})
        if not row:
            return 0
        assignments = ", ".join(f"{k} = ?" for k in row)
        cur = self._conn.cursor()
        cur.execute(
            f"UPDATE trades SET {assignments} WHERE id = ?",
            list(row.values()) + [int(trade_id)],
        )
        self._conn.commit()
        return cur.rowcount

    def update_order_package(self, *a, **k):  # pragma: no cover - not exercised here
        return 0

    # Test helpers -------------------------------------------------------
    def rows(self):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM trades ORDER BY id ASC"
        ).fetchall()]

    def open_rows(self, direction=None):
        q = "SELECT * FROM trades WHERE status = 'open'"
        params: list = []
        if direction is not None:
            q += " AND direction = ?"
            params.append(direction)
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]


def _signed_net(db: FakeDB, account_id: str, symbol: str) -> float:
    """Mirror current_net_position_qty: signed sum over open, non-backtest rows."""
    net = 0.0
    for r in db._conn.execute(
        "SELECT direction, position_size FROM trades "
        "WHERE account_id = ? AND symbol = ? AND status = 'open' "
        "AND COALESCE(is_backtest, 0) = 0",
        (account_id, symbol),
    ).fetchall():
        d = (r["direction"] or "").lower()
        q = float(r["position_size"] or 0.0)
        if d == "long":
            net += q
        elif d == "short":
            net -= q
    return net


@pytest.fixture()
def db(tmp_path):
    return FakeDB(str(tmp_path / "trades.db"))


def _seed_long_parents(db: FakeDB, *, account="bybit_2", symbol="BTCUSDT"):
    """Two LONG parents: id1=0.5 (oldest), id2=0.3."""
    id1 = db.insert_trade({
        "timestamp": "t1", "symbol": symbol, "direction": "long",
        "entry_price": 100.0, "position_size": 0.5, "status": "open",
        "is_backtest": 0, "account_id": account, "strategy_name": "vwap",
    })
    id2 = db.insert_trade({
        "timestamp": "t2", "symbol": symbol, "direction": "long",
        "entry_price": 110.0, "position_size": 0.3, "status": "open",
        "is_backtest": 0, "account_id": account, "strategy_name": "vwap",
    })
    return id1, id2


# --------------------------------------------------------------------------
# Pure-helper tests
# --------------------------------------------------------------------------

def test_fifo_partial_does_not_close_or_create_open(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    id1, id2 = _seed_long_parents(db)
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_2", symbol="BTCUSDT",
        reduce_direction="short", reduce_qty=0.29,
        fill_price=120.0, closed_at_iso="now",
    )
    rows = {r["id"]: r for r in db.rows()}
    # id1 shrinks 0.5 -> 0.21, stays open; id2 untouched at 0.3.
    assert rows[id1]["position_size"] == pytest.approx(0.21)
    assert rows[id1]["status"] == "open"
    assert rows[id2]["position_size"] == pytest.approx(0.3)
    assert rows[id2]["status"] == "open"
    assert res["allocations"] == [{"parent_id": id1, "consumed": pytest.approx(0.29)}]
    assert res["leftover"] == pytest.approx(0.0)
    assert res["no_parent_position"] is False
    # net = 0.21 + 0.3 = +0.51 (the prompt's "+0.52" is an arithmetic slip;
    # 0.5 + 0.3 − 0.29 = 0.51 — the loop-stopping decremented value).
    assert _signed_net(db, "bybit_2", "BTCUSDT") == pytest.approx(0.51)


def test_full_consume_cascade(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    id1, id2 = _seed_long_parents(db)
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_2", symbol="BTCUSDT",
        reduce_direction="short", reduce_qty=0.6,
        fill_price=120.0, closed_at_iso="now",
    )
    rows = {r["id"]: r for r in db.rows()}
    assert rows[id1]["status"] == "closed"
    assert rows[id1]["exit_reason"] == "intent_reduce"
    assert rows[id1]["pnl"] is None
    assert rows[id2]["status"] == "open"
    assert rows[id2]["position_size"] == pytest.approx(0.2)
    assert res["leftover"] == pytest.approx(0.0)
    assert _signed_net(db, "bybit_2", "BTCUSDT") == pytest.approx(0.2)


def test_over_reduce_leaves_leftover(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    id1, id2 = _seed_long_parents(db)
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_2", symbol="BTCUSDT",
        reduce_direction="short", reduce_qty=1.0,
        fill_price=120.0, closed_at_iso="now",
    )
    rows = {r["id"]: r for r in db.rows()}
    assert rows[id1]["status"] == "closed"
    assert rows[id2]["status"] == "closed"
    assert res["leftover"] == pytest.approx(0.2)
    assert _signed_net(db, "bybit_2", "BTCUSDT") == pytest.approx(0.0)


def test_no_parent_position(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_2", symbol="BTCUSDT",
        reduce_direction="short", reduce_qty=0.4,
        fill_price=120.0, closed_at_iso="now",
    )
    assert res["no_parent_position"] is True
    assert res["allocations"] == []
    assert res["leftover"] == pytest.approx(0.4)
    assert _signed_net(db, "bybit_2", "BTCUSDT") == pytest.approx(0.0)
    # No new rows created by the helper itself.
    assert db.rows() == []


# --------------------------------------------------------------------------
# Full _log_trade_to_journal path tests
# --------------------------------------------------------------------------

def _make_pkg(direction="short"):
    return SimpleNamespace(
        symbol="BTCUSDT",
        direction=direction,
        entry=120.0,
        sl=118.0,
        tp=125.0,
        confidence=0.7,
        strategy="vwap",
        meta={
            "order_package_id": "op-123",
            "execution_delta": {
                "action": "reduce", "target_qty": 0.21,
                "current_qty": 0.5,
            },
        },
    )


def test_log_trade_journal_intent_reduce_is_partial_close(db, monkeypatch):
    """The real writer, intent_reduce=True, performs a partial close and
    writes exactly ONE status='closed' audit row — ZERO new open rows."""
    from src.units.accounts import execute as execmod

    # Patch the Database + path resolution used inside the function so the
    # writer operates on our in-memory FakeDB.
    import src.units.db.database as dbmod
    import src.utils.paths as pathsmod
    monkeypatch.setattr(pathsmod, "trade_journal_db_path", lambda: "ignored")
    monkeypatch.setattr(dbmod, "Database", lambda db_path=None: db)

    id1, id2 = _seed_long_parents(db)
    pkg = _make_pkg(direction="short")
    account_cfg = {"account_id": "bybit_2", "account_class": "real_money"}
    order = {"qty": 0.6, "symbol": "BTCUSDT"}

    ok = execmod._log_trade_to_journal(
        pkg, account_cfg, order, trade_id="ex-1",
        is_dry=False, intent_reduce=True,
    )
    assert ok is True

    rows = {r["id"]: r for r in db.rows()}
    # Parents: id1 fully consumed (closed), id2 shrunk to 0.2 (open).
    assert rows[id1]["status"] == "closed"
    assert rows[id2]["status"] == "open"
    assert rows[id2]["position_size"] == pytest.approx(0.2)

    # Exactly one new closed intent_reduce audit row, ZERO new open rows.
    audit = [r for r in db.rows() if r["setup_type"] == "intent_reduce"]
    assert len(audit) == 1
    assert audit[0]["status"] == "closed"
    assert audit[0]["direction"] == "short"
    assert audit[0]["position_size"] == pytest.approx(0.6)
    assert audit[0]["pnl"] is None
    assert "deferred_intent_reduce" in (audit[0]["notes"] or "")

    # No-phantom property: no status='open' row in the reduce direction.
    assert db.open_rows(direction="short") == []

    # Net convergence: +0.8 reduced by 0.6 -> +0.2.
    assert _signed_net(db, "bybit_2", "BTCUSDT") == pytest.approx(0.2)


def test_log_trade_journal_normal_open_unchanged(db, monkeypatch):
    """Regression: a non-reduce leg still inserts a status='open' row."""
    from src.units.accounts import execute as execmod
    import src.units.db.database as dbmod
    import src.utils.paths as pathsmod
    monkeypatch.setattr(pathsmod, "trade_journal_db_path", lambda: "ignored")
    monkeypatch.setattr(dbmod, "Database", lambda db_path=None: db)

    pkg = _make_pkg(direction="long")
    pkg.meta = {"order_package_id": "op-999"}
    account_cfg = {"account_id": "bybit_2", "account_class": "real_money"}
    order = {"qty": 0.4, "symbol": "BTCUSDT"}

    ok = execmod._log_trade_to_journal(
        pkg, account_cfg, order, trade_id="ex-2",
        is_dry=False, intent_reduce=False,
    )
    assert ok is True
    opens = db.open_rows(direction="long")
    assert len(opens) == 1
    assert opens[0]["position_size"] == pytest.approx(0.4)
    assert opens[0]["setup_type"] == "vwap"
    assert opens[0]["status"] == "open"
