"""
Smoke tests for src/bot/telegram_query_bot.py.

Tests target pure-Python helper functions that carry no telegram dependency:
is_halted, is_authorised, get_strategy_label,
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
from unittest.mock import AsyncMock, MagicMock

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
    def _account_with(self, monkeypatch, **env_vars):
        """Return an account dict whose _account_env resolves to env_vars."""
        monkeypatch.setattr(bot, "_account_env", lambda _acct: env_vars)
        return {"env_path": "/fake/.env"}

    def test_killzone_maps_to_ict(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY="killzone")
        assert bot.get_strategy_label(account) == "ICT"

    def test_ict_maps_to_ict(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY="ict")
        assert bot.get_strategy_label(account) == "ICT"

    def test_vwap_maps_to_vwap(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY="vwap")
        assert bot.get_strategy_label(account) == "VWAP"

    def test_breakout_maps_to_breakout(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY="breakout")
        assert bot.get_strategy_label(account) == "Breakout"

    def test_multiplexed_maps_to_multi(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY="multiplexed")
        assert bot.get_strategy_label(account) == "Multi"

    def test_unknown_returns_default(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY="unknown_xyz")
        assert bot.get_strategy_label(account) == "Strategy"

    def test_empty_env_returns_default(self, monkeypatch):
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        assert bot.get_strategy_label({}) == "Strategy"

    def test_none_env_falls_back_gracefully(self, monkeypatch):
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        assert bot.get_strategy_label(None) == "Strategy"

    def test_case_insensitive(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY="VWAP")
        assert bot.get_strategy_label(account) == "VWAP"

    def test_legacy_strategy_name_key(self, monkeypatch):
        account = self._account_with(monkeypatch, STRATEGY_NAME="killzone")
        assert bot.get_strategy_label(account) == "ICT"


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


# ---------------------------------------------------------------------------
# S-003 N1-b: per-account fetch helpers + cmd_status multi-account
# ---------------------------------------------------------------------------

def _make_db_with_accounts(tmp_path) -> str:
    """DB with two accounts and an account_id column for per-account filter tests."""
    db_path = str(tmp_path / "trades_acct.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            pnl REAL,
            status TEXT,
            is_backtest INTEGER,
            timestamp TEXT,
            account_id TEXT
        )
    """)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.executemany(
        "INSERT INTO trades (symbol, pnl, status, is_backtest, timestamp, account_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            # account "live": two live trades today
            ("BTCUSDT", 200.0, "closed", 0, f"{today} 09:00:00", "live"),
            ("BTCUSDT",  -50.0, "closed", 0, f"{today} 10:00:00", "live"),
            # account "live": one open position today
            ("ETHUSDT",   0.0, "open",   0, f"{today} 11:00:00", "live"),
            # account "alpha": one live trade today
            ("SOLUSDT",  75.0, "closed", 0, f"{today} 09:30:00", "alpha"),
            # account "alpha": one open position
            ("SOLUSDT",   0.0, "open",   0, f"{today} 10:30:00", "alpha"),
            # backtest row — must be excluded for both accounts
            ("BTCUSDT",  999.0, "closed", 1, f"{today} 12:00:00", "live"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


class TestFetchTodayPnlPerAccount:
    def test_filters_by_account_id(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)

        count_live, pnl_live = bot.fetch_today_pnl(account_id="live")
        # 3 live rows for "live" (200, -50, 0-open)
        assert count_live == 3
        assert abs(pnl_live - 150.0) < 0.01  # 200 + (-50) + 0

        count_alpha, pnl_alpha = bot.fetch_today_pnl(account_id="alpha")
        # 2 live rows for "alpha" (75, 0-open)
        assert count_alpha == 2
        assert abs(pnl_alpha - 75.0) < 0.01

    def test_no_account_filter_returns_aggregate(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        count, pnl = bot.fetch_today_pnl()
        # 5 live rows total across both accounts (backtest excluded)
        assert count == 5
        assert abs(pnl - 225.0) < 0.01  # 200 - 50 + 0 + 75 + 0

    def test_unknown_account_id_returns_zeros(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        count, pnl = bot.fetch_today_pnl(account_id="nonexistent")
        assert count == 0
        assert pnl == 0.0

    def test_missing_db_returns_zeros(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "no.db"))
        count, pnl = bot.fetch_today_pnl(account_id="live")
        assert count == 0
        assert pnl == 0.0


class TestFetchOpenPositionsCountPerAccount:
    def test_filters_by_account_id(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)

        assert bot.fetch_open_positions_count(account_id="live") == 1
        assert bot.fetch_open_positions_count(account_id="alpha") == 1

    def test_no_account_filter_returns_aggregate(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        assert bot.fetch_open_positions_count() == 2

    def test_unknown_account_id_returns_zero(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        assert bot.fetch_open_positions_count(account_id="ghost") == 0

    def test_missing_db_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "no.db"))
        assert bot.fetch_open_positions_count(account_id="live") == 0


class TestCmdStatusMultiAccount:
    """cmd_status must produce a per-account block for each account returned
    by dl.list_accounts(), using per-account DB filters."""

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_shows_block_per_account(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag"))
        monkeypatch.setattr(bot, "_account_env", lambda acc: {"STRATEGY": "ict"})
        monkeypatch.setattr(bot, "get_service_status", lambda svc: "active")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live",  "exchange": "bybit",   "env_path": "",
             "service": "ict-trader-live"},
            {"account_id": "alpha", "exchange": "binance", "env_path": "",
             "service": "ict-trader-alpha"},
        ])

        upd = self._make_update()
        self._run(bot.cmd_status(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        # Both account IDs present
        assert "live" in sent
        assert "alpha" in sent
        # Service names present
        assert "ict-trader-live" in sent
        assert "ict-trader-alpha" in sent
        # Per-account P&L figures present (live: 150, alpha: 75)
        assert "$+150.00" in sent
        assert "$+75.00" in sent
        # Both open-position counts
        assert "Open (DB): 1" in sent

    def test_no_accounts_falls_back_to_aggregate(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag"))
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot, "get_service_status", lambda svc: "inactive")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])

        upd = self._make_update()
        self._run(bot.cmd_status(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "ICT Trading Bot Status" in sent
        # Fallback still shows aggregate — 5 live rows, pnl 225
        assert "$+225.00" in sent

    def test_halted_state_shown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        flag = tmp_path / "halt.flag"
        flag.touch()
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(flag))
        monkeypatch.setattr(bot, "get_service_status", lambda svc: "active")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "no.db"))

        upd = self._make_update()
        self._run(bot.cmd_status(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "HALTED" in sent

    def test_list_accounts_exception_falls_back_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag"))
        monkeypatch.setattr(bot, "get_service_status", lambda svc: "active")
        monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "no.db"))
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})

        def boom():
            raise RuntimeError("registry error")

        monkeypatch.setattr(bot.dl, "list_accounts", boom)

        upd = self._make_update()
        self._run(bot.cmd_status(upd, MagicMock()))

        # Must not crash — reply sent with fallback content
        upd.message.reply_text.assert_called_once()
        sent = upd.message.reply_text.call_args.args[0]
        assert "ICT Trading Bot Status" in sent


