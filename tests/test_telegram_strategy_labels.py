"""Tests for strategy-aware label helpers in `src/bot/telegram_query_bot.py`.

The bot module imports `telegram`, `telegram.ext`, and `pybit` at module
load time. Those packages are not installed in CI, so we install minimal
stubs into `sys.modules` *before* importing the module. The stubs only
need to satisfy the `from ... import ...` statements at the top of the
file — none of the tested helpers actually touch them.

Paper trading was removed from the bot in CP-16; the live trader is the
only target. These tests cover the single-trader API.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Stub the third-party packages the bot module imports at top-level.
# ---------------------------------------------------------------------------

class _AnyAttr(type):
    """Metaclass that returns a fresh placeholder for any attribute access.

    Lets us satisfy annotations like ``ContextTypes.DEFAULT_TYPE`` without
    pulling in the real ``python-telegram-bot`` package.
    """

    def __getattr__(cls, name):  # noqa: D401
        return type(name, (), {})


def _make_stub_class(name: str):
    return _AnyAttr(name, (), {})


def _install_stubs() -> None:
    if "telegram" not in sys.modules:
        telegram_mod = types.ModuleType("telegram")
        for name in ("Update", "BotCommand", "InlineKeyboardButton", "InlineKeyboardMarkup"):
            setattr(telegram_mod, name, _make_stub_class(name))
        sys.modules["telegram"] = telegram_mod

    if "telegram.ext" not in sys.modules:
        telegram_ext_mod = types.ModuleType("telegram.ext")
        for name in ("Application", "CommandHandler", "CallbackQueryHandler", "ContextTypes"):
            setattr(telegram_ext_mod, name, _make_stub_class(name))
        sys.modules["telegram.ext"] = telegram_ext_mod

    # `pybit.unified_trading.HTTP` is imported lazily inside
    # `get_bybit_client_from_env`, so it does not need stubbing for our
    # tests. Same for `requests` (real package, available in the sandbox).


_install_stubs()

# Now safe to import the module under test.
from src.bot import telegram_query_bot as bot  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_env(tmp_path: Path, name: str, **vars: str) -> Path:
    p = tmp_path / name
    p.write_text("\n".join(f"{k}={v}" for k, v in vars.items()) + "\n")
    return p


@pytest.fixture
def restore_dotenv_values(monkeypatch):
    """Restore a working ``dotenv_values`` on the bot module.

    Other tests in the suite (e.g. ``test_kill_switch``, ``test_orders``)
    install a ``MagicMock`` into ``sys.modules['dotenv']`` and never
    clean it up. That leaks across test files because import-time
    ``from dotenv import dotenv_values`` in ``telegram_query_bot`` then
    binds to the mock. We restore a minimal real implementation that
    parses ``KEY=VALUE`` lines so our tests work regardless of suite order.
    """

    def _real_dotenv_values(path):
        result = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip()
        except FileNotFoundError:
            pass
        return result

    monkeypatch.setattr(bot, "dotenv_values", _real_dotenv_values)


# ---------------------------------------------------------------------------
# Paper trading is fully excised — assert key surfaces are gone.
# ---------------------------------------------------------------------------

def test_get_account_label_helper_removed():
    """The legacy ``get_account_label`` helper is gone in CP-16."""
    assert not hasattr(bot, "get_account_label")


def test_paper_env_path_constant_removed():
    """``PAPER_ENV_PATH`` is gone — there is only ``LIVE_ENV_PATH``."""
    assert not hasattr(bot, "PAPER_ENV_PATH")
    assert hasattr(bot, "LIVE_ENV_PATH")


def test_live_service_name_constant_exists():
    """The bot drives a single systemd service identified by this constant."""
    assert bot.LIVE_SERVICE_NAME == "ict-trader-live"


# ---------------------------------------------------------------------------
# load_account_env — single-arg, reads only the live .env
# ---------------------------------------------------------------------------

def test_load_account_env_reads_live_env(monkeypatch, tmp_path, restore_dotenv_values):
    env_file = _write_env(tmp_path, ".env", STRATEGY="ict", BYBIT_API_KEY="x")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(env_file))

    result = bot.load_account_env()
    assert result["STRATEGY"] == "ict"
    assert result["BYBIT_API_KEY"] == "x"


def test_load_account_env_returns_empty_dict_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    assert bot.load_account_env() == {}


def test_load_account_env_takes_no_arguments():
    """Signature is ``load_account_env()`` — passing a target should error."""
    with pytest.raises(TypeError):
        bot.load_account_env("live")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        bot.load_account_env("paper")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# get_strategy_label — STRATEGY env var drives the label
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ict", "ICT"),
        ("ICT", "ICT"),  # case-insensitive
        ("  ict  ", "ICT"),  # whitespace-tolerant
        ("vwap", "VWAP"),
        ("breakout", "Breakout"),
        ("multiplexed", "Multi"),
        ("killzone", "ICT"),  # legacy alias
    ],
)
def test_get_strategy_label_known_strategies(raw, expected):
    assert bot.get_strategy_label({"STRATEGY": raw}) == expected


def test_get_strategy_label_strategy_name_alias():
    """``STRATEGY_NAME`` is supported as a fallback to ``STRATEGY``."""
    assert bot.get_strategy_label({"STRATEGY_NAME": "vwap"}) == "VWAP"


def test_get_strategy_label_falls_back_for_unknown_strategy():
    """Unknown / empty strategy values fall back to the default label."""
    assert bot.get_strategy_label({"STRATEGY": "not-a-real-strategy"}) == bot._DEFAULT_STRATEGY_LABEL
    assert bot.get_strategy_label({"STRATEGY": ""}) == bot._DEFAULT_STRATEGY_LABEL
    assert bot.get_strategy_label({}) == bot._DEFAULT_STRATEGY_LABEL


def test_get_strategy_label_reads_live_env_when_no_arg(monkeypatch, tmp_path, restore_dotenv_values):
    """Calling with no args reads the STRATEGY value from ``LIVE_ENV_PATH``."""
    env_file = _write_env(tmp_path, ".env", STRATEGY="vwap")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(env_file))

    assert bot.get_strategy_label() == "VWAP"


def test_get_strategy_label_swallows_unexpected_errors(monkeypatch):
    """Defensive: a broken ``load_account_env`` must not crash the bot."""

    def _boom():
        raise RuntimeError("unexpected env failure")

    monkeypatch.setattr(bot, "load_account_env", _boom)
    assert bot.get_strategy_label() == bot._DEFAULT_STRATEGY_LABEL


# ---------------------------------------------------------------------------
# format_target_options — back-compat helper used by post_init
# ---------------------------------------------------------------------------

def test_format_target_options_returns_single_strategy(monkeypatch, tmp_path, restore_dotenv_values):
    """Returns the active strategy label — no more ``live|paper``."""
    env_file = _write_env(tmp_path, ".env", STRATEGY="ict")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(env_file))

    assert bot.format_target_options() == "ICT"


def test_format_target_options_falls_back_when_env_missing(monkeypatch, tmp_path):
    """Missing env file → default label, never crashes."""
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(tmp_path / "nope.env"))
    assert bot.format_target_options() == bot._DEFAULT_STRATEGY_LABEL


def test_format_target_options_falls_back_when_strategy_unset(monkeypatch, tmp_path, restore_dotenv_values):
    """Env file exists but has no STRATEGY → default label."""
    env_file = _write_env(tmp_path, ".env", BYBIT_API_KEY="x")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(env_file))

    assert bot.format_target_options() == bot._DEFAULT_STRATEGY_LABEL


def test_format_target_options_swallows_unexpected_errors(monkeypatch):
    """If anything explodes, ``post_init`` still gets a safe default."""

    def _boom():
        raise RuntimeError("unexpected env failure")

    monkeypatch.setattr(bot, "load_account_env", _boom)
    assert bot.format_target_options() == bot._DEFAULT_STRATEGY_LABEL


def test_format_target_options_separator_is_no_op_with_single_label(
    monkeypatch, tmp_path, restore_dotenv_values
):
    """``separator`` is retained for API compatibility but unused with one label."""
    env_file = _write_env(tmp_path, ".env", STRATEGY="ict")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(env_file))

    assert bot.format_target_options(separator=" / ") == "ICT"
    assert bot.format_target_options(separator="|") == "ICT"
