"""Tests for strategy-aware label helpers in `src/bot/telegram_query_bot.py`.

The bot module imports `telegram`, `telegram.ext`, and `pybit` at module
load time. Those packages are not installed in CI, so we install minimal
stubs into `sys.modules` *before* importing the module. The stubs only
need to satisfy the `from ... import ...` statements at the top of the
file — none of the tested helpers actually touch them.
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
        # The names imported via `from telegram import ...`. Plain object
        # placeholders are enough — none of the tested helpers call them.
        for name in ("Update", "BotCommand", "InlineKeyboardButton", "InlineKeyboardMarkup"):
            setattr(telegram_mod, name, _make_stub_class(name))
        sys.modules["telegram"] = telegram_mod

    if "telegram.ext" not in sys.modules:
        telegram_ext_mod = types.ModuleType("telegram.ext")
        # ContextTypes.DEFAULT_TYPE and similar attribute access in type
        # annotations need to resolve, so use the metaclass-based stub.
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
# get_account_label — sanity check the existing fallback
# ---------------------------------------------------------------------------

def test_get_account_label_returns_uppercase_target():
    assert bot.get_account_label("live") == "LIVE"
    assert bot.get_account_label("paper") == "PAPER"
    # Anything that isn't "live" falls into the PAPER branch by design.
    assert bot.get_account_label("anything-else") == "PAPER"


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
    assert bot.get_strategy_label({"STRATEGY": raw}, "live") == expected


def test_get_strategy_label_strategy_name_alias():
    # STRATEGY_NAME is supported as a fallback to STRATEGY.
    assert bot.get_strategy_label({"STRATEGY_NAME": "vwap"}, "paper") == "VWAP"


def test_get_strategy_label_falls_back_for_unknown_strategy():
    # Unknown strategy → fall back to LIVE/PAPER per target.
    assert bot.get_strategy_label({"STRATEGY": "not-a-real-strategy"}, "live") == "LIVE"
    assert bot.get_strategy_label({"STRATEGY": ""}, "paper") == "PAPER"
    assert bot.get_strategy_label({}, "live") == "LIVE"


# ---------------------------------------------------------------------------
# format_target_options — the new helper used by /start help and BotCommand
# ---------------------------------------------------------------------------

def test_format_target_options_uses_strategy_labels(monkeypatch, tmp_path, restore_dotenv_values):
    live_env = _write_env(tmp_path, "live.env", STRATEGY="ict", BYBIT_API_KEY="x")
    paper_env = _write_env(tmp_path, "paper.env", STRATEGY="vwap", BYBIT_API_KEY="y")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(live_env))
    monkeypatch.setattr(bot, "PAPER_ENV_PATH", str(paper_env))

    assert bot.format_target_options() == "ICT|VWAP"


def test_format_target_options_falls_back_when_env_files_missing(monkeypatch, tmp_path, restore_dotenv_values):
    # Point both env paths at non-existent files. load_account_env returns
    # {} for missing files, so the helper should fall back to LIVE|PAPER.
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(tmp_path / "nope-live.env"))
    monkeypatch.setattr(bot, "PAPER_ENV_PATH", str(tmp_path / "nope-paper.env"))

    assert bot.format_target_options() == "LIVE|PAPER"


def test_format_target_options_falls_back_when_strategy_unset(monkeypatch, tmp_path, restore_dotenv_values):
    # Env files exist but contain no STRATEGY key.
    live_env = _write_env(tmp_path, "live.env", BYBIT_API_KEY="x")
    paper_env = _write_env(tmp_path, "paper.env", BYBIT_API_KEY="y")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(live_env))
    monkeypatch.setattr(bot, "PAPER_ENV_PATH", str(paper_env))

    assert bot.format_target_options() == "LIVE|PAPER"


def test_format_target_options_mixed_known_and_unknown(monkeypatch, tmp_path, restore_dotenv_values):
    live_env = _write_env(tmp_path, "live.env", STRATEGY="ict")
    paper_env = _write_env(tmp_path, "paper.env", STRATEGY="something-weird")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(live_env))
    monkeypatch.setattr(bot, "PAPER_ENV_PATH", str(paper_env))

    # ICT resolves; the unknown string falls back to PAPER for the paper target.
    assert bot.format_target_options() == "ICT|PAPER"


def test_format_target_options_custom_separator(monkeypatch, tmp_path, restore_dotenv_values):
    live_env = _write_env(tmp_path, "live.env", STRATEGY="ict")
    paper_env = _write_env(tmp_path, "paper.env", STRATEGY="vwap")
    monkeypatch.setattr(bot, "LIVE_ENV_PATH", str(live_env))
    monkeypatch.setattr(bot, "PAPER_ENV_PATH", str(paper_env))

    assert bot.format_target_options(separator=" / ") == "ICT / VWAP"


def test_format_target_options_swallows_unexpected_errors(monkeypatch):
    # If load_account_env raises something unexpected, the helper must not
    # propagate — the bot must keep starting up. It returns the safe default.
    def _boom(target: str):
        raise RuntimeError("unexpected env failure")

    monkeypatch.setattr(bot, "load_account_env", _boom)
    assert bot.format_target_options() == "LIVE|PAPER"