# ---------------------------------------------------------------------------
# PR-C: data_loaders facade wiring
# ---------------------------------------------------------------------------

class TestGetLastLogsRoutesThroughDataLoaders:
    """get_last_logs() must delegate to dl.recent_logs_for and pass through
    both the configured service name and the requested line count."""

    def test_delegates_to_dl_recent_logs_for(self, monkeypatch):
        captured = {}

        def _fake_recent_logs_for(service, n=20):
            captured["service"] = service
            captured["n"] = n
            return "== fake journalctl output =="

        monkeypatch.setattr(bot.dl, "recent_logs_for", _fake_recent_logs_for)
        monkeypatch.setattr(bot, "LIVE_SERVICE_NAME", "ict-trader-live")

        out = bot.get_last_logs(lines=42)

        assert out == "== fake journalctl output =="
        assert captured == {"service": "ict-trader-live", "n": 42}

    def test_propagates_unavailable_marker(self, monkeypatch):
        monkeypatch.setattr(
            bot.dl, "recent_logs_for", lambda service, n=20: "⚠️ unavailable"
        )
        assert bot.get_last_logs() == "⚠️ unavailable"


class TestLatestBacktestPullsFromDataLoaders:
    """cmd_latest_backtest must source completed/idle results from
    dl.latest_backtests_per_model() (newest entry) instead of the legacy
    fetch_latest_backtest_result(). The 'running' branch is unrelated and
    stays intentionally untouched in this PR."""

    def _make_row(self, **overrides):
        row = {
            "id": 7, "run_date": "2026-04-29", "strategy_version": "vX",
            "start_date": "2026-04-01", "end_date": "2026-04-28",
            "total_trades": 10, "winning_trades": 6, "losing_trades": 4,
            "win_rate": "60%", "profit_factor": 1.5, "expectancy": 12.0,
            "max_drawdown": 100.0, "max_drawdown_pct": "3%",
            "sharpe_ratio": 1.1, "total_pnl": 555.5, "total_pnl_pct": "5.5%",
            "avg_win": 50.0, "avg_loss": 25.0,
            "largest_win": 200.0, "largest_loss": 80.0,
            "created_at": "2026-04-29T00:00:00",
        }
        row.update(overrides)
        return row

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None  # is_authorised checks truthiness of this
        upd.message.reply_text = AsyncMock()
        return upd

    def test_completed_branch_uses_dl_first_row(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "BACKTEST_STATUS", {
            "state": "completed", "started_at": None, "finished_at": "now",
            "last_error": None, "last_stdout_tail": None, "last_returncode": 0,
        })
        rows = [self._make_row(id=99), self._make_row(id=100, strategy_version="vY")]
        monkeypatch.setattr(bot.dl, "latest_backtests_per_model", lambda: rows)

        upd = self._make_update()
        self._run(bot.cmd_latest_backtest(upd, MagicMock()))

        upd.message.reply_text.assert_called()
        sent = upd.message.reply_text.call_args.args[0]
        # newest row surfaces first — confirms rows[0] selection, not rows[-1]
        assert "Row ID: 99" in sent

    def test_idle_branch_falls_back_when_no_rows(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "BACKTEST_STATUS", {
            "state": "idle", "started_at": None, "finished_at": None,
            "last_error": None, "last_stdout_tail": None, "last_returncode": None,
        })
        monkeypatch.setattr(bot.dl, "latest_backtests_per_model", lambda: [])

        upd = self._make_update()
        self._run(bot.cmd_latest_backtest(upd, MagicMock()))

        upd.message.reply_text.assert_called_once()
        sent = upd.message.reply_text.call_args.args[0]
        assert "No backtest running" in sent

    def test_completed_branch_falls_back_when_no_rows(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "BACKTEST_STATUS", {
            "state": "completed", "started_at": None, "finished_at": "now",
            "last_error": None, "last_stdout_tail": None, "last_returncode": 0,
        })
        monkeypatch.setattr(bot.dl, "latest_backtests_per_model", lambda: [])

        upd = self._make_update()
        self._run(bot.cmd_latest_backtest(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "Backtest COMPLETED" in sent


# ---------------------------------------------------------------------------
# PR-D: balance / positions formatters consume dl.account_* output
# ---------------------------------------------------------------------------

class TestFormatBybitBalance:
    def _account(self):
        return {"account_id": "live", "exchange": "bybit", "env_path": ""}

    def test_renders_per_coin_lines_from_raw(self, monkeypatch):
        monkeypatch.setattr(bot, "_account_env", lambda acc: {"STRATEGY": "vwap"})
        monkeypatch.setattr(bot.dl, "account_balance", lambda acc: {
            "total_usdt": 1234.0,
            "raw": {"result": {"list": [{"coin": [
                {"coin": "USDT", "walletBalance": "1000.5", "usdValue": "1000.5"},
                {"coin": "BTC",  "walletBalance": "0.0123", "usdValue": "720.0"},
                {"coin": "ETH",  "walletBalance": "0",      "usdValue": "0"},  # filtered
            ]}]}},
        })
        out = bot.format_bybit_balance(self._account())
        assert "VWAP Balance" in out
        assert "USDT: 1000.5000" in out
        assert "BTC: 0.0123" in out
        assert "ETH:" not in out  # zero-balance row dropped

    def test_returns_unavailable_when_loader_returns_none(self, monkeypatch):
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot.dl, "account_balance", lambda acc: None)
        out = bot.format_bybit_balance(self._account())
        assert "⚠️" in out and "unavailable" in out


class TestFormatBybitPositions:
    def _account(self):
        return {"account_id": "live", "exchange": "bybit", "env_path": ""}

    def test_renders_normalized_rows(self, monkeypatch):
        monkeypatch.setattr(bot, "_account_env", lambda acc: {"STRATEGY": "ict"})
        monkeypatch.setattr(bot.dl, "account_open_positions", lambda acc: [
            {"symbol": "BTCUSDT", "side": "Buy", "size": 0.05,
             "entry_price": 50000.0, "unrealised_pnl": 12.34},
        ])
        out = bot.format_bybit_positions(self._account())
        assert "ICT Positions" in out
        assert "BTCUSDT Buy" in out
        assert "Entry: $50,000.00" in out
        assert "PnL: $+12.34" in out

    def test_empty_list_renders_no_open(self, monkeypatch):
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot.dl, "account_open_positions", lambda acc: [])
        out = bot.format_bybit_positions(self._account())
        assert "No open positions" in out

    def test_none_renders_unavailable(self, monkeypatch):
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot.dl, "account_open_positions", lambda acc: None)
        out = bot.format_bybit_positions(self._account())
        assert "⚠️" in out and "unavailable" in out


