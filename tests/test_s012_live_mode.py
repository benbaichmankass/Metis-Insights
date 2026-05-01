"""S-012 PR E1 + BUG-031 contract: live-mode interlock tests.

Pins the contract documented in
``docs/claude/trading-mode-flags.md`` (BUG-031). Live trading is the
**default**; the safety rails are the per-account RiskManager and the
``/halt`` kill-switch (see CLAUDE.md "Autonomous live-trading rule").

The runtime now refuses to start in only one state:

    DRY_RUN truthy AND ALLOW_LIVE_TRADING truthy   → contradiction

It also refuses ``MODE=LIVE`` combined with a truthy ``DRY_RUN`` (since
that is also self-contradictory). Truthy is normalised by
``src.runtime.trading_mode.is_live_truthy`` / ``is_dry_truthy`` — the
literal "live" the operator was setting before BUG-031 is now treated
the same as "true".
"""
from __future__ import annotations

import pytest

from src.runtime.validation import validate_startup


_BASE_VALID_ENV = {
    "EXCHANGE": "bybit",
    "BYBIT_API_KEY": "k",
    "BYBIT_API_SECRET": "s",
    "TELEGRAM_BOT_TOKEN": "tt",
    "TELEGRAM_CHAT_ID": "1",
    "MODE": "LIVE",
    "SYMBOL": "BTCUSDT",
    "TIMEFRAME": "15m",
    "RISK_PER_TRADE": "0.01",
    "MAX_QTY": "1",
    "ALLOW_LIVE_TRADING": "true",
    "DRY_RUN": "false",  # default-live baseline
}


def _set_env(monkeypatch, **overrides):
    for key, value in {**_BASE_VALID_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, str(value))


def test_explicit_live_mode_passes(monkeypatch):
    """DRY_RUN=false + ALLOW_LIVE_TRADING=true → validates clean."""
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    validate_startup()


def test_dry_run_only_with_backtest_mode_passes(monkeypatch):
    """DRY_RUN=true + MODE=BACKTEST + ALLOW_LIVE unset → validates clean."""
    _set_env(
        monkeypatch,
        MODE="BACKTEST",
        DRY_RUN="true",
        ALLOW_LIVE_TRADING=None,
    )
    validate_startup()


def test_default_live_passes_when_both_unset(monkeypatch):
    """BUG-031: with both flags unset, the default is live and start succeeds."""
    _set_env(monkeypatch, DRY_RUN=None, ALLOW_LIVE_TRADING=None)
    validate_startup()


def test_dry_run_false_without_allow_live_passes(monkeypatch):
    """BUG-031: explicit DRY_RUN=false without ALLOW_LIVE_TRADING is now
    a valid live config (the system is default-live)."""
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING=None)
    validate_startup()


def test_dry_run_true_with_allow_live_true_is_contradiction(monkeypatch):
    """The single state validate_startup still refuses: both truthy."""
    _set_env(
        monkeypatch,
        MODE="BACKTEST",
        DRY_RUN="true",
        ALLOW_LIVE_TRADING="true",
    )
    with pytest.raises(EnvironmentError, match="both truthy"):
        validate_startup()


def test_mode_live_with_dry_run_truthy_is_contradiction(monkeypatch):
    """MODE=LIVE + DRY_RUN truthy + ALLOW_LIVE unset/false → contradictory."""
    _set_env(monkeypatch, MODE="LIVE", DRY_RUN="true", ALLOW_LIVE_TRADING=None)
    with pytest.raises(EnvironmentError, match="contradictory"):
        validate_startup()


def test_case_insensitive_dry_run_passes(monkeypatch):
    _set_env(monkeypatch, MODE="BACKTEST", DRY_RUN="TRUE", ALLOW_LIVE_TRADING=None)
    validate_startup()


def test_case_insensitive_allow_live(monkeypatch):
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="True")
    validate_startup()


def test_allow_live_accepts_literal_live(monkeypatch):
    """BUG-031: the operator's natural-language 'live' is accepted."""
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="live")
    validate_startup()


def test_dry_run_yes_is_recognised_as_dry(monkeypatch):
    """DRY_RUN=yes is a recognised dry-run alias.

    Combined with ALLOW_LIVE_TRADING=false (also dry-leaning), there is
    no contradiction — validate_startup accepts the staging config.
    """
    _set_env(
        monkeypatch,
        MODE="BACKTEST",
        DRY_RUN="yes",
        ALLOW_LIVE_TRADING="false",
    )
    validate_startup()
