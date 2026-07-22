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

import json
import logging
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
        # 2026-05-06 operator directive: _bybit_category now defaults to
        # "spot" when market_type is omitted (the perp-instead-of-spot
        # fix). set_trading_stop is only valid for derivatives, so this
        # test pins the linear path by setting market_type explicitly.
        cfg = {"account_id": "bybit_2", "exchange": "bybit",
               "market_type": "linear"}
        result = modify_open_order(client, cfg, symbol="BTCUSDT", sl=49500.0)

        assert result["ok"] is True
        # 2026-05 tick-size refactor: modify_open_order now quantizes
        # sl/tp to the resolved tick (0.01 fallback for BTCUSDT) via
        # quantize_price, so the value carries the tick's decimal places.
        assert client.set_trading_stop_calls[0] == {
            "category": "linear", "symbol": "BTCUSDT", "stopLoss": "49500.00",
        }

    def test_bybit_set_trading_stop_called_with_tp_only(self):
        client = _StubBybit()
        # market_type=linear pins the derivatives path (see sl-only test).
        cfg = {"account_id": "bybit_2", "exchange": "bybit",
               "market_type": "linear"}
        result = modify_open_order(client, cfg, symbol="BTCUSDT", tp=51000.0)

        assert result["ok"] is True
        # 2026-05 tick-size refactor: tp quantized to the 0.01 fallback tick.
        assert client.set_trading_stop_calls[0] == {
            "category": "linear", "symbol": "BTCUSDT", "takeProfit": "51000.00",
        }

    def test_bybit_atomic_sl_and_tp(self):
        client = _StubBybit()
        # market_type=linear: _bybit_category defaults to "spot" when
        # omitted (2026-05-06), and set_trading_stop is derivatives-only.
        result = modify_open_order(
            client, {"exchange": "bybit", "market_type": "linear"},
            symbol="BTCUSDT", sl=49500.0, tp=51000.0,
        )
        assert result["ok"] is True
        kwargs = client.set_trading_stop_calls[0]
        # 2026-05 tick-size refactor: both legs quantized to the 0.01 tick.
        assert kwargs["stopLoss"] == "49500.00"
        assert kwargs["takeProfit"] == "51000.00"

    def test_bybit_non_zero_retcode_marks_not_ok(self):
        client = _StubBybit(ret_code=10001, ret_msg="invalid sl")
        result = modify_open_order(
            client, {"exchange": "bybit", "market_type": "linear"},
            symbol="BTCUSDT", sl=1.0,
        )
        assert result["ok"] is False
        assert "invalid sl" in result["error"]

    def test_bybit_raises_caught_returns_not_ok(self):
        class _Boom:
            def set_trading_stop(self, **kwargs):
                raise RuntimeError("network down")

        result = modify_open_order(
            _Boom(), {"exchange": "bybit", "market_type": "linear"},
            symbol="BTCUSDT", sl=49500.0,
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
            client, {"exchange": "kraken"}, symbol="BTCUSDT", sl=49500.0,
        )
        assert result["ok"] is False
        assert "kraken" in result["error"]


# ---------------------------------------------------------------------------
# close_open_position
# ---------------------------------------------------------------------------


