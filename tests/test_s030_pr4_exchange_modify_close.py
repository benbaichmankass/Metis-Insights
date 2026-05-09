"""S-030 PR4 regression tests — exchange-side modify/close helpers
wired into the monitor loop.

Pre-PR3, monitor verdicts updated only the DB. PR4 adds:
  * ``modify_open_order`` / ``close_open_position`` in execute.py —
    Bybit Unified Trading helpers wrapping ``set_trading_stop`` and
    a reduce-only ``place_order``.
  * Direct wiring in ``order_monitor._apply_update`` — every monitor
    verdict that produces a matched trade row also dispatches the
    update to the live exchange.

The original PR4 wiring was env-gated on ``MONITOR_APPLY_TO_EXCHANGE``
("shadow mode" = DB-only). Operator directive 2026-05-03 deleted that
gate (per-account ``RiskManager.dry_run`` is the only dry/live toggle
in the codebase, and on 2026-05-09 the gate stranded a vwap close
because monitor flipped DB to ``status='closed'`` while the live
position kept consuming margin).
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.data_layer.database import Database
from src.runtime import order_monitor as om
from src.units.accounts.execute import (
    close_open_position,
    modify_open_order,
)


# ---------------------------------------------------------------------------
# modify_open_order
# ---------------------------------------------------------------------------


class _StubBybit:
    """Captures kwargs for set_trading_stop + place_order; returns a
    canned retCode=0 response."""

    def __init__(self, *, ret_code=0, ret_msg="OK", order_id="STUB-1"):
        self._ret_code = ret_code
        self._ret_msg = ret_msg
        self._order_id = order_id
        self.set_trading_stop_calls = []
        self.place_order_calls = []

    def set_trading_stop(self, **kwargs):
        self.set_trading_stop_calls.append(kwargs)
        return {"retCode": self._ret_code, "retMsg": self._ret_msg, "result": {}}

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        return {
            "retCode": self._ret_code, "retMsg": self._ret_msg,
            "result": {"orderId": self._order_id},
        }


class TestModifyOpenOrder:
    def test_bybit_set_trading_stop_called_with_sl_only(self):
        client = _StubBybit()
        cfg = {"account_id": "bybit_2", "exchange": "bybit"}
        result = modify_open_order(client, cfg, symbol="BTCUSDT", sl=49500.0)

        assert result["ok"] is True
        assert client.set_trading_stop_calls[0] == {
            "category": "linear", "symbol": "BTCUSDT", "stopLoss": "49500.0",
        }

    def test_bybit_set_trading_stop_called_with_tp_only(self):
        client = _StubBybit()
        cfg = {"account_id": "bybit_2", "exchange": "bybit"}
        result = modify_open_order(client, cfg, symbol="BTCUSDT", tp=51000.0)

        assert result["ok"] is True
        assert client.set_trading_stop_calls[0] == {
            "category": "linear", "symbol": "BTCUSDT", "takeProfit": "51000.0",
        }

    def test_bybit_atomic_sl_and_tp(self):
        client = _StubBybit()
        result = modify_open_order(
            client, {"exchange": "bybit"}, symbol="BTCUSDT",
            sl=49500.0, tp=51000.0,
        )
        assert result["ok"] is True
        kwargs = client.set_trading_stop_calls[0]
        assert kwargs["stopLoss"] == "49500.0"
        assert kwargs["takeProfit"] == "51000.0"

    def test_bybit_non_zero_retcode_marks_not_ok(self):
        client = _StubBybit(ret_code=10001, ret_msg="invalid sl")
        result = modify_open_order(
            client, {"exchange": "bybit"}, symbol="BTCUSDT", sl=1.0,
        )
        assert result["ok"] is False
        assert "invalid sl" in result["error"]

    def test_bybit_raises_caught_returns_not_ok(self):
        class _Boom:
            def set_trading_stop(self, **kwargs):
                raise RuntimeError("network down")

        result = modify_open_order(
            _Boom(), {"exchange": "bybit"}, symbol="BTCUSDT", sl=49500.0,
        )
        assert result["ok"] is False
        assert "RuntimeError" in result["error"]

    def test_no_client_returns_not_ok(self):
        result = modify_open_order(
            None, {"exchange": "bybit"}, symbol="BTCUSDT", sl=49500.0,
        )
        assert result["ok"] is False

    def test_no_sl_or_tp_returns_not_ok(self):
        client = _StubBybit()
        result = modify_open_order(client, {"exchange": "bybit"}, symbol="BTCUSDT")
        assert result["ok"] is False
        assert client.set_trading_stop_calls == []  # never called

    def test_unsupported_exchange_returns_not_ok(self):
        client = _StubBybit()
        result = modify_open_order(
            client, {"exchange": "binance"}, symbol="BTCUSDT", sl=49500.0,
        )
        assert result["ok"] is False
        assert "binance" in result["error"]


# ---------------------------------------------------------------------------
# close_open_position
# ---------------------------------------------------------------------------


class TestCloseOpenPosition:
    def test_long_close_dispatches_sell_reduce_only(self):
        client = _StubBybit(order_id="CLOSE-LONG-1")
        result = close_open_position(
            client, {"exchange": "bybit"},
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is True
        assert result["exchange_order_id"] == "CLOSE-LONG-1"
        kwargs = client.place_order_calls[0]
        assert kwargs["side"] == "Sell"
        assert kwargs["reduceOnly"] is True
        assert kwargs["qty"] == "0.001"

    def test_short_close_dispatches_buy_reduce_only(self):
        client = _StubBybit(order_id="CLOSE-SHORT-1")
        result = close_open_position(
            client, {"exchange": "bybit"},
            symbol="BTCUSDT", side="short", qty=0.002,
        )
        assert result["ok"] is True
        assert result["exchange_order_id"] == "CLOSE-SHORT-1"
        kwargs = client.place_order_calls[0]
        assert kwargs["side"] == "Buy"
        assert kwargs["reduceOnly"] is True

    def test_zero_qty_returns_not_ok(self):
        client = _StubBybit()
        result = close_open_position(
            client, {"exchange": "bybit"},
            symbol="BTCUSDT", side="long", qty=0.0,
        )
        assert result["ok"] is False
        assert client.place_order_calls == []

    def test_no_client_returns_not_ok(self):
        result = close_open_position(
            None, {"exchange": "bybit"},
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is False

    def test_bybit_raises_caught(self):
        class _Boom:
            def place_order(self, **kwargs):
                raise RuntimeError("rate limited")

        result = close_open_position(
            _Boom(), {"exchange": "bybit"},
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is False
        assert "rate limited" in result["error"]

    def test_unsupported_exchange_returns_not_ok(self):
        client = _StubBybit()
        result = close_open_position(
            client, {"exchange": "binance"},
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Env-gated wiring inside order_monitor
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return Database(db_path=str(db_path))


def _seed(db, *, pkg_id="pkg-1", strategy="vwap", direction="long",
          symbol="BTCUSDT"):
    db.insert_order_package({
        "order_package_id": pkg_id, "strategy_name": strategy,
        "symbol": symbol, "direction": direction,
        "entry": 100.0, "sl": 98.0, "tp": 104.0, "confidence": 0.6,
    })
    db.insert_trade({
        "timestamp": "2026-05-02T20:00:00+00:00",
        "symbol": symbol, "direction": direction,
        "entry_price": 100.0, "stop_loss": 98.0, "take_profit_1": 104.0,
        "position_size": 0.001, "status": "open", "is_backtest": 0,
        "strategy_name": strategy, "account_id": "bybit_2",
        "setup_type": strategy,
    })


def _candles(close_price):
    return pd.DataFrame({
        "open": [close_price], "high": [close_price * 1.001],
        "low": [close_price * 0.999], "close": [close_price],
        "volume": [100.0],
    })


class TestExchangeDispatch:
    """The 2026-05-03 operator directive removed the
    ``MONITOR_APPLY_TO_EXCHANGE`` "shadow-mode" env-gate. The monitor
    loop now always dispatches to the exchange when there's a matched
    trade row; per-account ``RiskManager.dry_run`` is the single
    dry/live toggle."""

    def test_close_dispatches_to_exchange(self, tmp_db, monkeypatch):
        _seed(tmp_db)

        captured = []
        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=lambda t: (captured.append(t), {"ok": True})[-1],
        ), patch(
            "src.units.strategies.vwap.monitor",
            return_value={"action": "close", "reason": "test"},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(99.0),
            )

        assert len(captured) == 1
        assert captured[0]["account_id"] == "bybit_2"
        assert captured[0]["symbol"] == "BTCUSDT"

    def test_modify_dispatches_to_exchange(self, tmp_db, monkeypatch):
        _seed(tmp_db)

        captured = []
        def _stub_modify(matched, *, sl=None, tp=None):
            captured.append({"trade": matched, "sl": sl, "tp": tp})
            return {"ok": True}

        with patch(
            "src.runtime.order_monitor._send_modify_to_exchange",
            side_effect=_stub_modify,
        ), patch(
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 100.0},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        assert len(captured) == 1
        assert captured[0]["sl"] == 100.0
        assert captured[0]["tp"] is None

    def test_modify_with_no_open_trade_skips_exchange(
        self, tmp_db, monkeypatch,
    ):
        """If there's a package but no matching open trade row, the
        monitor still updates the DB but doesn't try to call the
        exchange (no account_id to dispatch to)."""
        # Only insert a package, NOT a trade row.
        tmp_db.insert_order_package({
            "order_package_id": "pkg-orphan", "strategy_name": "vwap",
            "symbol": "BTCUSDT", "direction": "long",
            "entry": 100.0, "sl": 98.0, "tp": 104.0,
        })

        send_modify_calls = []
        with patch(
            "src.runtime.order_monitor._send_modify_to_exchange",
            side_effect=lambda *a, **kw: send_modify_calls.append((a, kw)),
        ), patch(
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 100.0},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        assert send_modify_calls == []
        # DB row updated even though exchange wasn't touched.
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 100.0
