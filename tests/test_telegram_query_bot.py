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