class TestCloseOpenPosition:
    def test_long_close_dispatches_sell_reduce_only(self):
        client = _StubBybit(order_id="CLOSE-LONG-1")
        # market_type=linear: spot (the default when omitted) has no
        # reduceOnly; this test pins the derivatives reduce-only close.
        result = close_open_position(
            client, {"exchange": "bybit", "market_type": "linear"},
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
            client, {"exchange": "bybit", "market_type": "linear"},
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
            client, {"exchange": "kraken"},
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
        def _stub_modify(matched, *, sl=None, tp=None, **kwargs):
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

    def test_modify_with_no_open_trade_leaves_db_unchanged_and_logs_error(
        self, tmp_db, caplog,
    ):
        """2026-05-18 modify-path exchange-first refactor: when the
        package has no matching open trade row, the modify path now
        logs ERROR and leaves the DB row unchanged so the strategy
        verdict re-fires next tick. Pre-this-PR the DB was flipped to
        the new SL/TP even though no exchange call ever fired — the
        strategy + dashboard showed an SL move that hadn't actually
        reached Bybit. Live impact was SL-to-break-even verdicts
        getting silently dropped and trades running to their original
        SL."""
        # Only insert a package, NOT a trade row.
        tmp_db.insert_order_package({
            "order_package_id": "pkg-orphan", "strategy_name": "vwap",
            "symbol": "BTCUSDT", "direction": "long",
            "entry": 100.0, "sl": 98.0, "tp": 104.0,
        })

        send_modify_calls = []
        with patch(
            "src.runtime.order_monitor._send_modify_to_exchange",
            side_effect=lambda *a, **kw: (send_modify_calls.append((a, kw)),
                                          {"ok": True})[-1],
        ), patch(
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 100.0},
        ), caplog.at_level("ERROR", logger="src.runtime.order_monitor"):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        # Exchange not called (no trade row → no account_id to dispatch).
        assert send_modify_calls == []
        # DB row UNCHANGED so the verdict re-fires next tick when the
        # linkage lands.
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 98.0
        # ERROR logged so the operator + health-review see the miss
        # instead of the silent skip we used to have.
        error_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "ERROR"
        ]
        assert any("modify-path trade lookup returned no open row"
                   in m for m in error_messages), error_messages

    def test_modify_writes_db_only_after_exchange_success(self, tmp_db):
        """2026-05-18: the modify path is now exchange-first. The
        ``order_packages`` row's sl/tp must NOT be flipped until the
        exchange call returns ok=True. Mirrors the close-path
        invariant from PR #1190."""
        _seed(tmp_db)

        call_order: list[str] = []

        def _stub_modify(matched, *, sl=None, tp=None, **kwargs):
            # Capture the DB state visible to the exchange caller — if
            # exchange-first ordering holds, the DB row's sl/tp are
            # still the seed values (98.0 / 104.0).
            current = tmp_db.get_order_packages_by_strategy("vwap")[0]
            call_order.append(f"exchange:sl={current['sl']}")
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

        # The exchange call observed the OLD sl (98.0), then the DB
        # was updated to the new value (100.0) only after ok=True.
        assert call_order == ["exchange:sl=98.0"]
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 100.0

    def test_modify_leaves_db_unchanged_when_exchange_fails(self, tmp_db, caplog):
        """2026-05-18: exchange-first ordering must NOT touch the DB
        when ``_send_modify_to_exchange`` returns ok=False. The next
        monitor tick re-attempts; the journal never lies about an SL
        move that didn't reach Bybit."""
        _seed(tmp_db)

        with patch(
            "src.runtime.order_monitor._send_modify_to_exchange",
            return_value={"ok": False, "error": "Bybit retCode=10001 SL race"},
        ), patch(
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 100.0, "tp": 105.0},
        ), caplog.at_level("ERROR", logger="src.runtime.order_monitor"):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 98.0  # seed value, untouched
        assert rows[0]["tp"] == 104.0  # seed value, untouched
        error_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "ERROR"
        ]
        assert any("exchange modify failed — leaving DB unchanged"
                   in m for m in error_messages), error_messages

    def test_modify_dry_run_short_circuit_writes_db(self, tmp_db, monkeypatch):
        """2026-05-18: the dry-run short-circuit in
        ``_send_modify_to_exchange`` returns ok=True without calling
        ``modify_open_order`` so paper accounts still book the DB
        update. Mirrors the close-path dry-run handling."""
        _seed(tmp_db)

        # Stub the modify wrapper itself so we never touch
        # ``modify_open_order`` even by accident.
        modify_calls = []

        def _stub_modify(client, cfg, *, symbol, sl=None, tp=None):
            modify_calls.append((symbol, sl, tp))
            return {"ok": True}

        # Force the resolved account cfg to mode=dry_run.
        def _stub_build(account_id):
            return object(), {
                "account_id": account_id,
                "exchange": "bybit",
                "mode": "dry_run",
                "market_type": "linear",
            }

        monkeypatch.setattr(
            "src.runtime.order_monitor._build_account_client", _stub_build,
        )
        monkeypatch.setattr(
            "src.units.accounts.execute.modify_open_order", _stub_modify,
        )

        with patch(
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 100.0},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        # Dry-run path skipped ``modify_open_order`` entirely.
        assert modify_calls == []
        # DB row still updated as if the live call had succeeded.
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 100.0

    def test_modify_skips_immaterial_change_no_exchange_call_no_db_write(
        self, tmp_db,
    ):
        """BL-20260722-XRP-SLSPAM regression. A monitor() verdict whose new
        sl differs from the package's current sl by only float/live-candle
        noise (well under the 0.05% tolerance) must be dropped before any
        exchange call, DB write, or notification — this is what fired an
        exchange amend + a "TRADE UPDATED" Telegram ping every tick for 5
        days on trade 3577 even though the operator correctly saw the SL as
        static."""
        _seed(tmp_db)  # seed sl=98.0

        modify_calls = []
        with patch(
            "src.runtime.order_monitor._send_modify_to_exchange",
            side_effect=lambda *a, **kw: (modify_calls.append((a, kw)),
                                          {"ok": True})[-1],
        ), patch(
            # 98.0 * 1.00001 is a 0.001% bump — far under the 0.05% gate.
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 98.0 * 1.00001},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        assert modify_calls == []
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 98.0

    def test_modify_still_fires_on_meaningful_change(self, tmp_db):
        """Sibling of the noise-gate test above — a genuine trail step
        (well over the 0.05% tolerance) must still reach the exchange and
        the DB, unchanged from pre-gate behaviour."""
        _seed(tmp_db)  # seed sl=98.0

        modify_calls = []
        with patch(
            "src.runtime.order_monitor._send_modify_to_exchange",
            side_effect=lambda *a, **kw: (modify_calls.append((a, kw)),
                                          {"ok": True})[-1],
        ), patch(
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 99.0},  # ~1% move — clears the gate
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        assert len(modify_calls) == 1
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 99.0

    def test_modify_syncs_trades_stop_loss_after_success(self, tmp_db):
        """BL-20260722-XRP-SLSPAM regression (part 2). Before this fix a
        successful modify only ever wrote ``order_packages.sl`` — the
        linked ``trades.stop_loss`` (what /api/bot/positions surfaces to
        the dashboard/Android app as Position.stopLoss) never moved after
        the first modify, so the operator-facing view of the stop went
        stale forever while the strategy's real internal stop kept
        trailing underneath it."""
        _seed(tmp_db)  # seed sl=98.0

        with patch(
            "src.runtime.order_monitor._send_modify_to_exchange",
            return_value={"ok": True},
        ), patch(
            "src.units.strategies.vwap.monitor",
            return_value={"sl": 99.0, "tp": 105.0},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t: _candles(102.0),
            )

        pkg_rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert pkg_rows[0]["sl"] == 99.0
        assert pkg_rows[0]["tp"] == 105.0

        trade_rows = tmp_db.get_trades(filters={"account_id": "bybit_2"})
        assert len(trade_rows) == 1
        assert trade_rows[0]["stop_loss"] == 99.0
        assert trade_rows[0]["take_profit_1"] == 105.0


