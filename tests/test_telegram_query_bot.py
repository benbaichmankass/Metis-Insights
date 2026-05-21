"""
Smoke tests for src/bot/telegram_query_bot.py.

Tests target pure-Python helper functions that carry no telegram dependency:
is_halted, is_authorised, get_strategy_label,
format_backtest_summary, fetch_today_pnl, fetch_open_positions_count.

Heavy deps (telegram, pybit) are stubbed at sys.modules level before import.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Stub heavy deps before any src import
# ---------------------------------------------------------------------------
for _mod in (
    "telegram",
    "telegram.ext",
    "telegram.error",
    "dotenv",
    "requests",
    "pybit",
    "pybit.unified_trading",
    "src.runtime.signal_notifications",
):
    sys.modules.setdefault(_mod, MagicMock())

# S-027 PR2 — comms_handler imports ``filters`` from telegram.ext and
# ``TelegramError`` from telegram.error. Provide the attributes the
# import path needs; the actual handler logic is not exercised by this
# test module.
sys.modules["telegram.error"].TelegramError = type("TelegramError", (Exception,), {})
sys.modules["telegram.ext"].filters = MagicMock()
sys.modules["telegram.ext"].MessageHandler = MagicMock

# Provide realistic dotenv stubs
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

# telegram.Update must be importable as a class
_tg_mock = sys.modules["telegram"]
_tg_mock.Update = MagicMock


# G2 — BotCommand stand-in. The real telegram.BotCommand stores `command` and
# `description` as attributes; tests in TestHelpCommandParity read both.
# A bare MagicMock would auto-generate them as MagicMock attributes, so we
# need a tiny class that preserves the args.
class _FakeBotCommand:
    def __init__(self, command, description=""):
        self.command = command
        self.description = description

    def __repr__(self):
        return f"BotCommand(command={self.command!r}, description={self.description!r})"


_tg_mock.BotCommand = _FakeBotCommand
_tg_mock.InlineKeyboardButton = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix
_tg_mock.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix
_tg_ext_mock = sys.modules["telegram.ext"]
_tg_ext_mock.Application = MagicMock
_tg_ext_mock.CommandHandler = MagicMock
_tg_ext_mock.CallbackQueryHandler = MagicMock
_tg_ext_mock.ContextTypes = MagicMock()
_tg_ext_mock.ContextTypes.DEFAULT_TYPE = object

import src.bot.telegram_query_bot as bot  # noqa: E402
# get_strategy_label lives in trade_notifier and resolves _account_env in
# that module's namespace (it's only re-exported through `bot`), so tests
# must patch trade_notifier._account_env, not bot._account_env (which was
# removed when the helper moved). See src/bot/trade_notifier.py.
import src.bot.trade_notifier as trade_notifier  # noqa: E402


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
        monkeypatch.setattr(trade_notifier, "_account_env", lambda _acct: env_vars)
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
    # win_rate and max_drawdown_pct are stored as REAL in the DB
    # (src/backtest/run_backtest.py schema: win_rate REAL).  The
    # formatter uses :.1f% so string values like "60%" would crash.
    row = {
        "id": 1, "run_date": "2026-04-28", "strategy_version": "v1.0",
        "start_date": "2026-01-01", "end_date": "2026-04-01",
        "total_trades": 50, "winning_trades": 30, "losing_trades": 20,
        "win_rate": 60.0, "profit_factor": 1.8, "expectancy": 25.0,
        "max_drawdown": 500.0, "max_drawdown_pct": 5.0, "sharpe_ratio": 1.2,
        "total_pnl": 1250.0, "total_pnl_pct": "12.5%",
        "avg_win": 80.0, "avg_loss": 40.0,
        "largest_win": 300.0, "largest_loss": 150.0,
        "created_at": "2026-04-28T00:00:00",
    }
    summary = bot.format_backtest_summary(row)
    assert "50" in summary          # total_trades
    assert "60" in summary          # win_rate (rendered as "60.0%")
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
        # fetch_today_pnl delegates to processor.get_today_pnl which
        # reads TRADE_JOURNAL_DB env var (bot.DB_PATH is no longer used).
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        count, pnl = bot.fetch_today_pnl()
        # query counts all non-backtest live rows (open + closed)
        assert count == 3           # two closed + one open live trade
        assert abs(pnl - 70.0) < 0.01  # 100 + (-30) + 0 = 70

    def test_returns_zeros_on_missing_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "no.db"))
        count, pnl = bot.fetch_today_pnl()
        assert count == 0
        assert pnl == 0.0


class TestFetchOpenPositionsCount:
    def test_counts_open_live_trades(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        count = bot.fetch_open_positions_count()
        assert count == 1

    def test_returns_zero_on_missing_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "no.db"))
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
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)

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
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        count, pnl = bot.fetch_today_pnl()
        # 5 live rows total across both accounts (backtest excluded)
        assert count == 5
        assert abs(pnl - 225.0) < 0.01  # 200 - 50 + 0 + 75 + 0

    def test_unknown_account_id_returns_zeros(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        count, pnl = bot.fetch_today_pnl(account_id="nonexistent")
        assert count == 0
        assert pnl == 0.0

    def test_missing_db_returns_zeros(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "no.db"))
        count, pnl = bot.fetch_today_pnl(account_id="live")
        assert count == 0
        assert pnl == 0.0


class TestFetchOpenPositionsCountPerAccount:
    def test_filters_by_account_id(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)

        assert bot.fetch_open_positions_count(account_id="live") == 1
        assert bot.fetch_open_positions_count(account_id="alpha") == 1

    def test_no_account_filter_returns_aggregate(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        assert bot.fetch_open_positions_count() == 2

    def test_unknown_account_id_returns_zero(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        assert bot.fetch_open_positions_count(account_id="ghost") == 0

    def test_missing_db_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "no.db"))
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
        """S-telegram-format Phase 4: cmd_status now renders as
        collapsable HTML sections — one per account. The systemd unit
        name no longer appears (was already dropped per S-016 H1
        audit comment in the legacy renderer; the new shape stays
        consistent with that rule)."""
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag"))
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {"STRATEGY": "ict"})
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
        # Two account sections + kill-switch + bot status — at least
        # 4 collapsable blockquotes total.
        assert sent.count("<blockquote expandable>") >= 4
        # HTML-mode envelope (bold header) replaces the legacy
        # Markdown ``*ICT Trading Bot Status*``.
        assert "<b>✅ ICT Trading Bot Status</b>" in sent
        # Per-account summary line names trades/PnL/open. (Specific
        # dollar figures are not pinned because fetch_today_pnl()'s
        # timezone behaviour differs in this sandbox vs the test
        # fixture's timestamps; that quirk pre-dates this PR.)
        assert "trades" in sent and "open" in sent

    def test_no_accounts_falls_back_to_aggregate(self, tmp_path, monkeypatch):
        db_path = _make_db_with_accounts(tmp_path)
        monkeypatch.setattr(bot, "DB_PATH", db_path)
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag"))
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot, "get_service_status", lambda svc: "inactive")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])

        upd = self._make_update()
        self._run(bot.cmd_status(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "ICT Trading Bot Status" in sent
        # Aggregate sentinel — the renderer's no-accounts fallback
        # appends the ``(aggregate)`` marker so the operator can see
        # the figure isn't per-account.
        assert "(aggregate)" in sent

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
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})

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
        # win_rate / max_drawdown_pct stored as REAL in the DB schema
        row = {
            "id": 7, "run_date": "2026-04-29", "strategy_version": "vX",
            "start_date": "2026-04-01", "end_date": "2026-04-28",
            "total_trades": 10, "winning_trades": 6, "losing_trades": 4,
            "win_rate": 60.0, "profit_factor": 1.5, "expectancy": 12.0,
            "max_drawdown": 100.0, "max_drawdown_pct": 3.0,
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
        # format_bybit_balance moved to trade_notifier (not re-exported to bot).
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {"STRATEGY": "vwap"})
        monkeypatch.setattr(bot.dl, "account_balance", lambda acc: {
            "total_usdt": 1234.0,
            "raw": {"result": {"list": [{"coin": [
                {"coin": "USDT", "walletBalance": "1000.5", "usdValue": "1000.5"},
                {"coin": "BTC",  "walletBalance": "0.0123", "usdValue": "720.0"},
                {"coin": "ETH",  "walletBalance": "0",      "usdValue": "0"},  # filtered
            ]}]}},
        })
        out = trade_notifier.format_bybit_balance(self._account())
        # CP-2026-05-02: account-first labelling. Account_id leads,
        # strategy is parenthetical so two accounts that share a
        # single strategy don't render with identical headers.
        assert "live" in out
        assert "VWAP" in out
        assert "Balance" in out
        assert "USDT: 1000.5000" in out
        assert "BTC: 0.0123" in out
        assert "ETH:" not in out  # zero-balance row dropped

    def test_returns_unavailable_when_loader_returns_none(self, monkeypatch):
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot.dl, "account_balance", lambda acc: None)
        out = trade_notifier.format_bybit_balance(self._account())
        assert "⚠️" in out and "unavailable" in out


class TestFormatBybitPositions:
    def _account(self):
        return {"account_id": "live", "exchange": "bybit", "env_path": ""}

    def test_renders_normalized_rows(self, monkeypatch):
        # format_bybit_positions moved to trade_notifier (not re-exported to bot).
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {"STRATEGY": "ict"})
        monkeypatch.setattr(bot.dl, "account_open_positions", lambda acc: [
            {"symbol": "BTCUSDT", "side": "Buy", "size": 0.05,
             "entry_price": 50000.0, "unrealised_pnl": 12.34},
        ])
        out = trade_notifier.format_bybit_positions(self._account())
        assert "ICT Positions" in out
        assert "BTCUSDT Buy" in out
        assert "Entry: $50,000.00" in out
        assert "PnL: $+12.34" in out

    def test_empty_list_renders_no_open(self, monkeypatch):
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot.dl, "account_open_positions", lambda acc: [])
        out = trade_notifier.format_bybit_positions(self._account())
        assert "No open positions" in out

    def test_none_renders_unavailable(self, monkeypatch):
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot.dl, "account_open_positions", lambda acc: None)
        out = trade_notifier.format_bybit_positions(self._account())
        assert "⚠️" in out and "unavailable" in out


class TestFormatBinanceBalance:
    def _account(self):
        return {"account_id": "alpha", "exchange": "binance", "env_path": ""}

    def test_renders_total_free_used(self, monkeypatch):
        # format_binance_balance moved to trade_notifier (not re-exported to bot).
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {"STRATEGY": "breakout"})
        monkeypatch.setattr(bot.dl, "account_balance", lambda acc: {
            "total_usdt": 500.0,
            "raw": {"USDT": {"total": 500.0, "free": 480.0, "used": 20.0}},
        })
        out = trade_notifier.format_binance_balance(self._account())
        # CP-2026-05-02: account-first labelling. Strategy is parenthetical.
        assert "alpha" in out
        assert "Breakout" in out
        assert "Balance" in out
        assert "(Binance)" in out
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
        # cmd_balance dispatches via trade_notifier._render_account_balance
        # which calls trade_notifier.format_bybit_balance /
        # format_binance_balance.  Patching bot.format_X no longer works
        # because those symbols were never re-exported to bot.
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live",  "exchange": "bybit",   "env_path": ""},
            {"account_id": "alpha", "exchange": "binance", "env_path": ""},
        ])
        monkeypatch.setattr(trade_notifier, "format_bybit_balance", lambda acc: "BYBIT-BLOCK")
        monkeypatch.setattr(trade_notifier, "format_binance_balance", lambda acc: "BINANCE-BLOCK")

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

    def test_emits_dup_key_warning_when_two_accounts_share_a_key(self, monkeypatch):
        # CP-2026-05-02-03: directly answers the operator's "both
        # accounts show the same balance" complaint. When the resolved
        # API key is the same across two accounts, /balance prepends a
        # banner naming the offending accounts and explains the fix.
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "bybit_1", "exchange": "bybit",
             "api_key_env": "BYBIT_API_KEY_1",
             "strategies": ["turtle_soup"]},
            {"account_id": "bybit_2", "exchange": "bybit",
             "api_key_env": "BYBIT_API_KEY_2",
             "strategies": ["vwap"]},
        ])
        # Both env vars resolve to the same key value — the exact
        # situation the operator is in.
        monkeypatch.setenv("BYBIT_API_KEY_1", "AAAAAAAAAAAA9999")
        monkeypatch.setenv("BYBIT_API_SECRET_1", "secret-1")
        monkeypatch.setenv("BYBIT_API_KEY_2", "AAAAAAAAAAAA9999")
        monkeypatch.setenv("BYBIT_API_SECRET_2", "secret-2")
        monkeypatch.setattr(trade_notifier, "format_bybit_balance", lambda acc: "BLOCK")

        upd = self._make_update()
        self._run(bot.cmd_balance(upd, MagicMock()))
        sent = upd.message.reply_text.call_args.args[0]
        assert "DUPLICATE API KEY DETECTED" in sent
        assert "bybit_1" in sent and "bybit_2" in sent
        assert "…9999" in sent

    def test_no_dup_warning_when_keys_differ(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "bybit_1", "exchange": "bybit",
             "api_key_env": "BYBIT_API_KEY_1",
             "strategies": ["turtle_soup"]},
            {"account_id": "bybit_2", "exchange": "bybit",
             "api_key_env": "BYBIT_API_KEY_2",
             "strategies": ["vwap"]},
        ])
        monkeypatch.setenv("BYBIT_API_KEY_1", "AAAAAAAAAAAA1111")
        monkeypatch.setenv("BYBIT_API_SECRET_1", "secret-1")
        monkeypatch.setenv("BYBIT_API_KEY_2", "BBBBBBBBBBBB2222")
        monkeypatch.setenv("BYBIT_API_SECRET_2", "secret-2")
        monkeypatch.setattr(trade_notifier, "format_bybit_balance", lambda acc: "BLOCK")

        upd = self._make_update()
        self._run(bot.cmd_balance(upd, MagicMock()))
        sent = upd.message.reply_text.call_args.args[0]
        assert "DUPLICATE API KEY DETECTED" not in sent


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
        """S-telegram-format Phase 4: cmd_trades now wraps the
        per-account formatter output in a collapsable section. The
        formatter's body must still appear in the rendered HTML."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live", "exchange": "bybit", "env_path": ""},
        ])
        # format_bybit_positions moved to trade_notifier; bot no longer
        # re-exports it. cmd_trades dispatches via _render_account_positions.
        monkeypatch.setattr(trade_notifier, "format_bybit_positions", lambda acc: "POSITIONS-OK")

        upd = self._make_update()
        self._run(bot.cmd_trades(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "POSITIONS-OK" in sent
        assert "<blockquote expandable>" in sent


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
        # All format_* helpers live in trade_notifier; bot does not re-export them.
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live",  "exchange": "bybit",   "env_path": ""},
            {"account_id": "alpha", "exchange": "binance", "env_path": ""},
        ])

        def boom(_acc):
            raise RuntimeError("nope")

        monkeypatch.setattr(trade_notifier, "format_bybit_balance", boom)
        monkeypatch.setattr(trade_notifier, "format_binance_balance",
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

        monkeypatch.setattr(trade_notifier, "format_binance_positions", boom)
        monkeypatch.setattr(trade_notifier, "format_bybit_positions",
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

    def test_format_trade_row_handles_markdown_special_chars(self):
        """Regression: DB columns with *, _, [, ` must not crash the renderer.

        Telegram's legacy Markdown parser rejected unbalanced entities and
        produced ``Can't parse entities: can't find end of the entity`` on
        /last5. Output is now plain text, so the function must accept any
        string content without raising.
        """
        row = self._trade_row(42)
        row["notes"] = "saw a [bug] in *VWAP* with `weird` _name_"
        row["entry_reason"] = "FVG_fill `H1` *bullish*"
        row["exit_reason"] = "TP1 hit [partial] _scaled_"
        row["setup_type"] = "ICT *Silver* `bullet`"

        out = bot._format_trade_row(row)
        assert "Trade #42" in out
        assert "[bug]" in out and "*VWAP*" in out and "`weird`" in out
        assert "_name_" in out

    def test_last5_does_not_use_markdown_parse_mode(self, monkeypatch):
        """cmd_last5 must not use parse_mode='Markdown' so DB-sourced
        text containing *, _, [, ` no longer crashes Telegram.

        S-telegram-format Phase 3 migrated /last5 to a single collapsable
        HTML message (parse_mode='HTML') — still safe because HTML-escaping
        neutralises all markdown-special characters.
        """
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [
            {"account_id": "live", "exchange": "bybit", "env_path": ""},
        ])
        nasty = self._trade_row(99)
        nasty["notes"] = "edge*case_[stuff]`"
        monkeypatch.setattr(
            bot.dl, "recent_trades_for", lambda acc, n=5: [nasty])
        monkeypatch.setattr(bot.os.path, "exists", lambda _p: False)

        upd = self._make_update()
        self._run(bot.cmd_last5(upd, MagicMock()))

        trade_calls = [
            c for c in upd.message.reply_text.call_args_list
            if "Trade #99" in c.args[0]
        ]
        assert trade_calls, "expected the trade row to be rendered"
        for call in trade_calls:
            assert call.kwargs.get("parse_mode") != "Markdown", (
                "trade rows must not be sent with parse_mode='Markdown'; "
                "DB content can contain unescaped Markdown specials"
            )


# ---------------------------------------------------------------------------
# G2 — hamburger menu (set_my_commands) ↔ /help parity
# ---------------------------------------------------------------------------


class TestHelpCommandParity:
    """The Telegram command-menu (BOT_COMMANDS) must mirror the union of
    /help category drill-downs 1:1.

    G3 — /help is now a button-driven menu. cmd_start replies with the
    category buttons; tapping a category edits the message to that
    category's drill-down. Parity is therefore checked against the
    concatenation of every category render, not against cmd_start's
    top-level text (which intentionally lists no commands).

    Failure modes the test catches:
      1. A command in BOT_COMMANDS that's missing from every drill-down.
      2. A command in some drill-down that's missing from BOT_COMMANDS.
      3. Order drift between the two (Telegram displays the menu in the
         order set_my_commands receives it; the union of drill-downs in
         category-display order should match).
      4. A description longer than 80 chars (Telegram truncates ugly).
      5. A registered CommandHandler that has no spec.
    """

    META_NAMES = {"start", "help"}

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_every_bot_command_appears_in_some_category(self):
        all_cmds = bot._commands_across_help_categories()
        for bc in bot.BOT_COMMANDS:
            if bc.command in self.META_NAMES:
                continue
            assert bc.command in all_cmds, (
                f"/{bc.command} is in BOT_COMMANDS but missing from every "
                f"/help category drill-down"
            )

    def test_every_help_command_appears_in_bot_commands(self):
        cmd_names = {bc.command for bc in bot.BOT_COMMANDS}
        for name in bot._commands_across_help_categories():
            assert name in cmd_names, (
                f"/{name} appears in a /help drill-down but is missing "
                f"from BOT_COMMANDS"
            )

    def test_help_and_bot_commands_share_order(self):
        """The hamburger menu order must match the union of drill-downs.

        ``set_my_commands`` is what Telegram displays in the chat
        composer; the union of drill-downs is what operators see when
        navigating /help with buttons. Both must reflect BOT_COMMAND_SPECS
        in the same order, modulo the meta /start /help aliases (which
        live at the top of BOT_COMMANDS for discoverability but are not
        in any drill-down body).
        """
        cmds_in_help = bot._commands_across_help_categories()
        bot_cmd_names = [bc.command for bc in bot.BOT_COMMANDS
                         if bc.command not in self.META_NAMES]
        assert cmds_in_help == bot_cmd_names, (
            "Order mismatch:\n"
            f"  /help drill-downs: {cmds_in_help}\n"
            f"  BOT_COMMANDS:      {bot_cmd_names}"
        )

    def test_cmd_start_replies_with_category_buttons(self, monkeypatch):
        """The first reply to /help should be the top-level menu with one
        button per category — not a wall of text."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd = self._make_update()
        self._run(bot.cmd_help(upd, MagicMock()))
        kwargs = upd.message.reply_text.call_args.kwargs
        kb = kwargs.get("reply_markup")
        assert kb is not None, (
            "cmd_help must reply with an InlineKeyboardMarkup of category "
            "buttons (G3 — /help is button-driven now)"
        )
        # Top-level greeting should not list every command — that defeats
        # the purpose of the category menu.
        text = upd.message.reply_text.call_args.args[0]
        assert "/status" not in text, (
            "cmd_help top-level should not list every command — only "
            "category buttons. Found '/status' in the top message."
        )

    def test_render_help_category_lists_category_commands(self):
        """Each category render lists exactly the commands in that
        category (and only those), in declared order."""
        for cid, _label in bot.HELP_CATEGORIES:
            text, _kb = bot.render_help_category(cid)
            seen = bot._commands_in_help_text(text)
            expected = [s.name for s in bot.BOT_COMMAND_SPECS
                        if s.category == cid]
            assert seen == expected, (
                f"Category '{cid}' drill-down mismatch:\n"
                f"  rendered: {seen}\n"
                f"  expected: {expected}"
            )

    def test_unknown_help_category_returns_back_button(self):
        text, _kb = bot.render_help_category("does_not_exist")
        assert "Unknown" in text or "unknown" in text

    def test_descriptions_are_within_telegram_limits(self):
        for bc in bot.BOT_COMMANDS:
            assert 1 <= len(bc.description) <= 80, (
                f"BotCommand /{bc.command} description "
                f"({len(bc.description)} chars) violates ≤80-char rule: "
                f"{bc.description!r}"
            )

    def test_every_registered_handler_is_in_bot_commands(self):
        """Any operator-facing command-handler registered in main() should
        also appear in BOT_COMMANDS. Catches "registered handler with no
        menu entry" drift at PR time."""
        import inspect
        src = inspect.getsource(bot)
        registered = set(re.findall(r'CommandHandler\("([a-zA-Z0-9_]+)"', src))
        bot_cmd_names = {bc.command for bc in bot.BOT_COMMANDS}
        intentionally_hidden: set[str] = set()
        missing = registered - bot_cmd_names - intentionally_hidden
        assert not missing, (
            f"Handlers registered but missing from BOT_COMMANDS: {sorted(missing)}"
        )


class TestHelpButtonCallbacks:
    """G3 — the inline-button navigation for /help.

    Top-level → category drill-down → back to top-level. Each transition
    edits the original message in place via ``query.edit_message_text``.
    """

    def _make_query(self, data: str):
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

    def test_help_top_callback_renders_category_buttons(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, query = self._make_query("help_top")
        self._run(bot.callback_handler(upd, MagicMock()))
        kwargs = query.edit_message_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None, (
            "help_top callback must edit message with the category-button "
            "InlineKeyboardMarkup"
        )

    def test_help_cat_callback_lists_category_commands(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, query = self._make_query("help_cat:trading")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        # Trading commands should all be listed.
        for name in ("status", "halt", "resume", "closeall", "toggle"):
            assert f"/{name}" in text, (
                f"help_cat:trading drill-down missing /{name}"
            )

    def test_help_cat_callback_unknown_category_warns(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, query = self._make_query("help_cat:does_not_exist")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "Unknown" in text or "unknown" in text

    def test_typed_help_with_category_arg_renders_drilldown(self, monkeypatch):
        """`/help trading` typed in chat should render the trading drill-down
        directly — no menu navigation needed for power users."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.args = ["trading"]
        self._run(bot.cmd_help(upd, ctx))
        text = upd.message.reply_text.call_args.args[0]
        assert "/status" in text and "/halt" in text


# ---------------------------------------------------------------------------
# /hourly Markdown crash regression (BUG-031)
# ---------------------------------------------------------------------------


class TestCmdHourlyReplyMarkdown:
    """``/hourly`` was failing with ``BadRequest: Can't parse entities``
    because its success-reply text contained ``send_via_alert_manager``
    (three underscores → unbalanced italic in legacy Markdown). Same
    shape as BUG-009 (#190 /signals) and BUG-030 (#265 /last5).
    """

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_hourly_routes_through_ui_processor(self, monkeypatch):
        """Sprint 025 T1 — cmd_hourly must consume
        ``src.units.ui.processor.get_hourly_report`` (not the runtime helper
        directly), so the bot and any future UI surface get identical
        text via the same facade."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        captured = {}

        def fake_get_hourly_report(*, now_utc=None, tick_interval_s=900):
            captured["now_utc"] = now_utc
            captured["tick_interval_s"] = tick_interval_s
            return "fake hourly via processor"

        fake_processor = MagicMock()
        fake_processor.get_hourly_report = fake_get_hourly_report
        fake_outcomes = MagicMock()
        fake_outcomes.send_scheduled = lambda msg: captured.setdefault("msg", msg)
        monkeypatch.setitem(sys.modules, "src.units.ui.processor", fake_processor)
        monkeypatch.setitem(sys.modules, "src.runtime.outcomes", fake_outcomes)
        # cmd_hourly uses `from src.units.ui import processor` which resolves
        # the cached attribute on the src.units.ui package (bypassing
        # sys.modules when the real module was already imported by a prior
        # test). Patch the package attribute too so both lookup paths agree.
        import src.units.ui as _ui_pkg
        monkeypatch.setattr(_ui_pkg, "processor", fake_processor)

        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_hourly(upd, ctx))

        assert captured.get("msg") == "fake hourly via processor", (
            f"send_scheduled didn't get the processor's output. captured={captured}"
        )
        assert captured.get("tick_interval_s") == 900
        assert captured.get("now_utc") is not None

    def test_hourly_success_reply_drops_markdown_parse_mode(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setitem(
            sys.modules, "src.runtime.hourly_report",
            MagicMock(build_hourly_report=lambda **kwargs: "fake hourly"),
        )
        monkeypatch.setitem(
            sys.modules, "src.runtime.outcomes",
            MagicMock(send_scheduled=lambda msg: None),
        )

        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_hourly(upd, ctx))

        # The success reply should NOT have parse_mode='Markdown' — its
        # text contains identifiers like 'send_via_alert_manager' that
        # Telegram's legacy Markdown parser breaks on.
        success_calls = [
            c for c in upd.message.reply_text.call_args_list
            if c.args and "Hourly report dispatched" in c.args[0]
        ]
        assert success_calls, (
            f"expected a success-reply, got: "
            f"{[c.args[0] for c in upd.message.reply_text.call_args_list]}"
        )
        for call in success_calls:
            assert call.kwargs.get("parse_mode") is None, (
                "cmd_hourly success reply must not use parse_mode='Markdown' "
                "— text contains underscored identifiers (BUG-031)"
            )


# ---------------------------------------------------------------------------
# G4 — /risk_check inline-button account picker
# ---------------------------------------------------------------------------


class TestCmdRiskCheckButtonFlow:
    """G4 — /risk_check no longer requires a typed account name.

    No-args invocation now replies with an inline keyboard listing every
    configured account; tapping a button edits the message in place to
    that account's risk details. Typed `/risk_check <name>` still works
    as a power-user shortcut and goes through the same renderer.
    """

    def _statuses(self):
        return [
            {"name": "live",  "exchange": "bybit",
             "account_type": "futures", "halted": False,
             "daily_pnl": -25.50, "max_daily_loss_usd": 200.0,
             "daily_loss_remaining": 174.50, "max_pos_size_usd": 5000.0,
             "max_dd_pct": 0.05, "open_positions": 1},
            {"name": "alpha", "exchange": "binance",
             "account_type": "spot", "halted": True,
             "daily_pnl": -210.0, "max_daily_loss_usd": 200.0,
             "daily_loss_remaining": 0.0, "max_pos_size_usd": 1000.0,
             "max_dd_pct": 0.10, "open_positions": 0},
        ]

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _make_query(self, data: str):
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

    def _patch_coord(self, monkeypatch):
        coord = MagicMock()
        coord.accounts_status.return_value = self._statuses()
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        return coord

    def test_no_args_replies_with_account_picker(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_risk_check(upd, ctx))
        kwargs = upd.message.reply_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None, (
            "/risk_check with no args must reply with an account-picker "
            "InlineKeyboardMarkup (G4)"
        )
        text = upd.message.reply_text.call_args.args[0]
        assert "Pick an account" in text

    def test_typed_arg_still_renders_directly(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = ["live"]
        self._run(bot.cmd_risk_check(upd, ctx))
        text = upd.message.reply_text.call_args.args[0]
        assert "Risk Check: live" in text
        assert "🟢 OK" in text

    def test_callback_renders_chosen_account(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd, query = self._make_query("risk_check:alpha")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "Risk Check: alpha" in text
        assert "🔴 HALTED" in text  # alpha is halted in our fixture

    def test_callback_unknown_account_returns_not_found(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd, query = self._make_query("risk_check:does_not_exist")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "not found" in text.lower()

    def test_typed_arg_and_callback_produce_same_render(self, monkeypatch):
        """Typed `/risk_check live` and tapping the live button should
        produce identical text — the renderer is shared between the two
        paths."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        # Typed path
        upd_typed = self._make_update()
        ctx = MagicMock()
        ctx.args = ["live"]
        self._run(bot.cmd_risk_check(upd_typed, ctx))
        typed_text = upd_typed.message.reply_text.call_args.args[0]
        # Button path
        upd_btn, query = self._make_query("risk_check:live")
        self._run(bot.callback_handler(upd_btn, MagicMock()))
        btn_text = query.edit_message_text.call_args.args[0]
        assert typed_text == btn_text

    def test_no_accounts_configured_returns_friendly_message(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = MagicMock()
        coord.accounts_status.return_value = []
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_risk_check(upd, ctx))
        text = upd.message.reply_text.call_args.args[0]
        assert "No accounts" in text


# ---------------------------------------------------------------------------
# Sprint 025 T4 — /accounts mode toggle with confirm step (G4 slice 4)
# ---------------------------------------------------------------------------


class TestCmdAccountsToggleConfirm:
    """No-args /accounts replies with the listing + per-account toggle
    keyboard. Tap → "❓ Confirm flip" prompt with explicit Confirm /
    Cancel buttons. ONLY the second tap actually calls
    coord.set_account_dry_run, so a single accidental tap can't flip
    an account between dry and live.

    Typed `/accounts dry|live <name>` is preserved unchanged.
    """

    def _statuses(self, *, live_dry=("live", True), alpha_dry=False):
        return [
            {"name": live_dry[0], "exchange": "bybit", "account_type": "futures",
             "dry_run": live_dry[1], "halted": False,
             "daily_pnl": 0.0, "max_daily_loss_usd": 200.0},
            {"name": "alpha", "exchange": "binance", "account_type": "spot",
             "dry_run": alpha_dry, "halted": False,
             "daily_pnl": 0.0, "max_daily_loss_usd": 100.0},
        ]

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _make_query(self, data: str):
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

    def _patch_coord(self, monkeypatch, statuses=None):
        coord = MagicMock()
        coord.accounts_status.return_value = statuses or self._statuses()
        coord.set_account_dry_run.return_value = {
            "name": "live", "dry_run": True, "mode": "dry",
        }
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        return coord

    def test_no_args_replies_with_listing_and_keyboard(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_accounts(upd, ctx))
        kwargs = upd.message.reply_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None
        text = upd.message.reply_text.call_args.args[0]
        assert "Accounts" in text
        assert "confirm" in text.lower()

    def test_typed_path_still_works_one_shot(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = ["dry", "live"]
        self._run(bot.cmd_accounts(upd, ctx))
        # Typed path applies immediately — single coord call.
        coord.set_account_dry_run.assert_called_once_with("live", True)

    def test_first_tap_does_not_apply_flip(self, monkeypatch):
        """Tapping a toggle button must NOT call coord.set_account_dry_run.
        The first tap only opens the confirmation prompt."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd, query = self._make_query("acct_flip_ask:live:dry")
        self._run(bot.callback_handler(upd, MagicMock()))
        coord.set_account_dry_run.assert_not_called()
        # Edited message asks for confirmation.
        text = query.edit_message_text.call_args.args[0]
        assert "Confirm" in text
        kwargs = query.edit_message_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None

    def test_first_tap_to_live_warns_explicitly(self, monkeypatch):
        """Flipping TO live must include a clear "REAL orders" warning
        in the confirmation prompt — flipping to dry doesn't need
        that warning."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd, query = self._make_query("acct_flip_ask:live:live")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "REAL orders" in text

    def test_second_tap_applies_flip(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd, query = self._make_query("acct_flip_do:live:dry")
        self._run(bot.callback_handler(upd, MagicMock()))
        coord.set_account_dry_run.assert_called_once_with("live", True)
        text = query.edit_message_text.call_args.args[0]
        assert "dry mode" in text or "DRY mode" in text or "🧪" in text

    def test_cancel_button_does_not_apply(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd, query = self._make_query("acct_flip_cancel")
        self._run(bot.callback_handler(upd, MagicMock()))
        coord.set_account_dry_run.assert_not_called()
        text = query.edit_message_text.call_args.args[0]
        assert "Cancel" in text or "cancel" in text

    def test_invalid_target_in_callback_warns(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd, query = self._make_query("acct_flip_do:live:nonsense")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "Invalid flip" in text


# ---------------------------------------------------------------------------
# Sprint 025 T3 — /signals stepper (G4 slice 2)
# ---------------------------------------------------------------------------


class TestCmdSignalsStepper:
    """No-args /signals replies with a two-step stepper:
    1. Pick strategy (vwap / turtle_soup / all).
    2. Pick N (10 / 25 / 50 / 100).
    Final tap renders the audit tail using the existing
    `_format_signal_row` renderer.

    Typed `/signals [N] [strategy]` preserved as power-user shortcut.
    """

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _make_query(self, data: str):
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

    def test_no_args_replies_with_strategy_picker(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_signals(upd, ctx))
        kwargs = upd.message.reply_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None
        text = upd.message.reply_text.call_args.args[0]
        assert "Pick a strategy" in text

    def test_typed_n_arg_renders_directly(self, monkeypatch, tmp_path):
        """/signals 5 should still bypass the stepper and render."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        # Empty audit file → "no signals logged" body, but cmd reaches
        # the render path (not the stepper).
        # SIGNAL_AUDIT_PATH moved to signal_helpers.py and is read via
        # os.environ inside processor.get_signals_block; set the env var.
        audit = tmp_path / "signal_audit.jsonl"
        audit.write_text("", encoding="utf-8")
        monkeypatch.setenv("SIGNAL_AUDIT_PATH", str(audit))
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = ["5"]
        self._run(bot.cmd_signals(upd, ctx))
        text = upd.message.reply_text.call_args.args[0]
        assert "No signals logged" in text or "📡" in text
        # No reply_markup on the typed path — render is final.
        assert upd.message.reply_text.call_args.kwargs.get("reply_markup") is None

    def test_callback_signals_top_returns_strategy_picker(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, query = self._make_query("signals_top")
        self._run(bot.callback_handler(upd, MagicMock()))
        kwargs = query.edit_message_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None

    def test_callback_signals_strat_returns_n_picker(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, query = self._make_query("signals_strat:vwap")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "vwap" in text and "How many" in text
        kwargs = query.edit_message_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None

    def test_callback_signals_strat_all_label(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, query = self._make_query("signals_strat:all")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "all strategies" in text

    def test_callback_signals_n_renders_records(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        audit = tmp_path / "signal_audit.jsonl"
        # Two valid signal rows.
        rows = [
            '{"logged_at_utc":"2026-05-02T10:00:00+00:00","strategy":"vwap",'
            '"symbol":"BTCUSDT","side":"buy","qty":0.001,"status":"submitted"}',
            '{"logged_at_utc":"2026-05-02T10:01:00+00:00","strategy":"vwap",'
            '"symbol":"BTCUSDT","side":"sell","qty":0.001,"status":"dry_run"}',
        ]
        audit.write_text("\n".join(rows) + "\n", encoding="utf-8")
        # SIGNAL_AUDIT_PATH moved to signal_helpers / processor (read via
        # os.environ). Patching bot.SIGNAL_AUDIT_PATH no longer works.
        monkeypatch.setenv("SIGNAL_AUDIT_PATH", str(audit))
        upd, query = self._make_query("signals_n:vwap:10")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "Last 2 signals" in text or "vwap" in text

    def test_callback_signals_n_invalid_int_warns(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, query = self._make_query("signals_n:vwap:abc")
        self._run(bot.callback_handler(upd, MagicMock()))
        text = query.edit_message_text.call_args.args[0]
        assert "Invalid N" in text


# ---------------------------------------------------------------------------
# Sprint 025 T2 — /smoke_test inline-button account picker (G4 slice 3)
# ---------------------------------------------------------------------------


class TestCmdSmokeTestButtonFlow:
    """No-args /smoke_test now replies with an account picker (with a
    🌐 All accounts button); tap → callback runs the smoke and edits
    the message in place. Typed `/smoke_test [account|all]` still
    works as a power-user shortcut."""

    def _statuses(self):
        return [
            {"name": "live",  "exchange": "bybit",  "account_type": "futures"},
            {"name": "alpha", "exchange": "binance", "account_type": "spot"},
        ]

    def _result(self, ok=True, account_ids=("live",)):
        return {
            "ok": ok,
            "smoke_id": "smoke-2026-05-02-001",
            "package": {"symbol": "BTCUSDT", "direction": "long", "qty": 0.0001},
            "results": [
                {"account_id": aid, "exchange": "bybit",
                 "status": "rejected_too_small", "reason": "qty too small",
                 "logged": True}
                for aid in account_ids
            ],
        }

    def _make_update(self):
        upd = MagicMock()
        upd.effective_chat.id = 12345
        upd.callback_query = None
        upd.message.reply_text = AsyncMock()
        return upd

    def _make_query(self, data: str):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message.chat.id = 12345
        query.message.reply_text = AsyncMock()
        upd = MagicMock()
        upd.callback_query = query
        upd.effective_chat = None
        return upd, query

    def _run(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def _patch_coord(self, monkeypatch, *, result=None):
        coord = MagicMock()
        coord.accounts_status.return_value = self._statuses()
        coord.smoke_test_run.return_value = result or self._result()
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        return coord

    def test_no_args_replies_with_picker_including_all_button(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        self._patch_coord(monkeypatch)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_smoke_test(upd, ctx))
        kwargs = upd.message.reply_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None, (
            "/smoke_test no-args must reply with the picker keyboard"
        )
        text = upd.message.reply_text.call_args.args[0]
        assert "Pick an account" in text or "Smoke test" in text

    def test_typed_account_arg_runs_immediately(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = ["live"]
        self._run(bot.cmd_smoke_test(upd, ctx))
        # Coordinator received the account_id as positional arg.
        assert coord.smoke_test_run.call_args.args[0] == "live"

    def test_typed_all_arg_runs_against_every_account(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = ["all"]
        self._run(bot.cmd_smoke_test(upd, ctx))
        # account_id should be None — coord runs across all accounts.
        assert coord.smoke_test_run.call_args.args[0] is None

    def test_callback_smoke_specific_account(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd, query = self._make_query("smoke:alpha")
        self._run(bot.callback_handler(upd, MagicMock()))
        assert coord.smoke_test_run.call_args.args[0] == "alpha"
        # Result rendered as a follow-up reply (not edit) so the
        # original "Running…" stays as breadcrumb.
        assert query.message.reply_text.called

    def test_callback_smoke_all_accounts(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = self._patch_coord(monkeypatch)
        upd, query = self._make_query("smoke:all")
        self._run(bot.callback_handler(upd, MagicMock()))
        assert coord.smoke_test_run.call_args.args[0] is None

    def test_render_smoke_test_result_is_pure(self):
        result = {
            "ok": True,
            "smoke_id": "smoke-x",
            "package": {"symbol": "BTCUSDT", "direction": "long", "qty": 0.0001},
            "results": [
                {"account_id": "live", "exchange": "bybit",
                 "status": "rejected_too_small", "reason": "qty too small",
                 "logged": True},
            ],
        }
        out1 = bot._render_smoke_test_result(result)
        out2 = bot._render_smoke_test_result(result)
        assert out1 == out2, "renderer is not pure"
        assert "smoke-x" in out1
        assert "Test successful" in out1

    def test_no_accounts_configured_returns_friendly_message(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        coord = MagicMock()
        coord.accounts_status.return_value = []
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        upd = self._make_update()
        ctx = MagicMock()
        ctx.args = []
        self._run(bot.cmd_smoke_test(upd, ctx))
        text = upd.message.reply_text.call_args.args[0]
        assert "No accounts" in text


# ---------------------------------------------------------------------------
# BUG-050 note: close_all_bybit_positions and TestCmdCloseallFailureIsolation
# removed — both tested dead code that was replaced in S-031 PR4.
# The canonical close path (processor.close_open_positions → execute_pkg) is
# covered by tests/test_s031_pr4_closeall_helper.py.
# ---------------------------------------------------------------------------

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
        """S-telegram-format Phase 4: cmd_log now consolidates all
        accounts into ONE collapsable HTML message (the operator
        asked for less message-per-tick noise). Pre-PR the bot sent
        one message per account; the new shape is one message with
        per-account expandable sections. The function still calls
        recent_logs_for once per account, so the routing contract
        stays intact — what changed is the delivery shape."""
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: self._accounts())
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {"STRATEGY": "ict"})
        log_calls = []

        def fake_logs(svc, n=20):
            log_calls.append(svc)
            return f"log output for {svc}"

        monkeypatch.setattr(bot.dl, "recent_logs_for", fake_logs)

        upd = self._make_update()
        self._run(bot.cmd_log(upd, MagicMock()))

        # Per-account log fetcher still called once per account.
        assert "ict-trader-live" in log_calls
        assert "ict-trader-alpha" in log_calls
        # Single consolidated message — both accounts in one body.
        msgs = [c.args[0] for c in upd.message.reply_text.call_args_list]
        assert len(msgs) == 1
        body = msgs[0]
        assert "ict-trader-live" in body
        assert "ict-trader-alpha" in body
        # Each account renders inside its own collapsable section.
        assert body.count("<blockquote expandable>") == 2

    def test_no_accounts_falls_back_to_live_service(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})
        monkeypatch.setattr(bot, "get_last_logs", lambda lines=20: "fallback log")

        upd = self._make_update()
        self._run(bot.cmd_log(upd, MagicMock()))

        sent = upd.message.reply_text.call_args.args[0]
        assert "fallback log" in sent

    def test_one_account_log_failure_does_not_block_others(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(bot.dl, "list_accounts", lambda: self._accounts())
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})

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
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})
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
        monkeypatch.setattr(trade_notifier, "_account_env", lambda acc: {})
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
