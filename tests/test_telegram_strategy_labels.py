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
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub the third-party packages the bot module imports at top-level.
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

# Provide realistic dotenv stubs (real parse logic is restored per-test via fixture)
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

# telegram.Update must be importable as a class
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
    """Both legacy path constants are gone: PAPER_ENV_PATH (paper-trading era)
    and LIVE_ENV_PATH (deleted in S-003 N1-a — dead code)."""
    assert not hasattr(bot, "PAPER_ENV_PATH")
    assert not hasattr(bot, "LIVE_ENV_PATH")


def test_live_service_name_constant_exists():
    """The bot drives a single systemd service identified by this constant."""
    assert bot.LIVE_SERVICE_NAME == "ict-trader-live"


# ---------------------------------------------------------------------------
# get_strategy_label — account dict with env_path drives the label
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
def test_get_strategy_label_known_strategies(tmp_path, restore_dotenv_values, raw, expected):
    env_file = _write_env(tmp_path, ".env", STRATEGY=raw)
    account = {"env_path": str(env_file)}
    assert bot.get_strategy_label(account) == expected


def test_get_strategy_label_strategy_name_alias(tmp_path, restore_dotenv_values):
    """``STRATEGY_NAME`` is supported as a fallback to ``STRATEGY``."""
    env_file = _write_env(tmp_path, ".env", STRATEGY_NAME="vwap")
    account = {"env_path": str(env_file)}
    assert bot.get_strategy_label(account) == "VWAP"


def test_get_strategy_label_falls_back_for_unknown_strategy(tmp_path, restore_dotenv_values):
    """Unknown / empty strategy values fall back to the default label."""
    env_file_unknown = _write_env(tmp_path, "unknown.env", STRATEGY="not-a-real-strategy")
    env_file_empty = _write_env(tmp_path, "empty.env", STRATEGY="")
    assert bot.get_strategy_label({"env_path": str(env_file_unknown)}) == bot._DEFAULT_STRATEGY_LABEL
    assert bot.get_strategy_label({"env_path": str(env_file_empty)}) == bot._DEFAULT_STRATEGY_LABEL
    assert bot.get_strategy_label({}) == bot._DEFAULT_STRATEGY_LABEL


def test_get_strategy_label_reads_first_account_when_no_arg(monkeypatch, tmp_path, restore_dotenv_values):
    """Calling with no args reads from the first account returned by dl.list_accounts()."""
    env_file = _write_env(tmp_path, ".env", STRATEGY="vwap")
    monkeypatch.setattr(bot.dl, "list_accounts", lambda: [{"env_path": str(env_file)}])

    assert bot.get_strategy_label() == "VWAP"


def test_get_strategy_label_no_arg_falls_back_when_no_accounts(monkeypatch):
    """No-arg path returns default label when dl.list_accounts() is empty."""
    monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
    assert bot.get_strategy_label() == bot._DEFAULT_STRATEGY_LABEL


def test_get_strategy_label_swallows_unexpected_errors(monkeypatch):
    """Defensive: a broken dl.list_accounts must not crash the bot."""

    def _boom():
        raise RuntimeError("unexpected env failure")

    monkeypatch.setattr(bot.dl, "list_accounts", _boom)
    assert bot.get_strategy_label() == bot._DEFAULT_STRATEGY_LABEL


