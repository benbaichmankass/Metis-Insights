"""S-031 PR4 regression tests
(architecture-audit-2026-05-02 P1-6 + Rule-3 close-path violation).

Pre-PR ``cmd_closeall`` called
``dl.close_all_bybit_positions_for_strategy`` which placed reduce-only
market orders directly, bypassing ``execute_pkg``'s single-entry
contract for live order placement (CLAUDE.md § Architecture rules § 3).

Post-PR the bot calls ``processor.close_open_positions(strategy=…,
account=…)`` which:
  1. Reads open trades from the trade log filtered by strategy/account.
  2. Resolves the per-account exchange client.
  3. Dispatches to ``execute.close_open_position`` (the canonical
     close path added in S-030 PR4).
  4. Marks the trade row ``status='closed'`` on success.

Tests pin:
  - The DB filter by strategy + account works (case-insensitive on
    strategy, exact on account).
  - The helper resolves the account config, dispatches via the
    canonical ``execute.close_open_position``, and never calls
    ``client.place_order`` directly from the helper.
  - On a successful close, the trade row is marked ``status='closed'``
    with ``exit_reason='manual_closeall'``.
  - Per-trade failures (missing creds, exchange error) become
    ``ok=False`` rows with an ``error`` string and DON'T crash the
    helper — other rows still process.
  - Empty filter scope returns ``[]`` without DB write.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, direction TEXT, entry_price REAL,
            exit_price REAL, stop_loss REAL,
            take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL,
            position_size REAL, setup_type TEXT, killzone TEXT, bias TEXT,
            entry_reason TEXT, exit_reason TEXT,
            pnl REAL, pnl_percent REAL,
            status TEXT DEFAULT 'open',
            notes TEXT,
            is_backtest INTEGER DEFAULT 0,
            strategy_name TEXT,
            account_id TEXT NOT NULL DEFAULT 'live',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_trade(
    db_path, *, symbol="BTCUSDT", direction="long", qty=0.001,
    status="open", strategy="vwap", account_id="bybit_2",
    is_backtest=0,
):
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
        "position_size, status, is_backtest, strategy_name, account_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-05-02 12:00:00", symbol, direction, 100.0, qty, status,
         is_backtest, strategy, account_id),
    )
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def _trade_status(db_path, trade_id) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, exit_reason, notes FROM trades WHERE id = ?",
        (trade_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Empty / no-rows scope
# ---------------------------------------------------------------------------


class TestEmptyScope:
    def test_no_open_trades_returns_empty_list(self, tmp_journal):
        from src.ui.processor import close_open_positions
        result = close_open_positions()
        assert result == []

    def test_only_closed_trades_returns_empty(self, tmp_journal):
        from src.ui.processor import close_open_positions
        _insert_trade(tmp_journal, status="closed")
        assert close_open_positions() == []

    def test_only_backtest_rows_returns_empty(self, tmp_journal):
        from src.ui.processor import close_open_positions
        _insert_trade(tmp_journal, is_backtest=1)
        assert close_open_positions() == []

    def test_strategy_filter_no_match_returns_empty(self, tmp_journal):
        from src.ui.processor import close_open_positions
        _insert_trade(tmp_journal, strategy="vwap")
        assert close_open_positions(strategy="turtle_soup") == []

    def test_account_filter_no_match_returns_empty(self, tmp_journal):
        from src.ui.processor import close_open_positions
        _insert_trade(tmp_journal, account_id="bybit_2")
        assert close_open_positions(account="bybit_1") == []

    def test_db_unreadable_returns_empty(self, tmp_path, monkeypatch):
        from src.ui.processor import close_open_positions
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "missing/x.db"))
        assert close_open_positions() == []


# ---------------------------------------------------------------------------
# Filter shape
# ---------------------------------------------------------------------------


class TestFilters:
    def _stub_accounts(self, monkeypatch, accounts):
        from src.bot import data_loaders as dl
        monkeypatch.setattr(dl, "list_accounts", lambda: accounts)

    def test_strategy_filter_case_insensitive(self, tmp_journal, monkeypatch):
        from src.ui import processor
        _insert_trade(tmp_journal, strategy="VWAP")
        self._stub_accounts(monkeypatch, [
            {"account_id": "bybit_2", "exchange": "bybit"},
        ])
        # Empty creds → fast-fail with ok=False, but the row still
        # came back from the SELECT — that's what we're checking.
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=None):
            rows = processor.close_open_positions(strategy="vwap")
        assert len(rows) == 1

    def test_account_filter_exact(self, tmp_journal, monkeypatch):
        from src.ui import processor
        _insert_trade(tmp_journal, account_id="bybit_1", strategy="turtle_soup")
        _insert_trade(tmp_journal, account_id="bybit_2", strategy="vwap")
        self._stub_accounts(monkeypatch, [
            {"account_id": "bybit_1", "exchange": "bybit"},
            {"account_id": "bybit_2", "exchange": "bybit"},
        ])
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=None):
            rows = processor.close_open_positions(account="bybit_1")
        assert len(rows) == 1
        assert rows[0]["account_id"] == "bybit_1"

    def test_combined_strategy_account(self, tmp_journal, monkeypatch):
        from src.ui import processor
        _insert_trade(tmp_journal, account_id="bybit_1", strategy="vwap")
        _insert_trade(tmp_journal, account_id="bybit_2", strategy="vwap")
        self._stub_accounts(monkeypatch, [
            {"account_id": "bybit_1", "exchange": "bybit"},
            {"account_id": "bybit_2", "exchange": "bybit"},
        ])
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=None):
            rows = processor.close_open_positions(strategy="vwap", account="bybit_2")
        assert len(rows) == 1
        assert rows[0]["account_id"] == "bybit_2"


# ---------------------------------------------------------------------------
# Dispatch via canonical close path (Rule-3 compliance)
# ---------------------------------------------------------------------------


class TestDispatchThroughExecutePkg:
    """The helper MUST route through ``execute.close_open_position``,
    not call ``client.place_order`` directly."""

    def _stub_accounts(self, monkeypatch, accounts):
        from src.bot import data_loaders as dl
        monkeypatch.setattr(dl, "list_accounts", lambda: accounts)

    def test_dispatches_to_execute_close_open_position(
        self, tmp_journal, monkeypatch,
    ):
        from src.ui import processor
        _insert_trade(
            tmp_journal, account_id="bybit_2", strategy="vwap",
            symbol="BTCUSDT", direction="long", qty=0.005,
        )
        self._stub_accounts(monkeypatch, [
            {"account_id": "bybit_2", "exchange": "bybit",
             "strategies": ["vwap"]},
        ])

        fake_client = MagicMock(name="bybit_client")
        # Spy on the canonical close helper.
        captured = {}

        def fake_close_open_position(client, account_cfg, *, symbol, side, qty):
            captured["called"] = True
            captured["client"] = client
            captured["account_id"] = account_cfg.get("account_id")
            captured["symbol"] = symbol
            captured["side"] = side
            captured["qty"] = qty
            return {
                "ok": True, "exchange_response": {"retCode": 0},
                "exchange_order_id": "order-abc", "error": None,
            }

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=fake_client,
        ), patch(
            "src.units.accounts.execute.close_open_position",
            side_effect=fake_close_open_position,
        ):
            rows = processor.close_open_positions(strategy="vwap")

        assert captured.get("called") is True
        assert captured["account_id"] == "bybit_2"
        assert captured["symbol"] == "BTCUSDT"
        assert captured["side"] == "long"
        assert captured["qty"] == 0.005
        # Bot's render layer expects ok + order_id.
        assert len(rows) == 1
        assert rows[0]["ok"] is True
        assert rows[0]["exchange_order_id"] == "order-abc"
        # And the helper must NOT have called client.place_order
        # directly — that would be the Rule-3 violation.
        fake_client.place_order.assert_not_called()

    def test_successful_close_marks_trade_closed(
        self, tmp_journal, monkeypatch,
    ):
        from src.ui import processor
        trade_id = _insert_trade(
            tmp_journal, account_id="bybit_2", strategy="vwap",
        )
        self._stub_accounts(monkeypatch, [
            {"account_id": "bybit_2", "exchange": "bybit"},
        ])
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=MagicMock(),
        ), patch(
            "src.units.accounts.execute.close_open_position",
            return_value={"ok": True, "exchange_order_id": "x",
                          "error": None, "exchange_response": {}},
        ):
            processor.close_open_positions(strategy="vwap")

        row = _trade_status(tmp_journal, trade_id)
        assert row["status"] == "closed"
        assert row["exit_reason"] == "manual_closeall"
        # Notes carries a closed_at timestamp.
        assert "closed_at=" in (row.get("notes") or "")

    def test_failed_close_keeps_trade_open(self, tmp_journal, monkeypatch):
        from src.ui import processor
        trade_id = _insert_trade(
            tmp_journal, account_id="bybit_2", strategy="vwap",
        )
        self._stub_accounts(monkeypatch, [
            {"account_id": "bybit_2", "exchange": "bybit"},
        ])
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=MagicMock(),
        ), patch(
            "src.units.accounts.execute.close_open_position",
            return_value={"ok": False, "exchange_order_id": None,
                          "error": "exchange refused",
                          "exchange_response": {}},
        ):
            rows = processor.close_open_positions(strategy="vwap")

        # Trade row stays open.
        row = _trade_status(tmp_journal, trade_id)
        assert row["status"] == "open"
        # But the result row carries the error.
        assert rows[0]["ok"] is False
        assert rows[0]["error"] == "exchange refused"


# ---------------------------------------------------------------------------
# Per-trade failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def _stub_accounts(self, monkeypatch, accounts):
        from src.bot import data_loaders as dl
        monkeypatch.setattr(dl, "list_accounts", lambda: accounts)

    def test_missing_account_config_yields_failure_row(
        self, tmp_journal, monkeypatch,
    ):
        from src.ui import processor
        _insert_trade(tmp_journal, account_id="ghost", strategy="vwap")
        self._stub_accounts(monkeypatch, [])
        rows = processor.close_open_positions(strategy="vwap")
        assert len(rows) == 1
        assert rows[0]["ok"] is False
        assert "account not found" in (rows[0]["error"] or "")

    def test_unsupported_exchange_yields_failure_row(
        self, tmp_journal, monkeypatch,
    ):
        from src.ui import processor
        _insert_trade(tmp_journal, account_id="kraken_1", strategy="vwap")
        self._stub_accounts(monkeypatch, [
            {"account_id": "kraken_1", "exchange": "kraken"},
        ])
        rows = processor.close_open_positions(strategy="vwap")
        assert len(rows) == 1
        assert rows[0]["ok"] is False
        assert "unsupported exchange" in (rows[0]["error"] or "")

    def test_missing_credentials_yields_failure_row(
        self, tmp_journal, monkeypatch,
    ):
        from src.ui import processor
        _insert_trade(tmp_journal, account_id="bybit_2", strategy="vwap")
        self._stub_accounts(monkeypatch, [
            {"account_id": "bybit_2", "exchange": "bybit"},
        ])
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=None,
        ):
            rows = processor.close_open_positions(strategy="vwap")
        assert rows[0]["ok"] is False
        assert "missing creds" in (rows[0]["error"] or "")

    def test_one_failure_does_not_block_others(self, tmp_journal, monkeypatch):
        from src.ui import processor
        _insert_trade(tmp_journal, account_id="bybit_1", strategy="vwap",
                      symbol="ETHUSDT")
        _insert_trade(tmp_journal, account_id="bybit_2", strategy="vwap",
                      symbol="BTCUSDT")
        self._stub_accounts(monkeypatch, [
            # bybit_1 has no creds — first row fails.
            {"account_id": "bybit_1", "exchange": "bybit"},
            {"account_id": "bybit_2", "exchange": "bybit"},
        ])

        def client_factory(account):
            if account.get("account_id") == "bybit_1":
                return None
            return MagicMock()

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            side_effect=client_factory,
        ), patch(
            "src.units.accounts.execute.close_open_position",
            return_value={"ok": True, "exchange_order_id": "o2",
                          "error": None, "exchange_response": {}},
        ):
            rows = processor.close_open_positions(strategy="vwap")

        assert len(rows) == 2
        statuses = {r["account_id"]: r["ok"] for r in rows}
        assert statuses == {"bybit_1": False, "bybit_2": True}
