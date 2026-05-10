"""S-067 follow-up #6 — exchange_fills_store unit tests."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime import exchange_fills_store as store


def _row(**overrides):
    base = {
        "exec_id": "exec-1",
        "account_id": "bybit_2",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "price": 60000.0,
        "qty": 0.001,
        "fee": 0.012,
        "fee_currency": "USDT",
        "exec_time": "2026-05-08T10:00:00+00:00",
        "order_id": "order-1",
        "is_maker": False,
        "raw": {"raw": "passthrough"},
    }
    base.update(overrides)
    return base


@pytest.fixture
def fills_db(tmp_path: Path) -> Path:
    return tmp_path / "exchange_fills.sqlite"


def test_init_db_creates_schema(fills_db):
    p = store.init_db(fills_db)
    assert p.exists()
    conn = sqlite3.connect(str(p))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(exchange_fills)")}
    finally:
        conn.close()
    assert {"exec_id", "account_id", "symbol", "side", "price", "qty",
            "fee", "fee_currency", "exec_time", "order_id", "is_maker",
            "raw", "inserted_at"}.issubset(cols)


def test_init_db_is_idempotent(fills_db):
    store.init_db(fills_db)
    # Second call should not raise.
    store.init_db(fills_db)


def test_upsert_inserts_new_row(fills_db):
    inserted = store.upsert_fills([_row()], path=fills_db)
    assert inserted == 1
    conn = sqlite3.connect(str(fills_db))
    try:
        rows = list(conn.execute("SELECT exec_id, qty FROM exchange_fills"))
    finally:
        conn.close()
    assert rows == [("exec-1", 0.001)]


def test_upsert_dedupes_by_exec_id(fills_db):
    """Same exec_id inserted twice produces a single row."""
    inserted_first = store.upsert_fills([_row()], path=fills_db)
    inserted_second = store.upsert_fills([_row()], path=fills_db)
    assert inserted_first == 1
    assert inserted_second == 0
    conn = sqlite3.connect(str(fills_db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM exchange_fills").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_upsert_handles_mixed_new_and_duplicate(fills_db):
    store.upsert_fills([_row(exec_id="a"), _row(exec_id="b")], path=fills_db)
    inserted = store.upsert_fills(
        [_row(exec_id="a"), _row(exec_id="c")],  # 'a' duplicate, 'c' new
        path=fills_db,
    )
    assert inserted == 1


def test_aggregate_summary_window_filter(fills_db):
    now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    store.upsert_fills(
        [
            _row(exec_id="a", exec_time="2026-05-09T12:00:00+00:00", fee=0.10),
            _row(exec_id="b", exec_time="2026-05-08T12:00:00+00:00", fee=0.20),
            _row(exec_id="c", exec_time="2026-05-01T12:00:00+00:00", fee=99.0),
            # Out of window
        ],
        path=fills_db,
    )
    summary = store.aggregate_summary(days=3, path=fills_db, now=now)
    assert summary["fill_count"] == 2  # only 'a' and 'b' within 3-day window
    assert abs(summary["total_fees"] - 0.30) < 1e-9
    assert summary["window_days"] == 3


def test_aggregate_by_symbol_groups_correctly(fills_db):
    now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    store.upsert_fills(
        [
            _row(exec_id="a", symbol="BTC/USDT:USDT",
                 exec_time="2026-05-09T12:00:00+00:00",
                 qty=0.001, price=60000.0, fee=0.10),
            _row(exec_id="b", symbol="BTC/USDT:USDT",
                 exec_time="2026-05-09T13:00:00+00:00",
                 qty=0.002, price=60500.0, fee=0.20),
            _row(exec_id="c", symbol="ETH/USDT:USDT",
                 exec_time="2026-05-09T14:00:00+00:00",
                 qty=0.5, price=3000.0, fee=0.30),
        ],
        path=fills_db,
    )
    rows = store.aggregate_by_symbol(days=7, path=fills_db, now=now)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["BTC/USDT:USDT"]["fill_count"] == 2
    assert abs(by_sym["BTC/USDT:USDT"]["gross_qty"] - 0.003) < 1e-9
    # gross_notional = 0.001*60000 + 0.002*60500 = 60 + 121 = 181
    assert abs(by_sym["BTC/USDT:USDT"]["gross_notional"] - 181.0) < 1e-9
    assert abs(by_sym["BTC/USDT:USDT"]["total_fees"] - 0.30) < 1e-9
    assert by_sym["ETH/USDT:USDT"]["fill_count"] == 1


def test_aggregate_returns_empty_when_db_missing(tmp_path):
    summary = store.aggregate_summary(days=7, path=tmp_path / "missing.db")
    assert summary == {"fill_count": 0, "total_fees": 0.0,
                       "symbol_count": 0, "window_days": 7}
    assert store.aggregate_by_symbol(days=7, path=tmp_path / "missing.db") == []


def test_aggregate_returns_empty_for_zero_or_negative_days(fills_db):
    store.upsert_fills([_row()], path=fills_db)
    assert store.aggregate_by_symbol(days=0, path=fills_db) == []
    assert store.aggregate_by_symbol(days=-1, path=fills_db) == []
    summary = store.aggregate_summary(days=0, path=fills_db)
    assert summary["fill_count"] == 0


def test_get_fills_db_path_respects_env(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("EXCHANGE_FILLS_DB", str(custom))
    assert store.get_fills_db_path() == custom