# ---------------------------------------------------------------------------
# 2026-05-15: exchange-first close ordering (no phantom DB closes)
# ---------------------------------------------------------------------------
#
# Pin the close branch of ``_apply_update`` so it:
#   (1) writes the DB only when the exchange call returns ok=True,
#   (2) leaves every row UNCHANGED when the exchange returns ok=False,
#   (3) treats the ``skipped: "dry_run"`` short-circuit as a success
#       (so paper accounts still book the DB close as before).
#
# Pre-fix, the DB rows were flipped first and a fabricated PnL was
# stamped before the exchange call. On a Bybit 170131 / network blip
# / rate-limit the journal would lie about a still-open position and
# the reverse-reconciler would adopt it as a duplicate
# ``adopted_orphan`` row. These three tests anchor that fix.


def _read_pkg(db, pkg_id):
    """Find an order_package by id regardless of current status.

    ``get_order_packages_by_strategy`` filters on status, so a row
    that may have flipped to 'closed' has to be looked up by scanning
    both buckets — the public API doesn't expose a "get by id"
    helper in every Database build.
    """
    for status in ("open", "closed"):
        try:
            rows = db.get_order_packages_by_strategy("vwap", status=status)
        except TypeError:
            rows = db.get_order_packages_by_strategy("vwap")
        for r in rows or []:
            if r.get("order_package_id") == pkg_id:
                return r
    return None


