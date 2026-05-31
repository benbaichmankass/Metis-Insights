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


# ---------------------------------------------------------------------------
# Per-strategy net-of-fee attribution (cross-zero P3c)
# ---------------------------------------------------------------------------

def _now():
    return datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)


def test_fifo_pnl_by_strategy_basic_gross_net_fees(fills_db):
    """One strategy: buy then sell at +1000 move, fees on both fills.

    gross = (sell-buy)*qty ; net = gross - fees ; the three reconcile.
    """
    store.upsert_fills([
        _row(exec_id="e1", order_id="o1", side="buy",  price=60_000.0, qty=1.0,
             fee=6.0, exec_time="2026-05-08T10:00:00+00:00"),
        _row(exec_id="e2", order_id="o2", side="sell", price=61_000.0, qty=1.0,
             fee=6.1, exec_time="2026-05-08T11:00:00+00:00"),
    ], path=fills_db)

    rows = store.fifo_pnl_by_strategy(
        days=7, strategy_of_order_id={"o1": "vwap", "o2": "vwap"},
        path=fills_db, now=_now(),
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["strategy"] == "vwap"
    assert r["gross_pnl"] == pytest.approx(1000.0, abs=1e-6)
    assert r["total_fees"] == pytest.approx(12.1, abs=1e-6)
    assert r["net_pnl"] == pytest.approx(1000.0 - 12.1, abs=1e-6)
    assert r["fill_count"] == 2
    # gross - fees == net (reconciles)
    assert r["gross_pnl"] - r["total_fees"] == pytest.approx(r["net_pnl"], abs=1e-6)


def test_fifo_pnl_by_strategy_fee_drag_headline(fills_db):
    """Reproduces the audit headline: a thin gross edge buried by fees so that
    fee_pct_of_gross >> 100% (vwap was 418%)."""
    # Gross edge of +$10 (1.0 BTC * $10 move), but $42 of fees → net negative.
    store.upsert_fills([
        _row(exec_id="e1", order_id="o1", side="buy",  price=60_000.0, qty=1.0,
             fee=21.0, exec_time="2026-05-08T10:00:00+00:00"),
        _row(exec_id="e2", order_id="o2", side="sell", price=60_010.0, qty=1.0,
             fee=21.0, exec_time="2026-05-08T11:00:00+00:00"),
    ], path=fills_db)

    rows = store.fifo_pnl_by_strategy(
        days=7, strategy_of_order_id={"o1": "vwap", "o2": "vwap"},
        path=fills_db, now=_now(),
    )
    r = rows[0]
    assert r["gross_pnl"] == pytest.approx(10.0, abs=1e-6)
    assert r["total_fees"] == pytest.approx(42.0, abs=1e-6)
    assert r["net_pnl"] == pytest.approx(-32.0, abs=1e-6)
    assert r["fee_pct_of_gross"] == pytest.approx(420.0, abs=1e-3)  # 42/10*100


def test_fifo_pnl_by_strategy_partitions_and_unattributed(fills_db):
    store.upsert_fills([
        _row(exec_id="e1", order_id="o1", side="buy",  price=100.0, qty=1.0, fee=0.1),
        _row(exec_id="e2", order_id="o2", side="sell", price=110.0, qty=1.0, fee=0.1),
        _row(exec_id="e3", order_id="oX", side="buy",  price=100.0, qty=1.0, fee=0.1),  # unmapped
    ], path=fills_db)
    rows = store.fifo_pnl_by_strategy(
        days=7, strategy_of_order_id={"o1": "trend_donchian", "o2": "trend_donchian"},
        path=fills_db, now=_now(),
    )
    names = {r["strategy"] for r in rows}
    assert "trend_donchian" in names
    assert "unattributed" in names  # the oX fill is bucketed, not dropped


def test_fifo_pnl_by_strategy_zero_gross_fee_pct_is_none(fills_db):
    """fee_pct_of_gross is None (undefined) when gross ~0, never inf/divide error."""
    store.upsert_fills([
        _row(exec_id="e1", order_id="o1", side="buy",  price=100.0, qty=1.0, fee=0.1),
        _row(exec_id="e2", order_id="o2", side="sell", price=100.0, qty=1.0, fee=0.1),
    ], path=fills_db)
    rows = store.fifo_pnl_by_strategy(
        days=7, strategy_of_order_id={"o1": "x", "o2": "x"}, path=fills_db, now=_now(),
    )
    assert rows[0]["fee_pct_of_gross"] is None


def test_fifo_pnl_by_strategy_empty_and_missing_db(tmp_path, fills_db):
    assert store.fifo_pnl_by_strategy(days=0, strategy_of_order_id={}, path=fills_db) == []
    assert store.fifo_pnl_by_strategy(
        days=7, strategy_of_order_id={}, path=tmp_path / "missing.db") == []
