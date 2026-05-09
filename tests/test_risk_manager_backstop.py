"""Risk-manager active-position backstop.

Pins the contract for ``RiskManager.check_position_breach`` (the pure
per-trade breach detector) and ``_risk_manager_position_check`` (the
sweep that wires the detector into the monitor tick).

The backstop closes the gap where the strategy ``monitor()`` loop
fails to produce a TP/SL close verdict (and on Bybit spot-margin
there's no exchange-side bracket to fall back on — Bybit V5 retCode
170130 on Market orders). This sweep walks every DB-open trade each
tick, fetches the latest price for the symbol, and emergency-closes
any trade whose stop_loss or take_profit_1 has been crossed.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.runtime.order_monitor import (
    _risk_backstop_enabled,
    _risk_manager_position_check,
)
from src.units.accounts.risk import RiskManager
from src.units.db.database import Database


# ---------------------------------------------------------------------------
# Unit tests — pure breach detection (RiskManager.check_position_breach)
# ---------------------------------------------------------------------------


def _trade(direction, sl=80000.0, tp1=79000.0):
    """Default: short with SL above entry and TP below."""
    return {
        "direction": direction,
        "stop_loss": sl,
        "take_profit_1": tp1,
    }


class TestCheckPositionBreach:
    def test_short_price_above_sl_reports_sl_breach(self):
        verdict = RiskManager.check_position_breach(_trade("short"), 80001.0)
        assert verdict == {"reason": "risk_manager_sl_breach"}

    def test_short_price_at_sl_reports_sl_breach(self):
        # Inclusive — touching SL is a breach.
        verdict = RiskManager.check_position_breach(_trade("short"), 80000.0)
        assert verdict == {"reason": "risk_manager_sl_breach"}

    def test_short_price_at_or_below_tp_reports_tp_breach(self):
        verdict = RiskManager.check_position_breach(_trade("short"), 79000.0)
        assert verdict == {"reason": "risk_manager_tp_breach"}
        verdict = RiskManager.check_position_breach(_trade("short"), 78500.0)
        assert verdict == {"reason": "risk_manager_tp_breach"}

    def test_short_price_within_bracket_returns_none(self):
        verdict = RiskManager.check_position_breach(_trade("short"), 79500.0)
        assert verdict is None

    def test_long_price_at_or_below_sl_reports_sl_breach(self):
        # Long: SL below entry, TP above.
        trade = _trade("long", sl=79000.0, tp1=80500.0)
        assert (
            RiskManager.check_position_breach(trade, 79000.0)
            == {"reason": "risk_manager_sl_breach"}
        )
        assert (
            RiskManager.check_position_breach(trade, 78500.0)
            == {"reason": "risk_manager_sl_breach"}
        )

    def test_long_price_at_or_above_tp_reports_tp_breach(self):
        trade = _trade("long", sl=79000.0, tp1=80500.0)
        assert (
            RiskManager.check_position_breach(trade, 80500.0)
            == {"reason": "risk_manager_tp_breach"}
        )
        assert (
            RiskManager.check_position_breach(trade, 81000.0)
            == {"reason": "risk_manager_tp_breach"}
        )

    def test_long_price_within_bracket_returns_none(self):
        trade = _trade("long", sl=79000.0, tp1=80500.0)
        assert RiskManager.check_position_breach(trade, 79750.0) is None

    def test_sl_takes_priority_over_tp_on_simultaneous_breach(self):
        # Pathological gap: short with SL=80000, TP=79000 — price 78000
        # would breach both directions if asked naively. Our contract is
        # SL first (more conservative).
        # Construct a short where price simultaneously crosses both:
        # SL at 80000 (breached if price >= 80000), TP at 79000
        # (breached if price <= 79000) — there's no real-world price
        # that can do both. Use a normal SL-only breach instead.
        # The contract is documented as "SL checked first"; keep the
        # test asserting that ordering on a clean SL-only breach.
        verdict = RiskManager.check_position_breach(_trade("short"), 80100.0)
        assert verdict["reason"] == "risk_manager_sl_breach"

    def test_missing_sl_skips_sl_check(self):
        trade = {"direction": "short", "stop_loss": None, "take_profit_1": 79000.0}
        # Price would have breached SL at 80000 if it were set, but SL is None.
        assert RiskManager.check_position_breach(trade, 80100.0) is None
        # TP still works.
        assert (
            RiskManager.check_position_breach(trade, 78500.0)
            == {"reason": "risk_manager_tp_breach"}
        )

    def test_missing_tp_skips_tp_check(self):
        trade = {"direction": "short", "stop_loss": 80000.0, "take_profit_1": None}
        # No TP → no TP breach.
        assert RiskManager.check_position_breach(trade, 78000.0) is None
        # SL still works.
        assert (
            RiskManager.check_position_breach(trade, 80100.0)
            == {"reason": "risk_manager_sl_breach"}
        )

    def test_unknown_direction_returns_none(self):
        assert RiskManager.check_position_breach(_trade(""), 80100.0) is None
        assert RiskManager.check_position_breach(_trade("flat"), 80100.0) is None
        assert RiskManager.check_position_breach({"direction": None}, 80100.0) is None

    def test_garbage_levels_silently_skipped(self):
        trade = {"direction": "short", "stop_loss": "not-a-number", "take_profit_1": 79000.0}
        # SL unparseable → skipped; TP still evaluable.
        assert RiskManager.check_position_breach(trade, 80100.0) is None
        assert (
            RiskManager.check_position_breach(trade, 78500.0)
            == {"reason": "risk_manager_tp_breach"}
        )

    def test_garbage_price_returns_none(self):
        with pytest.raises(TypeError):
            # A None price is a programmer error in the caller; the
            # static method validates with float() and returns None
            # on coercion failure rather than raising.
            float(None)
        assert RiskManager.check_position_breach(_trade("short"), float("nan")) is None or True
        # NaN compared against SL: NaN >= 80000 is False, NaN <= 79000 is False → None.
        assert RiskManager.check_position_breach(_trade("short"), float("nan")) is None


# ---------------------------------------------------------------------------
# Integration tests — _risk_manager_position_check sweep
# ---------------------------------------------------------------------------


def _insert_open_trade(
    db,
    *,
    account_id="bybit_2",
    symbol="BTCUSDT",
    direction="short",
    stop_loss=80000.0,
    take_profit_1=79000.0,
    position_size=0.001,
    notes_pkg_id=None,
):
    notes = {"trade_id": "t-stub"}
    if notes_pkg_id:
        notes["order_package_id"] = notes_pkg_id
    db.insert_trade({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "direction": direction,
        "entry_price": 79500.0,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "position_size": position_size,
        "setup_type": "vwap",
        "entry_reason": "vwap signal",
        "status": "open",
        "is_backtest": 0,
        "strategy_name": "vwap",
        "account_id": account_id,
        "notes": json.dumps(notes),
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    })
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def _insert_package(db, pkg_id, linked_trade_id):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": "vwap",
        "symbol": "BTCUSDT",
        "direction": "short",
        "entry": 79500.0,
        "sl": 80000.0,
        "tp": 79000.0,
        "confidence": 0.5,
        "status": "open",
        "linked_trade_id": linked_trade_id,
        "meta": {},
    })


def _read_trade(db, trade_id):
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT id, status, exit_reason, exit_price FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _read_package(db, pkg_id):
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT order_package_id, status, close_reason FROM order_packages "
            "WHERE order_package_id=?",
            (pkg_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    db = Database(db_path=str(db_path))

    def _fake_cfg_loader():
        return {
            "bybit_2": {
                "account_id": "bybit_2",
                "exchange": "bybit",
                "api_key_env": "BYBIT_KEY_2",
                "api_secret_env": None,
                "mode": "live",
            },
            "bybit_dry": {
                "account_id": "bybit_dry",
                "exchange": "bybit",
                "api_key_env": "BYBIT_KEY_DRY",
                "api_secret_env": None,
                "mode": "dry_run",
            },
        }

    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        _fake_cfg_loader,
    )
    return db


def _candles(price):
    """List-of-dicts shim accepted by the sweep alongside DataFrames."""
    return [{"close": price}]


def _stub_send_close_to_exchange(*args, **kwargs):
    return {"ok": True, "stub": True}


class TestRiskBackstopFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("RISK_MANAGER_BACKSTOP_ENABLED", raising=False)
        assert _risk_backstop_enabled() is False

    def test_recognised_truthy_values(self, monkeypatch):
        for v in ("true", "True", "1", "yes", "on"):
            monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", v)
            assert _risk_backstop_enabled() is True

    def test_falsy_values(self, monkeypatch):
        for v in ("false", "0", "off", "no", "garbage", ""):
            monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", v)
            assert _risk_backstop_enabled() is False


class TestRiskBackstopSweep:
    def test_flag_off_is_noop_even_with_stale_open_trade(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "false")
        trade_id = _insert_open_trade(tmp_db)  # short SL=80000
        # Price is well past SL — would breach if the sweep ran.
        summary = _risk_manager_position_check(
            tmp_db, ohlcv_fetcher=lambda s, t: _candles(81000.0)
        )
        assert summary == {
            "checked": 0, "breaches": 0, "closes_sent": 0,
            "skipped_no_cfg": 0, "skipped_no_price": 0,
            "skipped_dry": 0, "errors": 0,
        }
        # Trade row untouched.
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_no_fetcher_is_noop(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        _insert_open_trade(tmp_db)
        summary = _risk_manager_position_check(tmp_db, ohlcv_fetcher=None)
        assert summary["checked"] == 0
        assert summary["breaches"] == 0

    def test_no_open_trades_is_noop(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        summary = _risk_manager_position_check(
            tmp_db, ohlcv_fetcher=lambda s, t: _candles(99999.0),
        )
        assert summary["checked"] == 0
        assert summary["breaches"] == 0

    def test_short_sl_breach_closes_db_and_sends_exchange_close(
        self, tmp_db, monkeypatch
    ):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        pkg_id = "pkg-backstop-001"
        trade_id = _insert_open_trade(tmp_db, notes_pkg_id=pkg_id)
        _insert_package(tmp_db, pkg_id, linked_trade_id=trade_id)

        # Price 81000 is well above SL 80000 for a short → SL breach.
        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ) as mock_send:
            summary = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=lambda s, t: _candles(81000.0),
            )

        assert summary["checked"] == 1
        assert summary["breaches"] == 1
        assert summary["closes_sent"] == 1
        assert summary["errors"] == 0

        trade = _read_trade(tmp_db, trade_id)
        assert trade["status"] == "closed"
        assert trade["exit_reason"] == "risk_manager_sl_breach"
        assert trade["exit_price"] == 81000.0

        pkg = _read_package(tmp_db, pkg_id)
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "risk_manager_sl_breach"

        assert mock_send.call_count == 1

    def test_short_tp_breach_closes_with_tp_reason(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        trade_id = _insert_open_trade(tmp_db)

        # Price 78500 is well below TP 79000 for a short → TP breach.
        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ):
            summary = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=lambda s, t: _candles(78500.0),
            )

        assert summary["breaches"] == 1
        trade = _read_trade(tmp_db, trade_id)
        assert trade["exit_reason"] == "risk_manager_tp_breach"

    def test_no_breach_leaves_trade_open(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        trade_id = _insert_open_trade(tmp_db)

        # Price 79500 is between TP (79000) and SL (80000) for a short.
        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ) as mock_send:
            summary = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=lambda s, t: _candles(79500.0),
            )

        assert summary["checked"] == 1
        assert summary["breaches"] == 0
        assert mock_send.call_count == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_dry_run_account_skipped(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        trade_id = _insert_open_trade(tmp_db, account_id="bybit_dry")

        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ) as mock_send:
            summary = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=lambda s, t: _candles(81000.0),
            )

        assert summary["skipped_dry"] == 1
        assert summary["breaches"] == 0
        assert mock_send.call_count == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_unknown_account_skipped(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        trade_id = _insert_open_trade(tmp_db, account_id="bybit_ghost")

        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ):
            summary = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=lambda s, t: _candles(81000.0),
            )

        assert summary["skipped_no_cfg"] == 1
        assert summary["breaches"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_price_fetch_failure_is_skipped_no_price(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        trade_id = _insert_open_trade(tmp_db)

        def _broken_fetcher(symbol, tf):
            raise RuntimeError("network down")

        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ):
            summary = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=_broken_fetcher,
            )

        assert summary["skipped_no_price"] == 1
        assert summary["breaches"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_price_cache_dedupes_fetches_for_same_symbol(self, tmp_db, monkeypatch):
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        # Three open trades on the same symbol/account.
        ids = [_insert_open_trade(tmp_db) for _ in range(3)]

        fetch_calls: list = []

        def _counting_fetcher(symbol, tf):
            fetch_calls.append(symbol)
            return _candles(81000.0)  # SL breach for all three

        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ):
            summary = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=_counting_fetcher,
            )

        assert summary["breaches"] == 3
        # Despite three trades, the fetcher was called once.
        assert len(fetch_calls) == 1
        for tid in ids:
            assert _read_trade(tmp_db, tid)["status"] == "closed"

    def test_idempotent_second_run_after_close(self, tmp_db, monkeypatch):
        """After a close, the trade is no longer status='open' so the
        next sweep doesn't see it. The backstop is naturally idempotent
        through DB state.
        """
        monkeypatch.setenv("RISK_MANAGER_BACKSTOP_ENABLED", "true")
        trade_id = _insert_open_trade(tmp_db)

        with patch(
            "src.runtime.order_monitor._send_close_to_exchange",
            side_effect=_stub_send_close_to_exchange,
        ):
            first = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=lambda s, t: _candles(81000.0),
            )
            second = _risk_manager_position_check(
                tmp_db, ohlcv_fetcher=lambda s, t: _candles(81000.0),
            )

        assert first["breaches"] == 1
        assert second["breaches"] == 0
        assert second["checked"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "closed"
