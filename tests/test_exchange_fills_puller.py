"""S-067 follow-up #6 — exchange_fills_puller unit tests with mocked
ccxt fetch_my_trades, no network."""
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime.exchange_fills_puller import (
    _ccxt_trade_to_fill_row,
    fetch_fills_window,
)


def _ccxt_trade(exec_id="exec-1", symbol="BTC/USDT:USDT", **overrides):
    base = {
        "id": exec_id,
        "order": "ord-1",
        "symbol": symbol,
        "side": "buy",
        "price": 60000.0,
        "amount": 0.001,
        "fee": {"cost": 0.012, "currency": "USDT"},
        "timestamp": 1778328000000,  # 2026-05-09T12:00:00Z
        "takerOrMaker": "taker",
        "info": {"raw": "from_bybit"},
    }
    base.update(overrides)
    return base


def test_ccxt_trade_to_fill_row_maps_all_fields():
    out = _ccxt_trade_to_fill_row(_ccxt_trade(), account_id="bybit_2")
    assert out["exec_id"] == "exec-1"
    assert out["account_id"] == "bybit_2"
    assert out["symbol"] == "BTC/USDT:USDT"
    assert out["side"] == "buy"
    assert out["price"] == 60000.0
    assert out["qty"] == 0.001
    assert out["fee"] == 0.012
    assert out["fee_currency"] == "USDT"
    assert out["exec_time"].startswith("2026-05-09")
    assert out["order_id"] == "ord-1"
    assert out["is_maker"] is False
    assert out["raw"] == {"raw": "from_bybit"}


def test_ccxt_trade_to_fill_row_handles_missing_fee():
    out = _ccxt_trade_to_fill_row(_ccxt_trade(fee=None), account_id="x")
    assert out["fee"] == 0.0
    assert out["fee_currency"] is None


def test_fetch_fills_window_invokes_per_symbol():
    calls = []

    def fake_fetch_my_trades(symbol, since, limit, params):
        calls.append({"symbol": symbol, "since": since, "limit": limit})
        return [_ccxt_trade(exec_id=f"{symbol}-1"),
                _ccxt_trade(exec_id=f"{symbol}-2")]

    now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    rows = fetch_fills_window(
        fake_fetch_my_trades,
        account_id="bybit_2",
        days=2,
        now=now,
        symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
    )
    assert len(rows) == 4
    assert {c["symbol"] for c in calls} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    # since = now - 2 days = 2026-05-08T12:00:00Z
    assert all(c["since"] == 1778241600000 for c in calls)


def test_fetch_fills_window_no_symbols_calls_once_with_none():
    calls = []

    def fake_fetch_my_trades(symbol, since, limit, params):
        calls.append(symbol)
        return []

    fetch_fills_window(
        fake_fetch_my_trades, account_id="x", days=1,
    )
    assert calls == [None]


def test_fetch_fills_window_skips_failing_symbol(caplog):
    """A network/auth failure on one symbol must not abort the others —
    log loudly, skip, and continue."""
    def fake_fetch_my_trades(symbol, since, limit, params):
        if symbol == "FAIL/USDT:USDT":
            raise RuntimeError("synthetic bybit error")
        return [_ccxt_trade(exec_id=f"{symbol}-1")]

    rows = fetch_fills_window(
        fake_fetch_my_trades,
        account_id="bybit_2",
        days=1,
        symbols=["BTC/USDT:USDT", "FAIL/USDT:USDT", "ETH/USDT:USDT"],
    )
    assert len(rows) == 2
    assert {r["exec_id"] for r in rows} == {
        "BTC/USDT:USDT-1", "ETH/USDT:USDT-1",
    }


def test_fetch_fills_window_drops_rows_without_exec_id():
    def fake_fetch_my_trades(symbol, since, limit, params):
        return [
            _ccxt_trade(exec_id="ok"),
            _ccxt_trade(exec_id=None),  # malformed
            _ccxt_trade(exec_id=""),    # malformed
        ]

    rows = fetch_fills_window(
        fake_fetch_my_trades, account_id="x", days=1,
        symbols=["BTC/USDT:USDT"],
    )
    assert len(rows) == 1
    assert rows[0]["exec_id"] == "ok"