def _read_trade(db, trade_id):
    rows = db.get_trades(filters={"id": int(trade_id)})
    return rows[0] if rows else None


def test_close_writes_db_when_exchange_succeeds(tmp_db, caplog):
    """When the exchange close returns ok=True the DB rows flip to
    closed, exit_price is stamped, and the summary counter increments
    — exactly the existing happy-path contract, just gated on a real
    exchange ack.

    2026-05-18 SSOT PnL refactor: the close path no longer computes
    gross PnL locally; ``pnl`` stays NULL until
    ``_sweep_pending_pnl_from_bybit`` fills it from Bybit's closed-pnl
    record. See order_monitor.py:662-674 + the matching update on the
    PR3 close tests."""
    _seed(tmp_db)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    open_trade = tmp_db.get_trades(
        filters={"strategy_name": "vwap", "symbol": "BTCUSDT", "status": "open"},
    )[0]
    trade_id = open_trade["id"]

    summary = om._StrategyTickSummary()
    verdict = {"action": "close", "reason": "vwap_cross", "exit_price": 81209.0}

    with patch.object(
        om, "_send_close_to_exchange",
        return_value={
            "ok": True, "exchange_response": {"retCode": 0},
            "exchange_order_id": "fake-orderid", "error": None,
        },
    ):
        om._apply_update(tmp_db, dict(open_pkg), verdict, summary)

    pkg_after = _read_pkg(tmp_db, open_pkg["order_package_id"])
    trade_after = _read_trade(tmp_db, trade_id)

    assert pkg_after is not None and pkg_after.get("status") == "closed"
    assert pkg_after.get("close_reason") == "vwap_cross"
    assert trade_after is not None and trade_after.get("status") == "closed"
    assert float(trade_after.get("exit_price")) == 81209.0
    # SSOT PnL refactor: pnl stays NULL on close; the Bybit sweep fills it.
    assert trade_after.get("pnl") is None
    assert summary.closed_count == 1
    assert summary.error_count == 0


def test_close_leaves_db_open_when_exchange_fails(tmp_db, caplog):
    """When the exchange close returns ok=False NOTHING in the DB
    changes — no phantom 'closed' row, no fabricated PnL — and the
    error is logged at ERROR with the package id + Bybit error string.
    The next monitor tick will re-attempt; the strategy-monocle gate
    keeps duplicate signals suppressed in the meantime."""
    _seed(tmp_db)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    open_trade = tmp_db.get_trades(
        filters={"strategy_name": "vwap", "symbol": "BTCUSDT", "status": "open"},
    )[0]
    trade_id = open_trade["id"]

    summary = om._StrategyTickSummary()
    verdict = {"action": "close", "reason": "vwap_cross", "exit_price": 81209.0}

    err = "Insufficient balance. (ErrCode: 170131)"
    with caplog.at_level(logging.ERROR, logger="src.runtime.order_monitor"), \
            patch.object(
                om, "_send_close_to_exchange",
                return_value={
                    "ok": False, "exchange_response": None,
                    "exchange_order_id": None, "error": err,
                },
            ):
        om._apply_update(tmp_db, dict(open_pkg), verdict, summary)

    pkg_after = _read_pkg(tmp_db, open_pkg["order_package_id"])
    trade_after = _read_trade(tmp_db, trade_id)

    # Package and trade rows are STILL open.
    assert pkg_after is not None and pkg_after.get("status") == "open"
    assert trade_after is not None and trade_after.get("status") == "open"
    assert trade_after.get("exit_price") is None
    assert trade_after.get("pnl") is None
    # Summary reflects the failure, not a close.
    assert summary.closed_count == 0
    assert summary.error_count == 1
    # ERROR log carries both the package id and the Bybit error string.
    pkg_id = open_pkg["order_package_id"]
    error_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    ]
    assert any(pkg_id in m and "170131" in m for m in error_messages), (
        f"expected an ERROR log mentioning {pkg_id!r} and '170131', "
        f"got: {error_messages!r}"
    )


