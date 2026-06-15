"""S-030 PR3 regression tests — order-package monitor loop
(architecture-audit-2026-05-02 P1-4).

Covers:
  1. ``Database.update_trade`` — close-side writer.
  2. ``run_monitor_tick`` end-to-end across stubbed strategies and
     stubbed candles, exercising the four verdict shapes
     (None / sl / tp / close).
  3. Best-effort guarantees — bad data, missing strategy module,
     monitor() raising, DB write failing.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.data_layer.database import Database
from src.runtime import order_monitor as om


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return Database(db_path=str(db_path))


def _seed_open_pkg(db, *, pkg_id, strategy="vwap", direction="long",
                   sl=98.0, tp=104.0, entry=100.0, symbol="BTCUSDT"):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": strategy,
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": 0.6,
        "meta": {"strategy_name": strategy, "killzone": "asia"},
    })


def _seed_open_trade(db, *, strategy="vwap", symbol="BTCUSDT"):
    return db.insert_trade({
        "timestamp": "2026-05-02T20:00:00+00:00",
        "symbol": symbol,
        "direction": "long",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "take_profit_1": 104.0,
        "position_size": 0.001,
        "setup_type": strategy,
        "entry_reason": f"{strategy} signal",
        "status": "open",
        "is_backtest": 0,
        "strategy_name": strategy,
        "account_id": "bybit_2",
    })


def _candles(close_price):
    return pd.DataFrame({
        "open": [close_price],
        "high": [close_price * 1.001],
        "low": [close_price * 0.999],
        "close": [close_price],
        "volume": [100.0],
    })


# ---------------------------------------------------------------------------
# Database.update_trade — close-side writer
# ---------------------------------------------------------------------------


class TestUpdateTrade:
    def test_update_modifies_status_and_exit_fields(self, tmp_db):
        trade_id = _seed_open_trade(tmp_db)
        affected = tmp_db.update_trade(trade_id, {
            "status": "closed",
            "exit_price": 102.0,
            "exit_reason": "monitor_close: tp_partial",
            "pnl": 2.0,
        })
        assert affected == 1

        rows = tmp_db.get_trades(filters={"id": trade_id})
        row = rows[0]
        assert row["status"] == "closed"
        assert row["exit_price"] == 102.0
        assert row["exit_reason"] == "monitor_close: tp_partial"
        assert row["pnl"] == 2.0

    def test_update_unknown_id_returns_zero(self, tmp_db):
        affected = tmp_db.update_trade(99999, {"status": "closed"})
        assert affected == 0

    def test_update_requires_id(self, tmp_db):
        with pytest.raises(ValueError):
            tmp_db.update_trade(None, {"status": "closed"})

    def test_update_empty_returns_zero(self, tmp_db):
        trade_id = _seed_open_trade(tmp_db)
        affected = tmp_db.update_trade(trade_id, {})
        assert affected == 0


# ---------------------------------------------------------------------------
# run_monitor_tick — end-to-end across stubbed strategies
# ---------------------------------------------------------------------------


class TestRunMonitorTick:
    def test_no_open_packages_returns_empty_summaries(self, tmp_db):
        # No seeded packages. Loop should run and return empty
        # per-strategy summaries.
        summaries = om.run_monitor_tick(strategies=["vwap"])
        assert summaries == {"vwap": {
            "open": 0, "updated": 0, "closed": 0, "no_change": 0,
            "errors": 0, "error_messages": [],
        }}

    def test_no_change_path(self, tmp_db):
        _seed_open_pkg(tmp_db, pkg_id="pkg-noop")

        # Stub vwap.monitor to return None.
        with patch("src.units.strategies.vwap.monitor", return_value=None):
            summaries = om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(100.5),
            )

        s = summaries["vwap"]
        assert s["open"] == 1
        assert s["no_change"] == 1
        assert s["updated"] == 0
        assert s["closed"] == 0
        # Row stays open.
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "open"

    def test_sl_update_path_writes_back(self, tmp_db):
        # 2026-05-18 exchange-first modify ordering: the sl/tp modify
        # path now requires a matched trade row + a successful
        # _send_modify_to_exchange before it touches the DB. Seed the
        # linked trade and stub the exchange call to ok=True so this
        # test still exercises the DB write-back it was written for.
        _seed_open_pkg(tmp_db, pkg_id="pkg-sl")
        _seed_open_trade(tmp_db)

        with patch(
            "src.units.strategies.vwap.monitor", return_value={"sl": 100.0},
        ), patch.object(
            om, "_send_modify_to_exchange", return_value={"ok": True},
        ):
            summaries = om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(102.0),
            )

        s = summaries["vwap"]
        assert s["updated"] == 1
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["sl"] == 100.0
        assert rows[0]["status"] == "open"  # still open, SL just moved

    def test_tp_update_path_writes_back(self, tmp_db):
        # Exchange-first modify ordering (see test_sl_update_path_writes_back).
        _seed_open_pkg(tmp_db, pkg_id="pkg-tp")
        _seed_open_trade(tmp_db)

        with patch(
            "src.units.strategies.vwap.monitor", return_value={"tp": 110.0},
        ), patch.object(
            om, "_send_modify_to_exchange", return_value={"ok": True},
        ):
            summaries = om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(105.0),
            )

        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["tp"] == 110.0
        assert summaries["vwap"]["updated"] == 1

    def test_close_path_closes_pkg_and_linked_trade(self, tmp_db):
        _seed_open_pkg(tmp_db, pkg_id="pkg-close")
        trade_id = _seed_open_trade(tmp_db)  # same strategy/symbol

        # 2026-05-15 exchange-first close ordering: the close path now
        # dispatches to the exchange before any DB write and leaves the
        # DB open on ok=False. Stub _send_close_to_exchange to ok=True
        # so this test exercises the DB close logic it pins.
        with patch(
            "src.units.strategies.vwap.monitor",
            return_value={"action": "close", "reason": "vwap_cross",
                          "exit_price": 99.5},
        ), patch.object(
            om, "_send_close_to_exchange",
            return_value={"ok": True, "exchange_order_id": None, "error": None},
        ):
            summaries = om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(99.5),
            )

        assert summaries["vwap"]["closed"] == 1

        # order_packages row closed.
        pkg = tmp_db.get_order_packages_by_strategy("vwap")[0]
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "vwap_cross"

        # trades row closed via the fallback symbol/strategy match
        # (linked_trade_id is None until a future PR wires it).
        trades = tmp_db.get_trades(filters={"id": trade_id})
        assert trades[0]["status"] == "closed"
        assert trades[0]["exit_reason"] == "vwap_cross"
        assert trades[0]["exit_price"] == 99.5

    def test_close_path_books_pnl_and_pnl_percent(self, tmp_db):
        """Regression for the 2026-05-10 layer-2 finding: 38 closed trades
        had status='closed' + exit_price set but pnl=NULL because the
        monitor close path only wrote status / exit_reason / exit_price.
        """
        _seed_open_pkg(tmp_db, pkg_id="pkg-pnl", entry=100.0, sl=95.0, tp=110.0)
        trade_id = tmp_db.insert_trade({
            "timestamp": "2026-05-10T01:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "take_profit_1": 110.0,
            "position_size": 2.0,
            "setup_type": "vwap",
            "entry_reason": "vwap signal",
            "status": "open",
            "is_backtest": 0,
            "strategy_name": "vwap",
            "account_id": "bybit_2",
        })

        with patch(
            "src.units.strategies.vwap.monitor",
            return_value={"action": "close", "reason": "tp_cross",
                          "exit_price": 110.0},
        ), patch.object(
            om, "_send_close_to_exchange",
            return_value={"ok": True, "exchange_order_id": None, "error": None},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(110.0),
            )

        trade = tmp_db.get_trades(filters={"id": trade_id})[0]
        assert trade["status"] == "closed"
        assert trade["exit_price"] == 110.0
        # 2026-05-18 SSOT PnL refactor: the monitor close path no
        # longer computes gross PnL locally. ``pnl`` stays NULL until
        # ``_sweep_pending_pnl_from_bybit`` fills it from Bybit's
        # closed-pnl record. See order_monitor.py docstring for
        # _compute_close_pnl (now-deleted) and the new sweep.
        assert trade["pnl"] is None
        assert trade["pnl_percent"] is None

    def test_close_path_books_pnl_for_short(self, tmp_db):
        """Short side of the PnL formula — short profits when exit < entry."""
        _seed_open_pkg(
            tmp_db, pkg_id="pkg-pnl-short",
            direction="short", entry=100.0, sl=105.0, tp=90.0,
        )
        trade_id = tmp_db.insert_trade({
            "timestamp": "2026-05-10T01:00:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "short",
            "entry_price": 100.0,
            "stop_loss": 105.0,
            "take_profit_1": 90.0,
            "position_size": 2.0,
            "setup_type": "vwap",
            "entry_reason": "vwap signal",
            "status": "open",
            "is_backtest": 0,
            "strategy_name": "vwap",
            "account_id": "bybit_2",
        })

        with patch(
            "src.units.strategies.vwap.monitor",
            return_value={"action": "close", "reason": "tp_cross",
                          "exit_price": 90.0},
        ), patch.object(
            om, "_send_close_to_exchange",
            return_value={"ok": True, "exchange_order_id": None, "error": None},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(90.0),
            )

        trade = tmp_db.get_trades(filters={"id": trade_id})[0]
        # 2026-05-18 SSOT PnL refactor: pnl stays NULL on close; the
        # Bybit sweep populates it next tick. See companion update on
        # the long-side test above.
        assert trade["pnl"] is None
        assert trade["pnl_percent"] is None
        assert trade["exit_price"] == 90.0

    def test_close_path_skips_pnl_when_exit_price_missing(self, tmp_db):
        """Verdict without exit_price → status flips, pnl stays NULL."""
        _seed_open_pkg(tmp_db, pkg_id="pkg-no-exit-px")
        trade_id = _seed_open_trade(tmp_db)

        with patch(
            "src.units.strategies.vwap.monitor",
            return_value={"action": "close", "reason": "manual_close"},
        ), patch.object(
            om, "_send_close_to_exchange",
            return_value={"ok": True, "exchange_order_id": None, "error": None},
        ):
            om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(100.0),
            )

        trade = tmp_db.get_trades(filters={"id": trade_id})[0]
        assert trade["status"] == "closed"
        assert trade["pnl"] is None
        assert trade["pnl_percent"] is None

    def test_unknown_verdict_shape_is_logged_no_change(self, tmp_db, caplog):
        _seed_open_pkg(tmp_db, pkg_id="pkg-weird")

        with patch(
            "src.units.strategies.vwap.monitor",
            return_value={"unknown_key": 42},
        ):
            summaries = om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(100.0),
            )

        assert summaries["vwap"]["no_change"] == 1
        # Row untouched.
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "open"


class TestRunMonitorTickDefensive:
    def test_strategy_missing_module_skipped(self, tmp_db):
        _seed_open_pkg(tmp_db, pkg_id="pkg-x", strategy="phantom_strategy")
        # Pass it explicitly so _load_strategies returns ["phantom_strategy"].
        summaries = om.run_monitor_tick(strategies=["phantom_strategy"])
        assert summaries["phantom_strategy"]["open"] == 1
        # No exception; package treated as no-change because the
        # importlib lookup fails inside _call_strategy_monitor.
        assert summaries["phantom_strategy"]["no_change"] == 1

    def test_monitor_raises_treated_as_no_change(self, tmp_db):
        _seed_open_pkg(tmp_db, pkg_id="pkg-boom")
        with patch(
            "src.units.strategies.vwap.monitor",
            side_effect=RuntimeError("strategy crashed"),
        ):
            summaries = om.run_monitor_tick(
                strategies=["vwap"],
                ohlcv_fetcher=lambda s, t, sn=None: _candles(100.0),
            )
        assert summaries["vwap"]["no_change"] == 1
        # Row untouched.
        assert tmp_db.get_order_packages_by_strategy("vwap")[0]["status"] == "open"

    def test_ohlcv_fetcher_raises_treated_as_none(self, tmp_db):
        _seed_open_pkg(tmp_db, pkg_id="pkg-fetch-fail")

        # vwap.monitor returns None when candles is None; the loop
        # treats that as no_change.
        def _bad_fetcher(symbol, timeframe, strategy_name=None):
            raise RuntimeError("ohlcv unavailable")

        summaries = om.run_monitor_tick(
            strategies=["vwap"], ohlcv_fetcher=_bad_fetcher,
        )
        assert summaries["vwap"]["no_change"] == 1

    def test_strategy_with_no_monitor_function_is_no_change(self, tmp_db):
        _seed_open_pkg(tmp_db, pkg_id="pkg-x", strategy="smoke_test")
        summaries = om.run_monitor_tick(strategies=["smoke_test"])
        # smoke_test has no monitor() — module loaded fine but no hook.
        assert summaries["smoke_test"]["open"] == 1
        assert summaries["smoke_test"]["no_change"] == 1

    def test_db_unwritable_returns_empty_summary(self, tmp_path, monkeypatch):
        # Point at an unwritable directory.
        monkeypatch.setenv(
            "TRADE_JOURNAL_DB", str(tmp_path / "missing" / "x.db"),
        )
        summaries = om.run_monitor_tick(strategies=["vwap"])
        assert summaries == {}


# ---------------------------------------------------------------------
# _sweep_pending_pnl_from_bybit (2026-05-18 SSOT PnL refactor)
# ---------------------------------------------------------------------
class TestSweepPendingPnlFromBybit:
    """The sister sweep that fills pnl from Bybit for any DB-closed
    row whose pnl is still NULL. Pre-this-PR the monitor close path
    wrote a fee-blind gross PnL; this PR deletes that write and
    replaces it with a periodic Bybit lookup. See order_monitor.py
    docstrings for the architectural directive."""

    def _seed_closed_pending(self, db, *, trade_id_hint=None, **overrides):
        """Insert a status='closed' row with pnl=NULL (the new
        steady-state shape between monitor-close and Bybit-sweep)."""
        row = {
            "timestamp": "2026-05-18T07:30:00+00:00",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": 76700.0,
            "stop_loss": 76600.0,
            "take_profit_1": 77000.0,
            "position_size": 0.004,
            "setup_type": "vwap",
            "entry_reason": "vwap signal",
            "status": "closed",
            "exit_reason": "tp_cross",
            "exit_price": 76977.6,
            "is_backtest": 0,
            "strategy_name": "vwap",
            "account_id": "bybit_2",
            "notes": '{"trade_id": "x"}',
        }
        row.update(overrides)
        return db.insert_trade(row)

    def test_sweep_fills_pnl_from_bybit(self, tmp_db, monkeypatch):
        """Happy path: Bybit returns a record, sweep writes pnl +
        exit_price + notes stamp."""
        trade_id = self._seed_closed_pending(tmp_db)

        # Stub the account-cfg loader so the sweep has a cfg to call
        # Bybit with.
        monkeypatch.setattr(
            om, "_load_account_cfgs_for_reconcile",
            lambda: {"bybit_2": {"id": "bybit_2", "category": "linear"}},
        )

        # Stub Bybit's closed-pnl response — what the real API returns
        # ~30-60s after the close fill.
        def _fake_closed_pnl(
            cfg, *, symbol, direction, opened_at_ms, qty, entry_price=None,
        ):
            assert symbol == "BTCUSDT"
            assert direction == "long"
            return {
                "avg_exit_price": 76977.6,
                "closed_pnl": 0.42,  # net of fees from Bybit
                "closed_at": "2026-05-18T07:40:00Z",
            }
        monkeypatch.setattr(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            _fake_closed_pnl,
        )

        summary = om._sweep_pending_pnl_from_bybit(tmp_db)
        assert summary["scanned"] == 1
        assert summary["filled"] == 1
        assert summary["still_pending"] == 0
        assert summary["errors"] == 0

        trade = tmp_db.get_trades(filters={"id": trade_id})[0]
        assert trade["pnl"] == pytest.approx(0.42, abs=0.001)
        assert trade["exit_price"] == pytest.approx(76977.6, abs=0.01)
        # pnl_percent = 0.42 / (76700 * 0.004) * 100 ≈ 0.1369
        assert trade["pnl_percent"] == pytest.approx(0.1369, abs=0.001)
        # Notes stamped with Bybit-truth marker.
        import json as _json
        notes = _json.loads(trade["notes"])
        assert notes["exit_price_source"] == "bybit_closed_pnl"
        assert notes["bybit_closed_pnl"] == 0.42

    def test_sweep_leaves_row_pending_when_bybit_has_no_record(
        self, tmp_db, monkeypatch,
    ):
        """Bybit hasn't booked the closed-pnl yet (typical for a
        trade that closed < 30s ago). Sweep returns 'still_pending'
        and the row stays at pnl=NULL for the next tick."""
        trade_id = self._seed_closed_pending(tmp_db)
        monkeypatch.setattr(
            om, "_load_account_cfgs_for_reconcile",
            lambda: {"bybit_2": {"id": "bybit_2"}},
        )
        monkeypatch.setattr(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            lambda *a, **kw: None,  # Bybit returns no record
        )

        summary = om._sweep_pending_pnl_from_bybit(tmp_db)
        assert summary["filled"] == 0
        assert summary["still_pending"] == 1

        trade = tmp_db.get_trades(filters={"id": trade_id})[0]
        assert trade["pnl"] is None  # still pending — try next tick

    def test_sweep_short_direction_pnl_percent_math(
        self, tmp_db, monkeypatch,
    ):
        """Short-side variant — same formula, different sign convention.
        Bybit's closed_pnl is already signed, we just write it through."""
        trade_id = self._seed_closed_pending(
            tmp_db, direction="short", entry_price=77100.0,
            stop_loss=77200.0, take_profit_1=76900.0, exit_price=76990.0,
        )
        monkeypatch.setattr(
            om, "_load_account_cfgs_for_reconcile",
            lambda: {"bybit_2": {"id": "bybit_2"}},
        )
        monkeypatch.setattr(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            lambda *a, **kw: {
                "avg_exit_price": 76990.0,
                "closed_pnl": 0.20,
                "closed_at": "2026-05-18T08:55:00Z",
            },
        )

        om._sweep_pending_pnl_from_bybit(tmp_db)
        trade = tmp_db.get_trades(filters={"id": trade_id})[0]
        assert trade["pnl"] == pytest.approx(0.20, abs=0.001)
        # pnl_percent = 0.20 / (77100 * 0.004) * 100 ≈ 0.0649
        assert trade["pnl_percent"] == pytest.approx(0.0649, abs=0.001)

    def test_sweep_skips_already_filled_rows(self, tmp_db, monkeypatch):
        """Rows that already have non-NULL pnl (reconciler-filled
        ones) must not be re-fetched — the SQL filter is the gate."""
        # Seed a closed-and-already-filled trade.
        self._seed_closed_pending(tmp_db)
        # Now flip pnl to non-NULL to simulate a previously-filled row.
        conn = tmp_db.connect()
        conn.execute("UPDATE trades SET pnl = 0.99 WHERE pnl IS NULL")
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            om, "_load_account_cfgs_for_reconcile",
            lambda: {"bybit_2": {"id": "bybit_2"}},
        )
        # If the sweep wrongly scans this row it'd call our stub —
        # raise to make the failure loud.
        def _should_not_be_called(*a, **kw):
            raise AssertionError("sweep called for already-filled row")
        monkeypatch.setattr(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            _should_not_be_called,
        )

        summary = om._sweep_pending_pnl_from_bybit(tmp_db)
        assert summary["scanned"] == 0

    def test_sweep_skips_backtest_rows(self, tmp_db, monkeypatch):
        """Backtest trades have no Bybit counterpart — must not be
        scanned regardless of pnl state."""
        self._seed_closed_pending(tmp_db, is_backtest=1)
        monkeypatch.setattr(
            om, "_load_account_cfgs_for_reconcile",
            lambda: {"bybit_2": {"id": "bybit_2"}},
        )
        monkeypatch.setattr(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("backtest row scanned")
            ),
        )
        summary = om._sweep_pending_pnl_from_bybit(tmp_db)
        assert summary["scanned"] == 0

