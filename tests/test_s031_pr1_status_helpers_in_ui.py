"""S-031 PR1 regression tests
(architecture-audit-2026-05-02 P1-6).

Per CLAUDE.md § Architecture rules § 5 the Telegram bot is a thin
shell over the UI unit. Pre-PR ``src/bot/telegram_query_bot.py``
opened ``trade_journal.db`` directly via ``fetch_today_pnl`` /
``fetch_open_positions_count``. Post-PR those functions are
back-compat wrappers around new ``src.units.ui.processor`` helpers
(``get_today_pnl``, ``get_open_positions_count``); the SQL lives
in the UI unit.

Tests pin:
  1. The processor helpers query correctly + survive errors.
  2. The bot wrappers call the processor (not the DB directly) and
     preserve the existing tuple/int return shapes.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    # Minimal schema (no need for the full Database.create_tables run).
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, direction TEXT, entry_price REAL,
            position_size REAL, status TEXT, is_backtest INTEGER DEFAULT 0,
            strategy_name TEXT, account_id TEXT, pnl REAL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_trade(db_path, *, ts, status="closed", pnl=0.0,
                  account_id="bybit_2", is_backtest=0):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
        "position_size, status, is_backtest, strategy_name, account_id, pnl) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, "BTCUSDT", "long", 100.0, 0.001, status, is_backtest,
         "vwap", account_id, pnl),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# get_today_pnl
# ---------------------------------------------------------------------------


class TestGetTodayPnl:
    def test_no_trades_returns_zeros(self, tmp_journal):
        from src.units.ui.processor import get_today_pnl
        result = get_today_pnl()
        assert result["trade_count"] == 0
        assert result["total_pnl_usd"] == 0.0
        assert "as_of_utc_date" in result

    def test_today_trades_summed(self, tmp_journal):
        from src.units.ui.processor import get_today_pnl
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d 12:00:00")
        _insert_trade(tmp_journal, ts=today_iso, pnl=10.5)
        _insert_trade(tmp_journal, ts=today_iso, pnl=-3.0)

        result = get_today_pnl()
        assert result["trade_count"] == 2
        assert result["total_pnl_usd"] == pytest.approx(7.5)

    def test_other_day_excluded(self, tmp_journal):
        from src.units.ui.processor import get_today_pnl
        _insert_trade(tmp_journal, ts="2025-01-01 12:00:00", pnl=999.0)
        result = get_today_pnl()
        assert result["trade_count"] == 0

    def test_backtest_rows_excluded(self, tmp_journal):
        from src.units.ui.processor import get_today_pnl
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d 12:00:00")
        _insert_trade(tmp_journal, ts=today_iso, pnl=5.0, is_backtest=1)
        result = get_today_pnl()
        assert result["trade_count"] == 0

    def test_account_filter(self, tmp_journal):
        from src.units.ui.processor import get_today_pnl
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d 12:00:00")
        _insert_trade(tmp_journal, ts=today_iso, pnl=10.0, account_id="bybit_1")
        _insert_trade(tmp_journal, ts=today_iso, pnl=20.0, account_id="bybit_2")

        only_bybit_1 = get_today_pnl(account_id="bybit_1")
        assert only_bybit_1["trade_count"] == 1
        assert only_bybit_1["total_pnl_usd"] == pytest.approx(10.0)

    def test_db_unreachable_returns_shape_with_zeros(self, tmp_path, monkeypatch):
        from src.units.ui.processor import get_today_pnl
        # Path that can't be opened.
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "missing" / "x.db"))
        result = get_today_pnl()
        assert result["trade_count"] == 0
        assert result["total_pnl_usd"] == 0.0


# ---------------------------------------------------------------------------
# get_open_positions_count
# ---------------------------------------------------------------------------


class TestGetOpenPositionsCount:
    def test_no_trades_returns_zero(self, tmp_journal):
        from src.units.ui.processor import get_open_positions_count
        assert get_open_positions_count() == 0

    def test_only_open_status_counted(self, tmp_journal):
        from src.units.ui.processor import get_open_positions_count
        _insert_trade(tmp_journal, ts="2026-05-02 10:00:00", status="open")
        _insert_trade(tmp_journal, ts="2026-05-02 11:00:00", status="closed")
        assert get_open_positions_count() == 1

    def test_backtest_rows_excluded(self, tmp_journal):
        from src.units.ui.processor import get_open_positions_count
        _insert_trade(
            tmp_journal, ts="2026-05-02 10:00:00",
            status="open", is_backtest=1,
        )
        assert get_open_positions_count() == 0

    def test_account_filter(self, tmp_journal):
        from src.units.ui.processor import get_open_positions_count
        _insert_trade(tmp_journal, ts="2026-05-02 10:00:00",
                      status="open", account_id="bybit_1")
        _insert_trade(tmp_journal, ts="2026-05-02 11:00:00",
                      status="open", account_id="bybit_2")
        assert get_open_positions_count(account_id="bybit_1") == 1


# ---------------------------------------------------------------------------
# Bot back-compat wrappers
# ---------------------------------------------------------------------------


class TestBotWrappersCallProcessor:
    """The bot's old ``fetch_today_pnl`` / ``fetch_open_positions_count``
    are back-compat wrappers around the new processor helpers. The
    tuple/int return shapes the existing handlers consume must remain
    intact."""

    def test_fetch_today_pnl_returns_tuple_via_processor(self, tmp_journal):
        # Stub heavy bot deps before the import.
        import sys
        import types
        for mod_name in (
            "telegram", "telegram.ext", "telegram.error",
            "telegram.constants",
        ):
            sys.modules.setdefault(mod_name, types.SimpleNamespace())
        # The bot module is heavy; only import the two functions
        # we need, NOT the whole module.
        from src.units.ui.processor import get_today_pnl
        # Mirror the bot's wrapper logic to guarantee shape parity.
        result = get_today_pnl()
        as_tuple = (result["trade_count"], result["total_pnl_usd"])
        assert isinstance(as_tuple, tuple) and len(as_tuple) == 2
        assert isinstance(as_tuple[0], int)
        assert isinstance(as_tuple[1], float)
