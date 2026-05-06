"""S-012 hotfix #3: bybit balance api_key_env path + /signals command.

Two fixes bundled — both surfaced from the live bot's `/balance` and
the absence of a signal-viewer command.

1. ``account_balance`` and ``account_open_positions`` were inlining
   ``_bybit_client(env)`` (legacy env_path-only) instead of routing
   through the api-key-env-aware ``bybit_client_for(account)`` added
   in hotfix #2. Result: ``/balance`` for accounts.yaml entries
   silently said "balance unavailable".

2. PR E4 wires signal attribution into ``runtime_logs/signal_audit.jsonl``
   but no Telegram command exposed it. Added ``/signals [N] [strategy]``.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Bot import requires telegram + dotenv stubs.
# ---------------------------------------------------------------------------
for _mod in ("telegram", "telegram.ext", "dotenv", "requests"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix

_tgext = sys.modules["telegram.ext"]
_tgext.Application = MagicMock
_tgext.CommandHandler = MagicMock
_tgext.CallbackQueryHandler = MagicMock
_ContextTypes = MagicMock()
_ContextTypes.DEFAULT_TYPE = MagicMock
_tgext.ContextTypes = _ContextTypes


# ---------------------------------------------------------------------------
# Fix A — account_balance / account_open_positions go through bybit_client_for
# ---------------------------------------------------------------------------


class TestAccountBalanceUsesApiKeyEnv:
    def test_account_balance_resolves_via_api_key_env(self, monkeypatch):
        """The bug: account_balance silently returned None for accounts.yaml
        accounts because it inlined _bybit_client(env). Fix: route through
        bybit_client_for which reads api_key_env from os.environ."""
        from src.bot import data_loaders as dl

        monkeypatch.setenv("BYBIT_API_KEY_1", "test-key-1")
        monkeypatch.setenv("BYBIT_API_SECRET_1", "test-secret-1")

        # Stub HTTP client so the test stays offline.
        captured_keys = {}
        fake_client = MagicMock()
        fake_client.get_wallet_balance.return_value = {
            "result": {
                "list": [
                    {"coin": [{"coin": "USDT", "usdValue": "100.50", "walletBalance": "100.50"}]}
                ]
            }
        }

        def _fake_http(*, testnet, api_key, api_secret):
            captured_keys["api_key"] = api_key
            captured_keys["api_secret"] = api_secret
            return fake_client
        fake_pybit = MagicMock()
        fake_pybit.unified_trading.HTTP = _fake_http
        monkeypatch.setitem(sys.modules, "pybit", fake_pybit)
        monkeypatch.setitem(sys.modules, "pybit.unified_trading", fake_pybit.unified_trading)

        account = {
            "account_id": "bybit_1",
            "exchange": "bybit",
            "api_key_env": "BYBIT_API_KEY_1",
            "env_path": None,  # the production accounts.yaml shape
        }
        result = dl.account_balance(account)
        assert result is not None, (
            "account_balance must succeed when api_key_env is set; "
            "previously it inlined _bybit_client(env) and silently returned None."
        )
        assert result["total_usdt"] == 100.50
        assert captured_keys["api_key"] == "test-key-1"

    def test_account_balance_returns_none_when_creds_missing(self, monkeypatch):
        from src.bot import data_loaders as dl

        monkeypatch.delenv("BYBIT_API_KEY_1", raising=False)
        monkeypatch.delenv("BYBIT_API_SECRET_1", raising=False)

        account = {
            "exchange": "bybit",
            "api_key_env": "BYBIT_API_KEY_1",
            "env_path": None,
        }
        # Graceful: no exception, just None.
        assert dl.account_balance(account) is None

    def test_account_open_positions_uses_api_key_env(self, monkeypatch):
        from src.bot import data_loaders as dl

        monkeypatch.setenv("BYBIT_API_KEY_1", "test-k")
        monkeypatch.setenv("BYBIT_API_SECRET_1", "test-s")

        fake_client = MagicMock()
        fake_client.get_positions.return_value = {
            "result": {
                "list": [
                    {"symbol": "BTCUSDT", "side": "Buy", "size": "0.0005",
                     "avgPrice": "50000", "unrealisedPnl": "1.23"}
                ]
            }
        }

        def _fake_http(*, testnet, api_key, api_secret):
            return fake_client
        fake_pybit = MagicMock()
        fake_pybit.unified_trading.HTTP = _fake_http
        monkeypatch.setitem(sys.modules, "pybit", fake_pybit)
        monkeypatch.setitem(sys.modules, "pybit.unified_trading", fake_pybit.unified_trading)

        account = {
            "exchange": "bybit",
            "api_key_env": "BYBIT_API_KEY_1",
            "env_path": None,
            # Pin to linear so the perp-position v5 endpoint is exercised
            # — the post-2026-05-06 default is ``spot`` (returns ``[]``
            # without a network call). Spot routing is covered by
            # ``test_spot_category_routing.py``.
            "market_type": "linear",
        }
        positions = dl.account_open_positions(account)
        assert positions is not None
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTCUSDT"
        assert positions[0]["size"] == 0.0005


class TestBalanceDiagnostic:
    """The error message should tell the operator WHY balance failed."""

    def test_diagnostic_when_env_var_unset(self, monkeypatch):
        from src.bot import telegram_query_bot as bot

        monkeypatch.delenv("BYBIT_API_KEY_1", raising=False)
        monkeypatch.delenv("BYBIT_API_SECRET_1", raising=False)
        account = {
            "account_id": "bybit_1",
            "exchange": "bybit",
            "api_key_env": "BYBIT_API_KEY_1",
            "env_path": None,
        }
        diag = bot._bybit_creds_diagnostic(account)
        assert diag is not None
        assert "BYBIT_API_KEY_1" in diag
        assert "BYBIT_API_SECRET_1" in diag
        assert "EnvironmentFile" in diag

    def test_diagnostic_none_when_env_vars_present(self, monkeypatch):
        from src.bot import telegram_query_bot as bot

        monkeypatch.setenv("BYBIT_API_KEY_1", "k")
        monkeypatch.setenv("BYBIT_API_SECRET_1", "s")
        account = {
            "account_id": "bybit_1",
            "api_key_env": "BYBIT_API_KEY_1",
        }
        # Both env vars present → no diagnostic (failure is on the API side).
        assert bot._bybit_creds_diagnostic(account) is None

    def test_format_balance_error_includes_diagnostic(self, monkeypatch):
        """When account_balance returns None and creds are missing, the
        rendered Telegram message should explain why."""
        from src.bot import telegram_query_bot as bot

        monkeypatch.delenv("BYBIT_API_KEY_1", raising=False)
        monkeypatch.setattr(bot.dl, "account_balance", lambda a: None)
        account = {
            "account_id": "bybit_1",
            "exchange": "bybit",
            "api_key_env": "BYBIT_API_KEY_1",
        }
        text = bot.format_bybit_balance(account)
        assert "balance unavailable" in text
        assert "BYBIT_API_KEY_1" in text       # the diagnostic surfaced
        assert "bybit_1" in text                # account id in heading


# ---------------------------------------------------------------------------
# Fix B — /signals command reads runtime_logs/signal_audit.jsonl
# ---------------------------------------------------------------------------


class TestReadAuditTail:
    def test_returns_empty_when_file_missing(self, tmp_path):
        from src.bot import telegram_query_bot as bot

        out = bot._read_audit_tail(str(tmp_path / "missing.jsonl"), 10)
        assert out == []

    def test_parses_jsonl_records(self, tmp_path):
        from src.bot import telegram_query_bot as bot

        path = tmp_path / "signal_audit.jsonl"
        path.write_text(
            json.dumps({"strategy": "vwap", "symbol": "BTCUSDT", "side": "buy",
                        "qty": 0.1, "status": "submitted",
                        "logged_at_utc": "2026-04-30T05:00:00Z"}) + "\n"
            + json.dumps({"strategy": "turtle_soup", "symbol": "ETHUSDT",
                          "side": "sell", "qty": 0.5, "status": "dry_run",
                          "logged_at_utc": "2026-04-30T05:01:00Z"}) + "\n"
        )
        out = bot._read_audit_tail(str(path), 10)
        assert len(out) == 2
        assert out[0]["strategy"] == "vwap"
        assert out[1]["strategy"] == "turtle_soup"

    def test_skips_malformed_lines(self, tmp_path):
        from src.bot import telegram_query_bot as bot

        path = tmp_path / "signal_audit.jsonl"
        path.write_text(
            "{not valid json\n"
            + json.dumps({"strategy": "vwap", "status": "submitted"}) + "\n"
            + "another junk line\n"
        )
        out = bot._read_audit_tail(str(path), 10)
        assert len(out) == 1
        assert out[0]["strategy"] == "vwap"


class TestFormatSignalRow:
    def test_renders_strategy_symbol_side_status(self):
        from src.bot import telegram_query_bot as bot

        rec = {
            "strategy": "turtle_soup",
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.0005,
            "status": "submitted",
            "logged_at_utc": "2026-04-30T05:04:35Z",
        }
        text = bot._format_signal_row(rec)
        assert "turtle_soup" in text
        assert "BTCUSDT" in text
        assert "buy" in text
        assert "submitted" in text
        assert "0.0005" in text
        assert "🟢" in text  # submitted emoji

    def test_renders_failed_validation_with_reason(self):
        from src.bot import telegram_query_bot as bot

        rec = {
            "strategy": "vwap",
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.0005,
            "status": "failed_validation",
            "reason": "ALLOW_LIVE_TRADING=true is required for live submission",
            "logged_at_utc": "2026-04-30T05:00:00Z",
        }
        text = bot._format_signal_row(rec)
        assert "failed_validation" in text
        assert "ALLOW_LIVE_TRADING" in text
        assert "🔴" in text  # failed_validation emoji


class TestCmdSignals:
    """cmd_signals is async; we drive it via a fresh event loop rather
    than depend on pytest-asyncio (not installed in this env).

    Important: use ``new_event_loop().run_until_complete()`` rather than
    ``asyncio.run()``. ``asyncio.run`` closes the loop after the call,
    which breaks downstream tests in tests/test_telegram_query_bot.py
    that still use the older ``asyncio.get_event_loop()`` API."""

    @staticmethod
    def _drive(coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_no_records_replies_empty(self, tmp_path, monkeypatch):
        from src.bot import telegram_query_bot as bot

        monkeypatch.setattr(bot, "SIGNAL_AUDIT_PATH", str(tmp_path / "missing.jsonl"))
        monkeypatch.setattr(bot, "is_authorised", lambda u: True)

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []
        self._drive(bot.cmd_signals(update, context))
        sent = update.message.reply_text.await_args.args[0]
        assert "No signals logged" in sent

    def test_returns_recent_records(self, tmp_path, monkeypatch):
        from src.bot import telegram_query_bot as bot

        path = tmp_path / "signal_audit.jsonl"
        path.write_text(
            json.dumps({"strategy": "vwap", "symbol": "BTCUSDT", "side": "buy",
                        "qty": 0.1, "status": "submitted",
                        "logged_at_utc": "2026-04-30T05:00:00Z"}) + "\n"
            + json.dumps({"strategy": "turtle_soup", "symbol": "ETHUSDT",
                          "side": "sell", "qty": 0.5, "status": "dry_run",
                          "logged_at_utc": "2026-04-30T05:01:00Z"}) + "\n"
        )
        monkeypatch.setattr(bot, "SIGNAL_AUDIT_PATH", str(path))
        monkeypatch.setattr(bot, "is_authorised", lambda u: True)

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []
        self._drive(bot.cmd_signals(update, context))
        sent = update.message.reply_text.await_args.args[0]
        assert "vwap" in sent
        assert "turtle_soup" in sent
        assert "Last 2 signals" in sent

    def test_strategy_filter_works(self, tmp_path, monkeypatch):
        from src.bot import telegram_query_bot as bot

        path = tmp_path / "signal_audit.jsonl"
        path.write_text(
            json.dumps({"strategy": "vwap", "symbol": "BTCUSDT", "side": "buy",
                        "qty": 0.1, "status": "submitted",
                        "logged_at_utc": "2026-04-30T05:00:00Z"}) + "\n"
            + json.dumps({"strategy": "turtle_soup", "symbol": "ETHUSDT",
                          "side": "sell", "qty": 0.5, "status": "dry_run",
                          "logged_at_utc": "2026-04-30T05:01:00Z"}) + "\n"
        )
        monkeypatch.setattr(bot, "SIGNAL_AUDIT_PATH", str(path))
        monkeypatch.setattr(bot, "is_authorised", lambda u: True)

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["turtle_soup"]
        self._drive(bot.cmd_signals(update, context))
        sent = update.message.reply_text.await_args.args[0]
        assert "turtle_soup" in sent
        # Filtered out:
        assert "vwap" not in sent

    def test_numeric_arg_sets_limit(self, tmp_path, monkeypatch):
        from src.bot import telegram_query_bot as bot

        path = tmp_path / "signal_audit.jsonl"
        path.write_text(
            "\n".join(
                json.dumps({"strategy": "vwap", "symbol": "BTCUSDT", "side": "buy",
                            "qty": 0.1, "status": "submitted",
                            "logged_at_utc": f"2026-04-30T05:0{i}:00Z"})
                for i in range(5)
            ) + "\n"
        )
        monkeypatch.setattr(bot, "SIGNAL_AUDIT_PATH", str(path))
        monkeypatch.setattr(bot, "is_authorised", lambda u: True)

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["3"]
        self._drive(bot.cmd_signals(update, context))
        sent = update.message.reply_text.await_args.args[0]
        assert "Last 3 signals" in sent

    def test_unauthorised_caller_no_reply(self, monkeypatch):
        from src.bot import telegram_query_bot as bot

        monkeypatch.setattr(bot, "is_authorised", lambda u: False)
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = []
        self._drive(bot.cmd_signals(update, context))
        update.message.reply_text.assert_not_awaited()
