"""
Smoke tests for src/bot/telegram_query_bot.py.

Tests target pure-Python helper functions that carry no telegram dependency:
is_halted, is_authorised, get_strategy_label, format_target_options,
format_backtest_summary, fetch_today_pnl, fetch_open_positions_count.

Heavy deps (telegram, pybit) are stubbed at sys.modules level before import.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub heavy deps before any src import
# ---------------------------------------------------------------------------
for _mod in (
    "telegram",
    "telegram.ext",
    "dotenv",
    "requests",
    "pybit",
    "pybit.unified_trading",
    "src.runtime.signal_notifications",
):
    sys.modules.setdefault(_mod, MagicMock())

# Provide realistic dotenv stubs
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

# telegram.Update must be importable as a class
_tg_mock = sys.modules["telegram"]
_tg_mock.Update = MagicMock
_tg_mock.BotCommand = MagicMock
_tg_mock.InlineKeyboardButton = MagicMock
_tg_mock.InlineKeyboardMarkup = MagicMock
_tg_ext_mock = sys.modules["telegram.ext"]
_tg_ext_mock.Application = MagicMock
_tg_ext_mock.CommandHandler = MagicMock
_tg_ext_mock.CallbackQueryHandler = MagicMock
_tg_ext_mock.ContextTypes = MagicMock()
_tg_ext_mock.ContextTypes.DEFAULT_TYPE = object

import src.bot.telegram_query_bot as bot  # noqa: E402


# ---------------------------------------------------------------------------
# is_halted
# ---------------------------------------------------------------------------

class TestIsHalted:
    def test_false_when_no_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag"))
        assert bot.is_halted() is False

    def test_true_when_flag_exists(self, tmp_path, monkeypatch):
        flag = tmp_path / "trader_halt.flag"
        flag.touch()
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(flag))
        assert bot.is_halted() is True


# ---------------------------------------------------------------------------
# is_authorised
# ---------------------------------------------------------------------------

class TestIsAuthorised:
    def _make_update(self, chat_id):
        upd = MagicMock()
        upd.effective_chat.id = chat_id
        upd.callback_query = None
        return upd

    def test_authorised_when_chat_id_matches(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        assert bot.is_authorised(self._make_update(12345)) is True

    def test_not_authorised_when_chat_id_differs(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        assert bot.is_authorised(self._make_update(99999)) is False

    def test_false_when_no_effective_chat_no_callback(self):
        upd = MagicMock()
        upd.effective_chat = None
        upd.callback_query = None
        assert bot.is_authorised(upd) is False


# ---------------------------------------------------------------------------
# get_strategy_label
# ---------------------------------------------------------------------------

class TestGetStrategyLabel:
    def test_killzone_maps_to_ict(self):
        assert bot.get_strategy_label({"STRATEGY": "killzone"}) == "ICT"

    def test_ict_maps_to_ict(self):
        assert bot.get_strategy_label({"STRATEGY": "ict"}) == "ICT"

    def test_vwap_maps_to_vwap(self):
        assert bot.get_strategy_label({"STRATEGY": "vwap"}) == "VWAP"

    def test_breakout_maps_to_breakout(self):
        assert bot.get_strategy_label({"STRATEGY": "breakout"}) == "Breakout"

    def test_multiplexed_maps_to_multi(self):
        assert bot.get_strategy_label({"STRATEGY": "multiplexed"}) == "Multi"

    def test_unknown_returns_default(self):
        assert bot.get_strategy_label({"STRATEGY": "unknown_xyz"}) == "Strategy"

    def test_empty_env_returns_default(self):
        assert bot.get_strategy_label({}) == "Strategy"

    def test_none_env_falls_back_gracefully(self, monkeypatch):
        monkeypatch.setattr(bot, "load_account_env", lambda: {})
        assert bot.get_strategy_label(None) == "Strategy"

    def test_case_insensitive(self):
        assert bot.get_strategy_label({"STRATEGY": "VWAP"}) == "VWAP"

    def test_legacy_strategy_name_key(self):
        assert bot.get_strategy_label({"STRATEGY_NAME": "killzone"}) == "ICT"


# ---------------------------------------------------------------------------
# format_target_options
# ---------------------------------------------------------------------------

def test_format_target_options_returns_string(monkeypatch):
    monkeypatch.setattr(bot, "get_strategy_label", lambda env_vars=None: "VWAP")
    result = bot.format_target_options()
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# format_backtest_summary
# ---------------------------------------------------------------------------

def test_format_backtest_summary_contains_key_fields():
    row = {
        "id": 1, "run_date": "2026-04-28", "strategy_version": "v1.0",
        "start_date": "2026-01-01", "end_date": "2026-04-01",
        "total_trades": 50, "winning_trades": 30, "losing_trades": 20,
        "win_rate": "60%", "profit_factor": 1.8, "expectancy": 25.0,
        "max_drawdown": 500.0, "max_drawdown_pct": "5%", "sharpe_ratio": 1.2,
        "total_pnl": 1250.0, "total_pnl_pct": "12.5%",
        "avg_win": 80.0, "avg_loss": 40.0,
        "largest_win": 300.0, "largest_loss": 150.0,
        "created_at": "2026-04-28T00:00:00",
    }
    summary = bot.format_backtest_summary(row)
    assert "50" in summary          # total_trades
    assert "60%" in summary         # win_rate
    assert "1250.0" in summary      # total_pnl


# ---------------------------------------------------------------------------
# fetch_today_pnl / fetch_open_positions_count — in-memory DB
# ---------------------------------------------------------------------------

def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            pnl REAL,
            status TEXT,
            is_backtest INTEGER,
            timestamp TEXT
        )
    """)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.executemany(
        "INSERT INTO trades (symbol, pnl, status, is_backtest, timestamp) VALUES (?, ?, ?, ?, ?)",
        [
            ("BTCUSDT", 100.0, "closed", 0, f"{today} 10:00:00"),
            ("BTCUSDT", -30.0, "closed", 0, f"{today} 11:00:00"),
            ("BTCUSDT",  50.0, "closed", 1, f"{today} 12:00:00"),  # backtest — excluded
            ("BTCUSDT",   0.0, "open",   0, f"{today} 13:00:00"),  # open position
        ],
    )
    conn.commit()
    conn.close()
    return db_path


class TestFetchTodayPnl:
    def test_sums_live_closed_trades(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        count, pnl = bot.fetch_today_pnl()
        # query counts all non-backtest live rows (open + closed)
        assert count == 3           # two closed + one open live trade
        assert abs(pnl - 70.0) < 0.01  # 100 + (-30) + 0 = 70

    def test_returns_zeros_on_missing_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "no.db"))
        count, pnl = bot.fetch_today_pnl()
        assert count == 0
        assert pnl == 0.0


class TestFetchOpenPositionsCount:
    def test_counts_open_live_trades(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        count = bot.fetch_open_positions_count()
        assert count == 1

    def test_returns_zero_on_missing_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "no.db"))
        assert bot.fetch_open_positions_count() == 0
