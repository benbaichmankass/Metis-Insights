"""Tests for the menu-driven operator bot (telegram_query_bot).

The 2026-05 overhaul replaced the ~40-command surface with a 4-item
inline menu + close-all and two persistent kill switches. These tests
cover: the trimmed command surface, the menu openers, the ``menu:*`` /
``killacct*`` / ``killstrat*`` callback routing, the resilient view
builders, and that the two kill switches call their sanctioned writers.

Telegram is stubbed centrally by ``tests/conftest.py`` (keyboards become
MagicMocks), so assertions target rendered TEXT and writer invocation —
not keyboard internals (those are covered in ``test_menu.py``).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.bot import telegram_query_bot as bot


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_msg(chat_id="12345"):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.callback_query = None
    upd.message.reply_text = AsyncMock()
    return upd


def _make_cb(data, chat_id="12345"):
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message.chat.id = chat_id
    upd = MagicMock()
    upd.callback_query = q
    upd.effective_chat = None  # is_authorised then falls back to callback chat id
    return upd, q


# ── command surface ──────────────────────────────────────────────────────────


def test_command_surface_is_just_the_menu_openers():
    # Assert against _MENU_OPENERS (stub-proof) — BOT_COMMANDS entries are
    # telegram.BotCommand, which other test files may stub to MagicMock.
    names = {name for name, _ in bot._MENU_OPENERS}
    assert names == {"start", "menu"}


def test_descriptions_within_telegram_limits():
    for _name, desc in bot._MENU_OPENERS:
        assert 1 <= len(desc) <= 80


# ── is_halted / is_authorised ────────────────────────────────────────────────


class TestGuards:
    def test_is_halted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(tmp_path / "no_flag"))
        assert bot.is_halted() is False
        flag = tmp_path / "halt.flag"
        flag.touch()
        monkeypatch.setattr(bot, "HALT_FLAG_PATH", str(flag))
        assert bot.is_halted() is True

    def test_is_authorised_match(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        assert bot.is_authorised(_make_msg("12345")) is True
        assert bot.is_authorised(_make_msg("99999")) is False


# ── menu openers ─────────────────────────────────────────────────────────────


class TestMenuOpener:
    def test_cmd_start_opens_menu(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd = _make_msg()
        _run(bot.cmd_start(upd, MagicMock()))
        upd.message.reply_text.assert_awaited_once()
        kwargs = upd.message.reply_text.await_args.kwargs
        assert kwargs.get("reply_markup") is not None

    def test_unauthorised_start_is_silent(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd = _make_msg("99999")
        _run(bot.cmd_start(upd, MagicMock()))
        upd.message.reply_text.assert_not_called()


# ── callback routing ─────────────────────────────────────────────────────────


class TestCallbackRouting:
    def _setup(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        # Keep the views offline + deterministic.
        monkeypatch.setattr(bot, "_accounts_status", lambda: [
            {"name": "bybit_1", "dry_run": False, "exchange": "bybit",
             "daily_pnl": 12.0, "open_positions": 1},
        ])
        monkeypatch.setattr(bot, "_load_strategies_config", lambda: [
            {"name": "vwap", "execution": "live"},
            {"name": "turtle_soup", "execution": "shadow"},
        ])
        monkeypatch.setattr(bot, "get_service_status", lambda u: "active")

    def test_unauthorised_callback(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, q = _make_cb("menu:system", chat_id="99999")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "Unauthorised" in q.edit_message_text.await_args.args[0]

    def test_home(self, monkeypatch):
        self._setup(monkeypatch)
        upd, q = _make_cb("menu:home")
        _run(bot.callback_handler(upd, MagicMock()))
        assert q.edit_message_text.await_args.kwargs.get("reply_markup") is not None

    def test_kill_menu(self, monkeypatch):
        self._setup(monkeypatch)
        upd, q = _make_cb("menu:kill")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "Kill switch" in q.edit_message_text.await_args.args[0]

    def test_system_view(self, monkeypatch):
        self._setup(monkeypatch)
        upd, q = _make_cb("menu:system")
        _run(bot.callback_handler(upd, MagicMock()))
        assert q.edit_message_text.await_args.kwargs.get("parse_mode") == "HTML"
        assert "System update" in q.edit_message_text.await_args.args[0]

    def test_accounts_view(self, monkeypatch):
        self._setup(monkeypatch)
        upd, q = _make_cb("menu:accounts")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "Accounts snapshot" in q.edit_message_text.await_args.args[0]
        assert "bybit_1" in q.edit_message_text.await_args.args[0]

    def test_strategies_view(self, monkeypatch):
        self._setup(monkeypatch)
        upd, q = _make_cb("menu:strategies")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "Strategies snapshot" in q.edit_message_text.await_args.args[0]

    def test_kill_accounts_lists(self, monkeypatch):
        self._setup(monkeypatch)
        upd, q = _make_cb("menu:kill_accounts")
        _run(bot.callback_handler(upd, MagicMock()))
        assert q.edit_message_text.await_args.kwargs.get("reply_markup") is not None

    def test_kill_strats_lists(self, monkeypatch):
        self._setup(monkeypatch)
        upd, q = _make_cb("menu:kill_strats")
        _run(bot.callback_handler(upd, MagicMock()))
        assert q.edit_message_text.await_args.kwargs.get("reply_markup") is not None


# ── kill switch: account ─────────────────────────────────────────────────────


class TestAccountKill:
    def test_flip_to_live_warns(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, q = _make_cb("killacct:bybit_1:live")
        _run(bot.callback_handler(upd, MagicMock()))
        text = q.edit_message_text.await_args.args[0]
        assert "bybit_1" in text and "REAL orders" in text

    def test_flip_to_dry_no_warning(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, q = _make_cb("killacct:bybit_1:dry_run")
        _run(bot.callback_handler(upd, MagicMock()))
        text = q.edit_message_text.await_args.args[0]
        assert "REAL orders" not in text

    def test_do_invokes_sanctioned_writer(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        persist = AsyncMock(return_value="✅ Account bybit_1 → dry_run.")
        monkeypatch.setattr(bot, "_persist_account_mode", persist)
        upd, q = _make_cb("killacct_do:bybit_1:dry_run")
        _run(bot.callback_handler(upd, MagicMock()))
        persist.assert_awaited_once_with("bybit_1", "dry_run")
        # last edit carries the writer's result
        assert "dry_run" in q.edit_message_text.await_args.args[0]


def test_persist_account_mode_runs_script(monkeypatch):
    calls = {}

    def fake_run(cmd, env=None, capture_output=None, text=None, timeout=None):
        calls["cmd"] = cmd
        calls["env"] = env
        return MagicMock(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(bot.subprocess, "run", fake_run)
    out = _run(bot._persist_account_mode("bybit_2", "dry_run"))
    assert calls["cmd"][0] == "bash"
    assert calls["cmd"][1].endswith("set_account_mode.sh")
    assert calls["env"]["ACCOUNT_ID"] == "bybit_2"
    assert calls["env"]["MODE"] == "dry_run"
    assert "persisted" in out


def test_persist_account_mode_reports_failure(monkeypatch):
    def fake_run(cmd, env=None, **kw):
        return MagicMock(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(bot.subprocess, "run", fake_run)
    out = _run(bot._persist_account_mode("bybit_2", "live"))
    assert "failed" in out and "boom" in out


# ── kill switch: strategy ────────────────────────────────────────────────────


class TestStrategyKill:
    def test_flip_to_live_warns(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, q = _make_cb("killstrat:vwap:live")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "LIVE" in q.edit_message_text.await_args.args[0]

    def test_do_invokes_writer_and_reload(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr(
            bot, "set_strategy_execution", lambda path, name, ex: ("live", ex)
        )
        coord = MagicMock()
        coord.reload_strategy_config.return_value = {"reloaded": True}
        monkeypatch.setattr(bot, "get_coordinator", lambda: coord)
        upd, q = _make_cb("killstrat_do:vwap:shadow")
        _run(bot.callback_handler(upd, MagicMock()))
        coord.reload_strategy_config.assert_called_once()
        text = q.edit_message_text.await_args.args[0]
        assert "vwap" in text and "shadow" in text

    def test_do_handles_writer_error(self, monkeypatch):
        from src.bot.strategy_execution_writer import StrategyExecutionWriteError

        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")

        def boom(path, name, ex):
            raise StrategyExecutionWriteError("nope")

        monkeypatch.setattr(bot, "set_strategy_execution", boom)
        upd, q = _make_cb("killstrat_do:ghost:shadow")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "Could not flip" in q.edit_message_text.await_args.args[0]


# ── close-all ────────────────────────────────────────────────────────────────


class TestCloseAll:
    def test_confirm_prompt(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        upd, q = _make_cb("menu:closeall")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "Close ALL" in q.edit_message_text.await_args.args[0]

    def test_confirm_executes_and_reports(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")
        import src.units.ui.processor as proc

        monkeypatch.setattr(
            proc, "close_open_positions",
            lambda: [{"ok": True}, {"ok": True}, {"ok": False}],
        )
        upd, q = _make_cb("menu:closeall_confirm")
        _run(bot.callback_handler(upd, MagicMock()))
        text = q.edit_message_text.await_args.args[0]
        assert "2 closed" in text and "1 failed" in text


# ── resilience ───────────────────────────────────────────────────────────────


class TestResilience:
    def test_views_dont_raise_without_coordinator(self, monkeypatch):
        monkeypatch.setattr(bot, "get_coordinator", lambda: None)
        monkeypatch.setattr(bot, "_load_strategies_config", lambda: [])
        monkeypatch.setattr(bot, "get_service_status", lambda u: "inactive")
        assert isinstance(bot.build_system_view(), str)
        assert isinstance(bot.build_accounts_view(), str)
        assert isinstance(bot.build_strategies_view(), str)

    def test_callback_error_degrades_gracefully(self, monkeypatch):
        monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")

        def boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(bot, "build_system_view", boom)
        upd, q = _make_cb("menu:system")
        _run(bot.callback_handler(upd, MagicMock()))
        assert "failed" in q.edit_message_text.await_args.args[0].lower()


# ── pure mappers ─────────────────────────────────────────────────────────────


def test_kill_summary_counts():
    statuses = [{"dry_run": True}, {"dry_run": False}, {"mode": "live"}]
    strategies = [
        {"execution": "live"}, {"execution": "shadow"}, {"execution": "shadow"},
    ]
    s = bot._kill_summary(statuses, strategies)
    assert s == {
        "accounts_live": 2, "accounts_dry": 1,
        "strats_live": 1, "strats_shadow": 2,
    }


def test_accounts_view_data_maps_fields():
    out = bot._accounts_view_data([
        {"name": "a1", "exchange": "bybit", "dry_run": True,
         "daily_pnl": -5.0, "live_balance_usdt": 100.0, "open_positions": 2},
    ])
    assert out[0]["account_id"] == "a1"
    assert out[0]["dry_run"] is True
    assert out[0]["balance"] == 100.0
    assert out[0]["pnl_24h"] == -5.0