def test_close_writes_db_when_account_is_dry_run(tmp_db):
    """A dry-run account short-circuits inside
    ``_send_close_to_exchange`` with ``{ok: True, skipped: 'dry_run',
    ...}`` — the caller treats that exactly like a live success and
    proceeds with the DB close. This pins the contract that paper
    accounts still get journaled closes (otherwise the DB would
    forever lag the strategy's view of its own paper book)."""
    _seed(tmp_db)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    open_trade = tmp_db.get_trades(
        filters={"strategy_name": "vwap", "symbol": "BTCUSDT", "status": "open"},
    )[0]
    trade_id = open_trade["id"]

    summary = om._StrategyTickSummary()
    verdict = {"action": "close", "reason": "vwap_cross", "exit_price": 81209.0}

    with patch.object(
        om, "_send_close_to_exchange",
        return_value={
            "ok": True, "skipped": "dry_run",
            "exchange_response": None, "exchange_order_id": None,
            "error": None,
        },
    ):
        om._apply_update(tmp_db, dict(open_pkg), verdict, summary)

    pkg_after = _read_pkg(tmp_db, open_pkg["order_package_id"])
    trade_after = _read_trade(tmp_db, trade_id)

    assert pkg_after is not None and pkg_after.get("status") == "closed"
    assert trade_after is not None and trade_after.get("status") == "closed"
    assert float(trade_after.get("exit_price")) == 81209.0
    # SSOT PnL refactor: pnl stays NULL on close; the Bybit sweep fills it.
    assert trade_after.get("pnl") is None
    assert summary.closed_count == 1
    assert summary.error_count == 0


# ---------------------------------------------------------------------------
# FU-20260515-002 Gap A — fill-price capture (full close)
# ---------------------------------------------------------------------------
#
# PR #1190 fixed the phantom-DB-close cascade, but the journal still
# wrote ``exit_price = verdict.exit_price`` — the monitor's projected
# close price — instead of the actual Bybit fill price. These three
# tests pin the FU-20260515-002 wiring that captures the real avg_price
# via ``account_order_status`` after a successful close, with graceful
# fallback to the verdict-derived value (and a notes annotation) when
# the lookup is unavailable.


def test_full_close_uses_account_order_status_avg_price_when_available(tmp_db):
    """Happy path: the exchange returns ok=True, the post-close
    ``account_order_status`` lookup returns a non-zero ``avg_price``,
    and the trade row records THAT — not the verdict's projection."""
    _seed(tmp_db)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    open_trade = tmp_db.get_trades(
        filters={"strategy_name": "vwap", "symbol": "BTCUSDT", "status": "open"},
    )[0]
    trade_id = open_trade["id"]

    summary = om._StrategyTickSummary()
    verdict = {"action": "close", "reason": "vwap_cross", "exit_price": 81209.0}

    with patch.object(
        om, "_send_close_to_exchange",
        return_value={
            "ok": True, "exchange_response": {"retCode": 0},
            "exchange_order_id": "fake-orderid", "error": None,
        },
    ), patch.object(
        om, "_capture_fill_details",
        return_value={"avg_price": 81210.5, "filled_qty": 0.001},
    ):
        om._apply_update(tmp_db, dict(open_pkg), verdict, summary)

    trade_after = _read_trade(tmp_db, trade_id)

    assert trade_after.get("status") == "closed"
    assert float(trade_after.get("exit_price")) == 81210.5  # exchange avg, not verdict
    # SSOT PnL refactor: pnl stays NULL on close; the Bybit sweep fills it.
    assert trade_after.get("pnl") is None
    # No "exit_price_source: verdict" annotation when exchange-confirmed.
    notes = json.loads(trade_after.get("notes") or "{}")
    assert notes.get("exit_price_source") != "verdict"
    assert summary.closed_count == 1
    assert summary.error_count == 0