class TestFormatBinanceBalance:
    def _account(self):
        return {"account_id": "alpha", "exchange": "binance", "env_path": ""}

    def test_renders_total_free_used(self, monkeypatch):
        monkeypatch.setattr(bot, "_account_env", lambda acc: {"STRATEGY": "breakout"})
        monkeypatch.setattr(bot.dl, "account_balance", lambda acc: {
            "total_usdt": 500.0,
            "raw": {"USDT": {"total": 500.0, "free": 480.0, "used": 20.0}},
        })
        out = bot.format_binance_balance(self._account())
        assert "Breakout Balance (Binance Futures)" in out
        assert "USDT Total: 500.00" in out
        assert "USDT Free: 480.00" in out
        assert "USDT Used: 20.00" in out


class TestCmdBalanceIteratesAccounts:
    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_concatenates_blocks_per_account(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live",  "exchange": "bybit",   "env_path": ""},
            {"account_id": "alpha", "exchange": "binance", "env_path": ""},
        ])
        monkeypatch.setattr(bot, "format_bybit_balance", lambda acc: "BYBIT-BLOCK")
        monkeypatch.setattr(bot, "format_binance_balance", lambda acc: "BINANCE-BLOCK")

        upd = self._make_update()
        self._run(bot.cmd_balance(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "BYBIT-BLOCK" in sent
        assert "BINANCE-BLOCK" in sent
        assert sent.index("BYBIT-BLOCK") < sent.index("BINANCE-BLOCK")

    def test_no_accounts_renders_empty_message(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        upd = self._make_update()
        self._run(bot.cmd_balance(upd, MagicMock()))
        sent = upd.message.reply_text.call_args.args[0]
        assert "No accounts configured" in sent


class TestCmdTradesIteratesAccounts:
    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_concatenates_position_blocks(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live", "exchange": "bybit", "env_path": ""},
        ])
        monkeypatch.setattr(bot, "format_bybit_positions", lambda acc: "POSITIONS-OK")

        upd = self._make_update()
        self._run(bot.cmd_trades(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert sent == "POSITIONS-OK"


class TestCmdBalanceTradesPerAccountFailureIsolation:
    """PR-F — restored from PR-D's trim. A raising formatter for one
    account must not block the other accounts' blocks from rendering."""

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_balance_one_account_raises_others_render(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live",  "exchange": "bybit",   "env_path": ""},
            {"account_id": "alpha", "exchange": "binance", "env_path": ""},
        ])

        def boom(_acc):
            raise RuntimeError("nope")

        monkeypatch.setattr(bot, "format_bybit_balance", boom)
        monkeypatch.setattr(bot, "format_binance_balance",
                            lambda acc: "BINANCE-OK")
        upd = self._make_update()
        self._run(bot.cmd_balance(upd, MagicMock()))
        sent = upd.message.reply_text.call_args.args[0]
        assert "BINANCE-OK" in sent
        assert "live" in sent and "nope" in sent

    def test_trades_one_account_raises_others_render(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live",  "exchange": "bybit",   "env_path": ""},
            {"account_id": "alpha", "exchange": "binance", "env_path": ""},
        ])

        def boom(_acc):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(bot, "format_binance_positions", boom)
        monkeypatch.setattr(bot, "format_bybit_positions",
                            lambda acc: "BYBIT-POS-OK")
        upd = self._make_update()
        self._run(bot.cmd_trades(upd, MagicMock()))
        sent = upd.message.reply_text.call_args.args[0]
        assert "BYBIT-POS-OK" in sent
        assert "alpha" in sent and "kaboom" in sent


class TestCmdLast5IteratesAccounts:
    """PR-E — /last5 wired through dl.recent_trades_for + dl.list_accounts."""

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        upd.message.reply_document = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    @staticmethod
    def _trade_row(trade_id, symbol="BTCUSDT"):
        return {
            "id": trade_id, "timestamp": "2026-04-29T10:00", "symbol": symbol,
            "direction": "LONG", "entry_price": 65000.0, "exit_price": 66000.0,
            "stop_loss": 64000.0, "take_profit_1": 66500.0,
            "take_profit_2": 67000.0, "take_profit_3": 67500.0,
            "position_size": 0.01, "setup_type": "ICT", "killzone": "London",
            "bias": "Bullish", "entry_reason": "FVG fill",
            "exit_reason": "TP1 hit", "pnl": 10.0, "pnl_percent": 1.5,
            "status": "CLOSED", "notes": "n/a", "is_backtest": 0,
            "created_at": "2026-04-29 10:30:00",
        }

    def test_calls_recent_trades_for_each_account(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        accounts = [
            {"account_id": "live",  "exchange": "bybit",   "env_path": ""},
            {"account_id": "alpha", "exchange": "binance", "env_path": ""},
        ]
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: accounts)
        seen = []

        def fake_recent(acc, n=5):
            seen.append((acc["account_id"], n))
            return [self._trade_row(1)] if acc["account_id"] == "live" else []

        monkeypatch.setattr(bot.dl, "recent_trades_for", fake_recent)
        # No charts available
        monkeypatch.setattr(bot.os.path, "exists", lambda _p: False)

        upd = self._make_update()
        self._run(bot.cmd_last5(upd, MagicMock()))

        assert ("live", 5) in seen
        assert ("alpha", 5) in seen
        # One trade rendered.
        msgs = [c.args[0] for c in upd.message.reply_text.call_args_list]
        assert any("Trade #1" in m for m in msgs)

    def test_no_trades_anywhere_renders_empty_message(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live", "exchange": "bybit", "env_path": ""},
        ])
        monkeypatch.setattr(bot.dl, "recent_trades_for", lambda acc, n=5: [])
        upd = self._make_update()
        self._run(bot.cmd_last5(upd, MagicMock()))
        sent = [c.args[0] for c in upd.message.reply_text.call_args_list]
        assert any("No trades found" in m for m in sent)

    def test_loader_failure_isolated_per_account(self, monkeypatch):
        """A raising loader for one account does not block the other."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live",  "exchange": "bybit",   "env_path": ""},
            {"account_id": "alpha", "exchange": "binance", "env_path": ""},
        ])

        def fake_recent(acc, n=5):
            if acc["account_id"] == "alpha":
                raise RuntimeError("boom")
            return [self._trade_row(7, "ETHUSDT")]

        monkeypatch.setattr(bot.dl, "recent_trades_for", fake_recent)
        monkeypatch.setattr(bot.os.path, "exists", lambda _p: False)

        upd = self._make_update()
        self._run(bot.cmd_last5(upd, MagicMock()))
        msgs = [c.args[0] for c in upd.message.reply_text.call_args_list]
        # Warning surfaced for alpha + trade rendered for live.
        assert any("alpha" in m and "boom" in m for m in msgs)
        assert any("Trade #7" in m for m in msgs)

    def test_list_accounts_failure_warns_and_returns(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")

        def boom():
            raise RuntimeError("nope")

        monkeypatch.setattr(bot.dl, "list_accounts", boom)
        upd = self._make_update()
        self._run(bot.cmd_last5(upd, MagicMock()))
        sent = [c.args[0] for c in upd.message.reply_text.call_args_list]
        assert any("Could not list accounts" in m for m in sent)


# ---------------------------------------------------------------------------
# close_all_bybit_positions (M2a) — per-account migration tests
# ---------------------------------------------------------------------------

class TestCloseAllBybitPositions:
    """Tests for the migrated close_all_bybit_positions(account: dict)."""

    def _account(self, aid="live"):
        return {"account_id": aid, "exchange": "bybit", "env_path": ""}

    def _fake_client(self, positions):
        """Build a mock Bybit client with the given position list."""
        client = MagicMock()
        client.get_positions.return_value = {
            "result": {"list": positions}
        }
        client.place_order.return_value = {"retCode": 0}
        return client

    def test_place_order_called_with_reduce_only_and_correct_args(self, monkeypatch):
        positions = [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.01"},
            {"symbol": "ETHUSDT", "side": "Sell", "size": "0.5"},
        ]
        client = self._fake_client(positions)
        monkeypatch.setattr(bot.dl, "bybit_client_for", lambda acc: client)

        result = bot.close_all_bybit_positions(self._account())

        assert client.place_order.call_count == 2
        calls = {c.kwargs["symbol"]: c.kwargs for c in client.place_order.call_args_list}
        # Buy position → Sell close
        assert calls["BTCUSDT"]["side"] == "Sell"
        assert calls["BTCUSDT"]["reduceOnly"] is True
        assert calls["BTCUSDT"]["category"] == "linear"
        assert calls["BTCUSDT"]["qty"] == "0.01"
        # Sell position → Buy close
        assert calls["ETHUSDT"]["side"] == "Buy"
        assert calls["ETHUSDT"]["reduceOnly"] is True
        assert calls["ETHUSDT"]["qty"] == "0.5"
        assert "Closed 2" in result

    def test_side_flip_buy_to_sell(self, monkeypatch):
        client = self._fake_client([{"symbol": "BTCUSDT", "side": "Buy", "size": "1.0"}])
        monkeypatch.setattr(bot.dl, "bybit_client_for", lambda acc: client)
        bot.close_all_bybit_positions(self._account())
        assert client.place_order.call_args.kwargs["side"] == "Sell"

    def test_side_flip_sell_to_buy(self, monkeypatch):
        client = self._fake_client([{"symbol": "ETHUSDT", "side": "Sell", "size": "2.0"}])
        monkeypatch.setattr(bot.dl, "bybit_client_for", lambda acc: client)
        bot.close_all_bybit_positions(self._account())
        assert client.place_order.call_args.kwargs["side"] == "Buy"

    def test_empty_positions_returns_no_open_message(self, monkeypatch):
        client = self._fake_client([])
        monkeypatch.setattr(bot.dl, "bybit_client_for", lambda acc: client)
        result = bot.close_all_bybit_positions(self._account("bybit-sub1"))
        assert "No open positions to close" in result
        assert "bybit-sub1" in result
        client.place_order.assert_not_called()

    def test_no_creds_returns_error_message(self, monkeypatch):
        monkeypatch.setattr(bot.dl, "bybit_client_for", lambda acc: None)
        result = bot.close_all_bybit_positions(self._account("bybit-sub2"))
        assert "credentials not found" in result
        assert "bybit-sub2" in result

    def test_per_position_failure_does_not_stop_other_positions(self, monkeypatch):
        """If one place_order raises, the other positions still get closed."""
        call_count = {"n": 0}

        def flaky_place_order(**kwargs):
            call_count["n"] += 1
            if kwargs["symbol"] == "BTCUSDT":
                raise RuntimeError("exchange error")
            return {"retCode": 0}

        positions = [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.01"},
            {"symbol": "ETHUSDT", "side": "Buy", "size": "0.5"},
        ]
        client = self._fake_client(positions)
        client.place_order.side_effect = flaky_place_order
        monkeypatch.setattr(bot.dl, "bybit_client_for", lambda acc: client)

        result = bot.close_all_bybit_positions(self._account())
        assert call_count["n"] == 2  # both attempted
        assert "Closed 1" in result
        assert "Failed: 1" in result
        assert "exchange error" in result


class TestCmdCloseallFailureIsolation:
    """closeall — one account raising must not block the other.

    With S-005 M3, the direct no-arg /closeall command now shows an inline
    keyboard. The "close all accounts" path lives in the callback handler
    under closeall:all, so failure-isolation is tested there.
    """

    def _make_query(self, data):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message.chat.id = 12345
        upd = MagicMock()
        upd.callback_query = query
        upd.effective_chat = None
        return upd, query

    def _run(self, coro):
        import asyncio
        asyncio.get_event_loop().run_until_complete(coro)

    def test_one_account_raises_other_still_sends(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        accounts = [
            {"account_id": "bybit-a", "exchange": "bybit", "env_path": ""},
            {"account_id": "bybit-b", "exchange": "bybit", "env_path": ""},
        ]
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: accounts)

        def fake_closeall(account):
            if account["account_id"] == "bybit-a":
                raise RuntimeError("network error")
            return "🟢 bybit-b: No open positions to close."

        monkeypatch.setattr(bot, "close_all_bybit_positions", fake_closeall)
        upd, query = self._make_query("closeall:all")
        self._run(bot.callback_handler(upd, MagicMock()))

        # Final edit_message_text call contains results from both accounts
        sent = query.edit_message_text.call_args.args[0]
        assert "bybit-a" in sent and "network error" in sent
        assert "bybit-b" in sent


# ---------------------------------------------------------------------------
# S-003 N1-c: account-aware /log and /toggle (cmd + callback_handler)
# ---------------------------------------------------------------------------

class TestCmdLogMultiAccount:
    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def _accounts(self):
        return [
            {"account_id": "live",  "exchange": "bybit",   "env_path": "",
             "service": "ict-trader-live"},
            {"account_id": "alpha", "exchange": "binance", "env_path": "",
             "service": "ict-trader-alpha"},
        ]

    def test_sends_one_message_per_account(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: self._accounts())
        monkeypatch.setattr(bot, "_account_env", lambda acc: {"STRATEGY": "ict"})
        log_calls = []

        def fake_logs(svc, n=20):
            log_calls.append(svc)
            return f"log output for {svc}"

        monkeypatch.setattr(bot.dl, "recent_logs_for", fake_logs)

        upd = self._make_update()
        self._run(bot.cmd_log(upd, MagicMock()))

        assert "ict-trader-live" in log_calls
        assert "ict-trader-alpha" in log_calls
        msgs = [c.args[0] for c in upd.message.reply_text.call_args_list]
        assert len(msgs) == 2
        assert any("ict-trader-live" in m for m in msgs)
        assert any("ict-trader-alpha" in m for m in msgs)

    def test_no_accounts_falls_back_to_live_service(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot, "get_last_logs", lambda lines=20: "fallback log")

        upd = self._make_update()
        self._run(bot.cmd_log(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "fallback log" in sent

    def test_one_account_log_failure_does_not_block_others(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: self._accounts())
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})

        def boom(svc, n=20):
            if svc == "ict-trader-live":
                raise RuntimeError("journalctl error")
            return "alpha log ok"

        monkeypatch.setattr(bot.dl, "recent_logs_for", boom)

        upd = self._make_update()
        self._run(bot.cmd_log(upd, MagicMock()))

        msgs = [c.args[0] for c in upd.message.reply_text.call_args_list]
        assert any("journalctl error" in m for m in msgs)
        assert any("alpha log ok" in m for m in msgs)


class TestCmdToggleMultiAccount:
    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def _accounts(self):
        return [
            {"account_id": "live",  "exchange": "bybit",   "env_path": "",
             "service": "ict-trader-live"},
            {"account_id": "alpha", "exchange": "binance", "env_path": "",
             "service": "ict-trader-alpha"},
        ]

    def test_toggles_each_account_service(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: self._accounts())
        toggle_calls = []

        def fake_toggle(svc, action):
            toggle_calls.append((svc, action))
            return f"✅ `{svc}` {action}ed. Status: `inactive`"

        monkeypatch.setattr(bot, "get_service_status", lambda svc: "active")
        monkeypatch.setattr(bot, "toggle_service", fake_toggle)

        upd = self._make_update()
        self._run(bot.cmd_toggle(upd, MagicMock()))

        assert ("ict-trader-live", "stop") in toggle_calls
        assert ("ict-trader-alpha", "stop") in toggle_calls
        assert upd.message.reply_text.call_count == 2

    def test_start_when_inactive(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live", "service": "ict-trader-live", "env_path": ""}
        ])
        toggle_calls = []

        def fake_toggle(svc, action):
            toggle_calls.append((svc, action))
            return f"✅ `{svc}` {action}ed."

        monkeypatch.setattr(bot, "get_service_status", lambda svc: "inactive")
        monkeypatch.setattr(bot, "toggle_service", fake_toggle)

        upd = self._make_update()
        self._run(bot.cmd_toggle(upd, MagicMock()))

        assert toggle_calls == [("ict-trader-live", "start")]

    def test_no_accounts_falls_back_to_live_service(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        toggle_calls = []

        def fake_toggle(svc, action):
            toggle_calls.append((svc, action))
            return f"✅ `{svc}` {action}ed."

        monkeypatch.setattr(bot, "get_service_status", lambda svc: "active")
        monkeypatch.setattr(bot, "toggle_service", fake_toggle)
        monkeypatch.setattr(bot, "LIVE_SERVICE_NAME", "ict-trader-live")

        upd = self._make_update()
        self._run(bot.cmd_toggle(upd, MagicMock()))

        assert toggle_calls == [("ict-trader-live", "stop")]


class TestCallbackHandlerLogToggleMultiAccount:
    def _make_query(self, data):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message.chat.id = 12345
        upd = MagicMock()
        upd.callback_query = query
        upd.effective_chat = None
        return upd, query

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def _accounts(self):
        return [
            {"account_id": "live",  "env_path": "",
             "service": "ict-trader-live"},
            {"account_id": "alpha", "env_path": "",
             "service": "ict-trader-alpha"},
        ]

    def test_log_callback_concatenates_all_accounts(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: self._accounts())
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot.dl, "recent_logs_for",
                            lambda svc, n=10: f"log:{svc}")

        upd, query = self._make_query("log")
        self._run(bot.callback_handler(upd, MagicMock()))

        sent = query.edit_message_text.call_args.args[0]
        assert "ict-trader-live" in sent
        assert "ict-trader-alpha" in sent

    def test_log_callback_fallback_when_no_accounts(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        monkeypatch.setattr(bot, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot, "get_last_logs", lambda lines=20: "fallback")

        upd, query = self._make_query("log")
        self._run(bot.callback_handler(upd, MagicMock()))

        sent = query.edit_message_text.call_args.args[0]
        assert "fallback" in sent

    def test_toggle_callback_aggregates_all_accounts(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: self._accounts())
        toggle_calls = []

        def fake_toggle(svc, action):
            toggle_calls.append(svc)
            return f"✅ `{svc}` {action}ed."

        monkeypatch.setattr(bot, "get_service_status", lambda svc: "active")
        monkeypatch.setattr(bot, "toggle_service", fake_toggle)

        upd, query = self._make_query("toggle")
        self._run(bot.callback_handler(upd, MagicMock()))

        assert "ict-trader-live" in toggle_calls
        assert "ict-trader-alpha" in toggle_calls
        sent = query.edit_message_text.call_args.args[0]
        assert "ict-trader-live" in sent
        assert "ict-trader-alpha" in sent

    def test_toggle_callback_fallback_when_no_accounts(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        monkeypatch.setattr(bot, "get_service_status", lambda svc: "active")
        monkeypatch.setattr(bot, "toggle_service",
                            lambda svc, act: f"✅ `{svc}` {act}ed.")
        monkeypatch.setattr(bot, "LIVE_SERVICE_NAME", "ict-trader-live")

        upd, query = self._make_query("toggle")
        self._run(bot.callback_handler(upd, MagicMock()))

        sent = query.edit_message_text.call_args.args[0]
        assert "ict-trader-live" in sent


# ---------------------------------------------------------------------------
# S-005 M3 — TestCmdCloseallStrategy
# ---------------------------------------------------------------------------

class TestCmdCloseallStrategy:
    """Tests for per-strategy /closeall <strategy> command and inline callback."""

    def _make_update(self, args=None):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = args or []
        return upd, ctx

    def _make_query(self, data):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message.chat.id = 12345
        upd = MagicMock()
        upd.callback_query = query
        upd.effective_chat = None
        return upd, query

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    def _bybit_account(self, aid, strategies):
        return {
            "account_id": aid,
            "exchange": "bybit",
            "env_path": None,
            "service": f"ict-trader-{aid}",
            "strategies": strategies,
            "source": "env",
        }

    def test_cmd_closeall_with_strategy_arg_calls_strategy_filter(self, monkeypatch):
        """'/closeall vwap' only processes accounts that run vwap."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        vwap_acc = self._bybit_account("vwap-acct", ["vwap"])
        ict_acc = self._bybit_account("ict-acct", ["ict"])
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [vwap_acc, ict_acc])

        closed_for = []

        def fake_close(account, strategy_name):
            closed_for.append((account["account_id"], strategy_name))
            return f"✅ Closed {account['account_id']}"

        monkeypatch.setattr(bot.dl, "close_all_bybit_positions_for_strategy", fake_close)

        upd, ctx = self._make_update(args=["vwap"])
        self._run(bot.cmd_closeall(upd, ctx))

        assert closed_for == [("vwap-acct", "vwap"), ("ict-acct", "vwap")]

    def test_cmd_closeall_with_strategy_arg_skips_non_matching(self, monkeypatch):
        """When close_all_bybit_positions_for_strategy returns None, no message sent for that account."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        vwap_acc = self._bybit_account("vwap-acct", ["vwap"])
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [vwap_acc])

        def fake_close(account, strategy_name):
            if strategy_name == "ict":
                return None  # account doesn't run ict
            return "✅ closed"

        monkeypatch.setattr(bot.dl, "close_all_bybit_positions_for_strategy", fake_close)

        upd, ctx = self._make_update(args=["ict"])
        self._run(bot.cmd_closeall(upd, ctx))

        # Reply must say no accounts configured for ict
        sent = upd.message.reply_text.call_args.args[0]
        assert "ict" in sent.lower()

    def test_cmd_closeall_no_args_sends_inline_keyboard(self, monkeypatch):
        """'/closeall' with no args must reply with an InlineKeyboardMarkup."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            self._bybit_account("a1", ["vwap"])
        ])
        monkeypatch.setattr(bot.dl, "list_live_strategies",
                            lambda: ["breakout_confirmation", "vwap", "ict"])

        # Replace InlineKeyboardMarkup with a simple sentinel factory so
        # the list-of-rows argument doesn't confuse MagicMock's spec logic.
        class _FakeKeyboard:
            def __init__(self, rows):
                self.rows = rows

        monkeypatch.setattr(bot, "InlineKeyboardMarkup", _FakeKeyboard)

        upd, ctx = self._make_update(args=[])
        self._run(bot.cmd_closeall(upd, ctx))

        call_kwargs = upd.message.reply_text.call_args
        assert call_kwargs is not None
        # reply_markup keyword must have been passed
        assert "reply_markup" in (call_kwargs.kwargs or {})

    def test_callback_closeall_strategy_dispatches_to_strategy_filter(self, monkeypatch):
        """Inline button 'closeall:vwap' calls per-strategy close helper."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")

        closed_strategy = []

        async def fake_do_closeall(reply_fn, strategy_name):
            closed_strategy.append(strategy_name)
            await reply_fn(f"closed {strategy_name}")

        monkeypatch.setattr(bot, "_do_closeall_strategy", fake_do_closeall)

        upd, query = self._make_query("closeall:vwap")
        self._run(bot.callback_handler(upd, MagicMock()))

        assert closed_strategy == ["vwap"]


# ---------------------------------------------------------------------------
# S-005 M4 — TestCmdStrategiesMultiAccount
# ---------------------------------------------------------------------------

class TestCmdStrategiesMultiAccount:
    """Tests for /strategies dashboard command (S-005 M4)."""

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    def _dashboard_rows(self, strategies=None):
        strategies = strategies or ["breakout_confirmation", "vwap", "ict"]
        return [
            {"strategy": s, "signals_today": i + 1,
             "pnl": (i - 1) * 50.0, "open_pos": i, "status": "active"}
            for i, s in enumerate(strategies)
        ]

    def test_cmd_strategies_sends_dashboard_message(self, monkeypatch):
        """'/strategies' replies with a formatted dashboard message."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        rows = self._dashboard_rows()
        monkeypatch.setattr(bot.dl, "strategy_dashboard_data", lambda: rows)

        upd = self._make_update()
        self._run(bot.cmd_strategies(upd, MagicMock()))

        assert upd.message.reply_text.called
        sent = upd.message.reply_text.call_args.args[0]
        assert "Strategy Dashboard" in sent

    def test_dashboard_contains_all_strategies(self, monkeypatch):
        """All strategies from dashboard_data appear in the message."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        rows = self._dashboard_rows(["breakout_confirmation", "vwap", "ict", "killzone"])
        monkeypatch.setattr(bot.dl, "strategy_dashboard_data", lambda: rows)

        upd = self._make_update()
        self._run(bot.cmd_strategies(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        for strategy in ["breakout_confirmation", "vwap", "ict", "killzone"]:
            assert strategy in sent, f"'{strategy}' missing from dashboard"

    def test_dashboard_shows_signals_pnl_open_pos(self, monkeypatch):
        """Dashboard message includes signals, PnL, and open positions."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "strategy_dashboard_data", lambda: [
            {"strategy": "vwap", "signals_today": 7,
             "pnl": -25.50, "open_pos": 2, "status": "active"},
        ])

        upd = self._make_update()
        self._run(bot.cmd_strategies(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "7" in sent        # signals_today
        assert "25.50" in sent    # pnl magnitude
        assert "2" in sent        # open_pos

    def test_dashboard_positive_pnl_prefixed_plus(self, monkeypatch):
        """Positive PnL must be prefixed with '+'."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "strategy_dashboard_data", lambda: [
            {"strategy": "breakout_confirmation", "signals_today": 3,
             "pnl": 100.0, "open_pos": 0, "status": "active"},
        ])

        upd = self._make_update()
        self._run(bot.cmd_strategies(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "+$100.00" in sent

    def test_dashboard_empty_strategies_shows_fallback(self, monkeypatch):
        """When strategy_dashboard_data returns [], a fallback message is shown."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "strategy_dashboard_data", lambda: [])

        upd = self._make_update()
        self._run(bot.cmd_strategies(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "No strategies" in sent

    def test_unauthorised_request_ignored(self, monkeypatch):
        """Unauthorised chat must not receive a reply."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "99999")

        upd = self._make_update()  # chat_id=12345 ≠ 99999
        self._run(bot.cmd_strategies(upd, MagicMock()))

        upd.message.reply_text.assert_not_called()


# _format_strategies_dashboard unit tests

def test_format_strategies_dashboard_renders_all_fields():
    rows = [
        {"strategy": "vwap", "service": "ict-trader-vwap", "model": None,
         "signals_today": 5, "pnl": -10.0, "open_pos": 1, "status": "active"},
    ]
    text = bot._format_strategies_dashboard(rows)
    assert "vwap" in text
    assert "ict-trader-vwap" in text   # S-007: service shown
    assert "5" in text
    assert "10.00" in text
    assert "1" in text


def test_format_strategies_dashboard_shows_model_when_present():
    rows = [
        {"strategy": "breakout_confirmation", "service": "ict-trader-breakout",
         "model": "btc_v1.joblib", "signals_today": 0, "pnl": 0.0,
         "open_pos": 0, "status": "active"},
    ]
    text = bot._format_strategies_dashboard(rows)
    assert "btc_v1.joblib" in text


def test_format_strategies_dashboard_no_model_line_when_none():
    rows = [
        {"strategy": "vwap", "service": "ict-trader-vwap", "model": None,
         "signals_today": 0, "pnl": 0.0, "open_pos": 0, "status": "active"},
    ]
    text = bot._format_strategies_dashboard(rows)
    assert "🧠" not in text


def test_format_strategies_dashboard_empty():
    assert "No strategies" in bot._format_strategies_dashboard([])
