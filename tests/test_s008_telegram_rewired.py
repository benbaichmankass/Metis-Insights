"""S-008 PR #124: Telegram Bot rewired — coordinator integration tests.

Tests the pure-Python parts added in PR #124:
  - get_coordinator() singleton behaviour
  - cmd_strategies() → coordinator.dashboard_stats()
  - cmd_halt() → coordinator.return_command("halt") + flag file
  - cmd_resume() → coordinator.return_command("resume") + flag removal
  - cmd_alerts() → coordinator.list_alerts()

Heavy deps (telegram, pybit) are stubbed at sys.modules level before import,
following the pattern established in test_telegram_query_bot.py.
Async coroutines are driven with asyncio.new_event_loop().run_until_complete().
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Stub heavy deps before any src import (same pattern as existing test file)
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

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

_tg_mock = sys.modules["telegram"]
_tg_mock.Update = MagicMock
_tg_mock.BotCommand = MagicMock
_tg_mock.InlineKeyboardButton = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix
_tg_mock.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix
_tg_ext_mock = sys.modules["telegram.ext"]
_tg_ext_mock.Application = MagicMock
_tg_ext_mock.CommandHandler = MagicMock
_tg_ext_mock.CallbackQueryHandler = MagicMock
_tg_ext_mock.ContextTypes = MagicMock()
_tg_ext_mock.ContextTypes.DEFAULT_TYPE = object

import src.bot.telegram_query_bot as bot  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(chat_id: str = "99999") -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    return update


def _make_context() -> MagicMock:
    return MagicMock()


def _mock_coordinator(dashboard_rows=None, alerts=None):
    coord = MagicMock()
    coord.dashboard_stats.return_value = {
        "strategies": dashboard_rows or [],
        "accounts": [],
        "alerts": alerts or [],
        "generated_at": "2026-04-29T00:00:00+00:00",
    }
    coord.list_alerts.return_value = alerts or []
    coord.return_command.return_value = {"status": "ok", "detail": "ok"}
    return coord


# ---------------------------------------------------------------------------
# get_coordinator()
# ---------------------------------------------------------------------------


class TestGetCoordinator:
    def setup_method(self):
        bot._coordinator = None

    def test_returns_coordinator_instance(self):
        coord = bot.get_coordinator()
        assert coord is not None

    def test_returns_same_instance_on_second_call(self):
        c1 = bot.get_coordinator()
        c2 = bot.get_coordinator()
        assert c1 is c2

    def test_pre_set_coordinator_returned_directly(self):
        mock_coord = MagicMock()
        bot._coordinator = mock_coord
        assert bot.get_coordinator() is mock_coord


# ---------------------------------------------------------------------------
# cmd_strategies → coordinator.dashboard_stats()
# ---------------------------------------------------------------------------


class TestCmdStrategiesRewired:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_calls_coordinator_dashboard_stats(self):
        coord = _mock_coordinator(dashboard_rows=[
            {"strategy": "vwap", "service": "ict-trader-vwap", "model": None,
             "signals_today": 2, "pnl": 50.0, "open_pos": 0, "status": "active",
             "paused": False},
        ])
        bot._coordinator = coord
        update = _make_update(chat_id="99999")

        _run(bot.cmd_strategies(update, _make_context()))

        coord.dashboard_stats.assert_called_once()
        update.message.reply_text.assert_called_once()

    def test_reply_contains_strategy_name(self):
        coord = _mock_coordinator(dashboard_rows=[
            {"strategy": "vwap", "service": "ict-trader-vwap", "model": None,
             "signals_today": 0, "pnl": 0.0, "open_pos": 0, "status": "active",
             "paused": False},
        ])
        bot._coordinator = coord
        update = _make_update(chat_id="99999")

        _run(bot.cmd_strategies(update, _make_context()))

        text = update.message.reply_text.call_args[0][0]
        assert "vwap" in text.lower()

    def test_falls_back_to_dl_when_coordinator_none(self, monkeypatch):
        bot._coordinator = None
        monkeypatch.setattr(bot, "get_coordinator", lambda: None)
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data",
            lambda: [{"strategy": "ict", "service": "s", "model": None,
                      "signals_today": 0, "pnl": 0.0, "open_pos": 0, "status": "active"}],
        )
        update = _make_update(chat_id="99999")
        _run(bot.cmd_strategies(update, _make_context()))
        update.message.reply_text.assert_called_once()

    def test_not_authorised_returns_early(self):
        update = _make_update(chat_id="wrong-id")
        _run(bot.cmd_strategies(update, _make_context()))
        update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_halt → coordinator.return_command("halt") + flag file
# ---------------------------------------------------------------------------


class TestCmdHaltRewired:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_calls_coordinator_return_command_halt(self, tmp_path, monkeypatch):
        coord = _mock_coordinator()
        bot._coordinator = coord
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "halt.flag"))
        update = _make_update(chat_id="99999")

        _run(bot.cmd_halt(update, _make_context()))

        coord.return_command.assert_called_once_with("halt")

    def test_creates_flag_file(self, tmp_path, monkeypatch):
        coord = _mock_coordinator()
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        flag = tmp_path / "halt.flag"
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(flag))
        update = _make_update(chat_id="99999")

        _run(bot.cmd_halt(update, _make_context()))

        assert flag.exists()

    def test_halt_replies_halted_message(self, tmp_path, monkeypatch):
        coord = _mock_coordinator()
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "halt.flag"))
        update = _make_update(chat_id="99999")

        _run(bot.cmd_halt(update, _make_context()))

        text = update.message.reply_text.call_args[0][0]
        assert "HALT" in text.upper()

    def test_not_authorised_returns_early(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "halt.flag"))
        update = _make_update(chat_id="wrong")
        _run(bot.cmd_halt(update, _make_context()))
        update.message.reply_text.assert_not_called()
        assert not (tmp_path / "halt.flag").exists()


# ---------------------------------------------------------------------------
# cmd_resume → coordinator.return_command("resume") + flag removal
# ---------------------------------------------------------------------------


class TestCmdResumeRewired:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_calls_coordinator_return_command_resume(self, tmp_path, monkeypatch):
        coord = _mock_coordinator()
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        flag = tmp_path / "halt.flag"
        flag.write_text("halted")
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(flag))
        update = _make_update(chat_id="99999")

        _run(bot.cmd_resume(update, _make_context()))

        coord.return_command.assert_called_once_with("resume")

    def test_removes_flag_file(self, tmp_path, monkeypatch):
        coord = _mock_coordinator()
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        flag = tmp_path / "halt.flag"
        flag.write_text("halted")
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(flag))
        update = _make_update(chat_id="99999")

        _run(bot.cmd_resume(update, _make_context()))

        assert not flag.exists()

    def test_no_flag_file_skips_coordinator_and_informs_user(self, tmp_path, monkeypatch):
        coord = _mock_coordinator()
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag.flag"))
        update = _make_update(chat_id="99999")

        _run(bot.cmd_resume(update, _make_context()))

        coord.return_command.assert_not_called()
        text = update.message.reply_text.call_args[0][0]
        assert "not halted" in text.lower() or "no flag" in text.lower()

    def test_not_authorised_returns_early(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "halt.flag"))
        update = _make_update(chat_id="wrong")
        _run(bot.cmd_resume(update, _make_context()))
        update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_alerts → coordinator.list_alerts()
# ---------------------------------------------------------------------------


class TestCmdAlerts:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_shows_alerts_from_coordinator(self):
        alerts = [
            {"ts": "2026-04-29T10:00:00+00:00", "source": "accounts",
             "level": "info", "message": "Trade placed"},
            {"ts": "2026-04-29T10:01:00+00:00", "source": "return_commands",
             "level": "warning", "message": "Halted"},
        ]
        coord = _mock_coordinator(alerts=alerts)
        bot._coordinator = coord
        update = _make_update(chat_id="99999")

        _run(bot.cmd_alerts(update, _make_context()))

        coord.list_alerts.assert_called_once_with(n=10)
        text = update.message.reply_text.call_args[0][0]
        assert "Trade placed" in text or "Halted" in text

    def test_empty_alerts_shows_no_alerts_message(self):
        coord = _mock_coordinator(alerts=[])
        bot._coordinator = coord
        update = _make_update(chat_id="99999")

        _run(bot.cmd_alerts(update, _make_context()))

        text = update.message.reply_text.call_args[0][0]
        assert "No alerts" in text or "📭" in text

    def test_coordinator_none_returns_unavailable(self):
        bot._coordinator = None
        update = _make_update(chat_id="99999")

        with patch.object(bot, "get_coordinator", return_value=None):
            _run(bot.cmd_alerts(update, _make_context()))

        text = update.message.reply_text.call_args[0][0]
        assert "unavailable" in text.lower()

    def test_not_authorised_returns_early(self):
        update = _make_update(chat_id="wrong")
        _run(bot.cmd_alerts(update, _make_context()))
        update.message.reply_text.assert_not_called()