def test_full_close_falls_back_to_verdict_when_avg_price_unavailable(tmp_db):
    """``_capture_fill_details`` returns ``None`` when
    ``account_order_status`` reports ``status="not_found"`` /
    ``avg_price=0`` even after the single retry — caller falls back
    to ``verdict.exit_price`` and stamps ``exit_price_source: "verdict"``
    on the trade row's notes so consumers can filter out projected
    exit_prices downstream."""
    _seed(tmp_db)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    open_trade = tmp_db.get_trades(
        filters={"strategy_name": "vwap", "symbol": "BTCUSDT", "status": "open"},
    )[0]
    trade_id = open_trade["id"]

    summary = om._StrategyTickSummary()
    verdict = {"action": "close", "reason": "vwap_cross", "exit_price": 81209.0}

    with patch.object(
        om, "_send_close_to_exchange",
        return_value={
            "ok": True, "exchange_response": {"retCode": 0},
            "exchange_order_id": "fake-orderid", "error": None,
        },
    ), patch(
        "src.units.accounts.clients.account_order_status",
        return_value={"status": "not_found", "avg_price": 0.0,
                      "filled_qty": 0.0, "order_id": "fake-orderid",
                      "exec_time": None},
    ):
        om._apply_update(tmp_db, dict(open_pkg), verdict, summary)

    trade_after = _read_trade(tmp_db, trade_id)
    assert trade_after.get("status") == "closed"
    assert float(trade_after.get("exit_price")) == 81209.0  # fallback to verdict
    notes = json.loads(trade_after.get("notes") or "{}")
    assert notes.get("exit_price_source") == "verdict"
    assert summary.closed_count == 1


def test_full_close_falls_back_when_order_status_returns_none(tmp_db):
    """``account_order_status`` returns ``None`` on a read failure
    (creds missing, network, etc.). ``_capture_fill_details`` short-
    circuits the same way as the not_found path — the caller uses
    ``verdict.exit_price`` and stamps the notes annotation."""
    _seed(tmp_db)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    open_trade = tmp_db.get_trades(
        filters={"strategy_name": "vwap", "symbol": "BTCUSDT", "status": "open"},
    )[0]
    trade_id = open_trade["id"]

    summary = om._StrategyTickSummary()
    verdict = {"action": "close", "reason": "vwap_cross", "exit_price": 81209.0}

    with patch.object(
        om, "_send_close_to_exchange",
        return_value={
            "ok": True, "exchange_response": {"retCode": 0},
            "exchange_order_id": "fake-orderid", "error": None,
        },
    ), patch(
        "src.units.accounts.clients.account_order_status",
        return_value=None,
    ):
        om._apply_update(tmp_db, dict(open_pkg), verdict, summary)

    trade_after = _read_trade(tmp_db, trade_id)
    assert trade_after.get("status") == "closed"
    assert float(trade_after.get("exit_price")) == 81209.0
    notes = json.loads(trade_after.get("notes") or "{}")
    assert notes.get("exit_price_source") == "verdict"
    assert summary.closed_count == 1


