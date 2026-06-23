"""Tests for the 2026-06-23 work-session fixes:

1. `_classify_broker_exit` — a reconciler-finalised broker close is labelled
   'sl' / 'tp' from exit price vs the package bracket (conservative inequality;
   mid-range → None so a manual flatten is never mislabelled).
2. `send_telegram_direct` returns False (not None, no raise) when credentials
   are missing — so the claude-bridge drainer keeps the file for retry instead
   of silently deleting an undelivered ping.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime import order_monitor
from src.runtime.notify import send_telegram_direct


class _FileDB:
    """Minimal db shim: each connect() opens the same on-disk sqlite file so
    the helpers' connect()/close() cycle preserves data (an in-memory db would
    vanish on close)."""

    def __init__(self, path: str) -> None:
        self._path = path

    def connect(self):
        return sqlite3.connect(self._path)


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "j.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, order_package_id TEXT, "
        "symbol TEXT, direction TEXT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (order_package_id TEXT, linked_trade_id INTEGER, "
        "symbol TEXT, direction TEXT, sl REAL, tp REAL, created_at TEXT)"
    )
    # One long BTC trade linked to a package with sl=62000, tp=68000.
    conn.execute("INSERT INTO trades VALUES (1,'pkg-long','BTCUSDT','long')")
    conn.execute(
        "INSERT INTO order_packages VALUES "
        "('pkg-long',1,'BTCUSDT','long',62000,68000,'2026-06-23T00:00:00+00:00')"
    )
    # One short MGC trade: sl=4200 (above entry), tp=3700 (below).
    conn.execute("INSERT INTO trades VALUES (2,'pkg-short','MGC','short')")
    conn.execute(
        "INSERT INTO order_packages VALUES "
        "('pkg-short',2,'MGC','short',4200,3700,'2026-06-23T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return _FileDB(path)


def _row(tid, symbol, direction):
    return {"id": tid, "symbol": symbol, "direction": direction}


def test_long_exit_at_or_below_sl_is_sl(db):
    # fill through the stop (slippage) still classifies as sl
    assert order_monitor._classify_broker_exit(db, _row(1, "BTCUSDT", "long"), 61950) == "sl"
    assert order_monitor._classify_broker_exit(db, _row(1, "BTCUSDT", "long"), 62000) == "sl"


def test_long_exit_at_or_above_tp_is_tp(db):
    assert order_monitor._classify_broker_exit(db, _row(1, "BTCUSDT", "long"), 68050) == "tp"


def test_long_midrange_exit_is_unresolved(db):
    # a manual/reconciler flatten between the bracket levels must NOT be mislabelled
    assert order_monitor._classify_broker_exit(db, _row(1, "BTCUSDT", "long"), 64000) is None


def test_short_exit_at_or_above_sl_is_sl(db):
    assert order_monitor._classify_broker_exit(db, _row(2, "MGC", "short"), 4250) == "sl"


def test_short_exit_at_or_below_tp_is_tp(db):
    assert order_monitor._classify_broker_exit(db, _row(2, "MGC", "short"), 3680) == "tp"


def test_short_midrange_exit_is_unresolved(db):
    assert order_monitor._classify_broker_exit(db, _row(2, "MGC", "short"), 4000) is None


def test_no_exit_price_returns_none(db):
    assert order_monitor._classify_broker_exit(db, _row(1, "BTCUSDT", "long"), 0) is None
    assert order_monitor._classify_broker_exit(db, _row(1, "BTCUSDT", "long"), None) is None


def test_intent_reduce_leg_is_never_classified(db):
    # Real-world case (trade 2807): an ADA position that was SHORT, reduced by a
    # LONG leg, so the package bracket is inverted (sl ABOVE entry). Without the
    # reduce-leg guard the long-side inequality (exit <= sl) would mislabel this
    # deliberate reduce as 'sl'. With is_reduce_leg=True it must return None so
    # the close keeps 'reconciler_filled'.
    conn = sqlite3.connect(db._path)
    conn.execute("INSERT INTO trades VALUES (3,'pkg-reduce','ADAUSDT','long')")
    conn.execute(
        "INSERT INTO order_packages VALUES "
        "('pkg-reduce',3,'ADAUSDT','long',0.1604,0.1364,'2026-06-23T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    row = _row(3, "ADAUSDT", "long")
    # Unguarded it WOULD classify (proving the guard is needed):
    assert order_monitor._classify_broker_exit(db, row, 0.1513) == "sl"
    # Guarded as a reduce leg → None (keeps reconciler_filled):
    assert order_monitor._classify_broker_exit(
        db, row, 0.1513, is_reduce_leg=True) is None


def test_send_telegram_direct_returns_false_when_creds_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # mirror_to_fcm=False so the test doesn't touch the mobile-push path.
    result = send_telegram_direct("hi", mirror_to_fcm=False)
    assert result is False