# ---------------------------------------------------------------------------
# FU-20260515-002 Gap B — partial-close exchange wiring
# ---------------------------------------------------------------------------
#
# Pre-FU-20260515-002, ``_apply_partial_close`` was DB-only by design:
# a partial-close verdict (e.g. turtle_soup TP1 partial_close_pct=0.25)
# would mark the trade row down in size while the live Bybit position
# stayed at the original size. The exchange-side SL/TP would eventually
# fire against the full original size. These three tests pin the
# refactored exchange-first behaviour: the partial close hits Bybit
# FIRST and the DB only follows on ok=True (mirror of the PR #1190
# full-close fix).


def _seed_with_link(db, *, pkg_id="pkg-1", strategy="vwap", direction="long",
                    symbol="BTCUSDT", position_size=0.001):
    """Seed an open package + a linked trade row, and patch the
    package's ``linked_trade_id`` to point at the trade row's id.

    ``_apply_partial_close`` refuses to act without a numeric
    ``linked_trade_id`` (the symbol/strategy fallback is unsafe for
    partials — the wrong-row update would silently shrink an
    unrelated position). The default ``_seed`` doesn't wire the link,
    so partial-close tests need this fuller fixture.
    """
    db.insert_order_package({
        "order_package_id": pkg_id, "strategy_name": strategy,
        "symbol": symbol, "direction": direction,
        "entry": 100.0, "sl": 98.0, "tp": 104.0, "confidence": 0.6,
    })
    trade_id = db.insert_trade({
        "timestamp": "2026-05-02T20:00:00+00:00",
        "symbol": symbol, "direction": direction,
        "entry_price": 100.0, "stop_loss": 98.0, "take_profit_1": 104.0,
        "position_size": position_size, "status": "open", "is_backtest": 0,
        "strategy_name": strategy, "account_id": "bybit_2",
        "setup_type": strategy,
    })
    db.update_order_package(pkg_id, {"linked_trade_id": int(trade_id)})
    return trade_id


def test_partial_close_dispatches_to_exchange_with_reduce_only(tmp_db):
    """Happy path: the partial close hits the exchange (close_open_position
    via ``_send_partial_close_to_exchange``), the post-close fill lookup
    returns the actual filled qty, and the trade row's position_size is
    decremented by the EXCHANGE-reported qty — not by the verdict's
    requested fraction. ``close_open_position`` always sets
    reduceOnly=True, so the partial dispatch can never accidentally
    open a fresh position even when the exchange rounds the qty."""
    trade_id = _seed_with_link(tmp_db, position_size=0.004)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    pkg_dict = dict(open_pkg)
    pkg_dict["linked_trade_id"] = int(trade_id)

    summary = om._StrategyTickSummary()
    verdict = {
        "action": "close", "close_qty_pct": 0.25,
        "reason": "tp1_partial", "exit_price": 102.0,
    }

    with patch.object(
        om, "_send_partial_close_to_exchange",
        return_value={
            "ok": True, "exchange_response": {"retCode": 0},
            "exchange_order_id": "partial-orderid", "error": None,
        },
    ) as mock_partial, patch.object(
        om, "_capture_fill_details",
        return_value={"avg_price": 102.1, "filled_qty": 0.001},
    ):
        om._apply_partial_close(tmp_db, pkg_dict, verdict, summary)

    # Exchange called with the verdict-requested qty (0.004 * 0.25).
    args, kwargs = mock_partial.call_args
    assert args[0]["id"] == trade_id
    assert abs(args[1] - 0.001) < 1e-8  # 0.004 * 0.25

    trade_after = _read_trade(tmp_db, trade_id)
    # position_size decremented by ACTUAL filled qty (0.001), not by
    # the verdict-requested 0.001 (they happen to match here — what
    # the test really pins is that we used filled_qty, not requested).
    assert abs(float(trade_after["position_size"]) - 0.003) < 1e-8
    notes = json.loads(trade_after.get("notes") or "{}")
    partials = notes.get("partial_closes", [])
    assert len(partials) == 1
    frag = partials[0]
    assert frag["filled_qty"] == 0.001
    assert frag["exit_price"] == 102.1
    assert frag["exit_price_source"] == "exchange"
    assert frag["exchange_order_id"] == "partial-orderid"
    assert summary.updated_count == 1
    assert summary.error_count == 0


def test_partial_close_leaves_db_unchanged_when_exchange_fails(tmp_db, caplog):
    """Exchange refuses (170131, network, rate-limit). The DB row is
    untouched — position_size stays at the original, notes are not
    rewritten, no partial_closes fragment is appended — and the
    failure is logged at ERROR with the pkg_id + Bybit error string.
    The next monitor tick will re-attempt; strategy_monocle continues
    to suppress duplicate signals."""
    trade_id = _seed_with_link(tmp_db, position_size=0.004)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    pkg_dict = dict(open_pkg)
    pkg_dict["linked_trade_id"] = int(trade_id)

    summary = om._StrategyTickSummary()
    verdict = {
        "action": "close", "close_qty_pct": 0.25,
        "reason": "tp1_partial", "exit_price": 102.0,
    }

    err = "Insufficient balance. (ErrCode: 170131)"
    with caplog.at_level(logging.ERROR, logger="src.runtime.order_monitor"), \
            patch.object(
                om, "_send_partial_close_to_exchange",
                return_value={
                    "ok": False, "exchange_response": None,
                    "exchange_order_id": None, "error": err,
                },
            ):
        om._apply_partial_close(tmp_db, pkg_dict, verdict, summary)

    trade_after = _read_trade(tmp_db, trade_id)
    # position_size unchanged.
    assert abs(float(trade_after["position_size"]) - 0.004) < 1e-8
    # notes carry no partial_closes fragment.
    notes = json.loads(trade_after.get("notes") or "{}")
    assert not notes.get("partial_closes")
    # Summary reflects the failure.
    assert summary.updated_count == 0
    assert summary.error_count == 1
    pkg_id = open_pkg["order_package_id"]
    error_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    ]
    assert any(pkg_id in m and "170131" in m for m in error_messages), (
        f"expected an ERROR log mentioning {pkg_id!r} and '170131', "
        f"got: {error_messages!r}"
    )


def test_partial_close_dry_run_short_circuit(tmp_db):
    """``_send_partial_close_to_exchange`` short-circuits to
    ``{ok: True, skipped: 'dry_run', exchange_order_id: None}`` on
    dry-run accounts. The caller treats that exactly like a live ack
    and writes the DB partial using the verdict-requested qty
    (no exchange to look up an avg_price from). The fragment's
    ``exit_price_source`` is ``"verdict"`` since we never got an
    exchange-confirmed fill price."""
    trade_id = _seed_with_link(tmp_db, position_size=0.004)
    open_pkg = tmp_db.get_order_packages_by_strategy("vwap", status="open")[0]
    pkg_dict = dict(open_pkg)
    pkg_dict["linked_trade_id"] = int(trade_id)

    summary = om._StrategyTickSummary()
    verdict = {
        "action": "close", "close_qty_pct": 0.25,
        "reason": "tp1_partial", "exit_price": 102.0,
    }

    with patch.object(
        om, "_send_partial_close_to_exchange",
        return_value={
            "ok": True, "skipped": "dry_run",
            "exchange_response": None, "exchange_order_id": None,
            "error": None,
        },
    ):
        om._apply_partial_close(tmp_db, pkg_dict, verdict, summary)

    trade_after = _read_trade(tmp_db, trade_id)
    # Decremented by the verdict-requested qty (0.004 * 0.25 = 0.001).
    assert abs(float(trade_after["position_size"]) - 0.003) < 1e-8
    notes = json.loads(trade_after.get("notes") or "{}")
    partials = notes.get("partial_closes", [])
    assert len(partials) == 1
    frag = partials[0]
    assert frag["qty"] == 0.25
    assert frag["filled_qty"] == 0.001  # verdict-requested
    assert frag["exit_price"] == 102.0  # verdict
    assert frag["exit_price_source"] == "verdict"
    assert summary.updated_count == 1
    assert summary.error_count == 0
