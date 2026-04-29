"""S-012 PR E1: hard live-mode interlock tests.

Pins the contract documented in docs/audit/sprint-012/06-dry-run-surface.md
§ 6.5 — the ONLY path to live order placement is::

    DRY_RUN!=true  AND  ALLOW_LIVE_TRADING=true

The runtime must refuse to start in any other configuration where it
might attempt live trading. Specifically, an unset DRY_RUN must NOT
silently bypass the interlock.
"""
from __future__ import annotations

import pytest

from src.runtime.validation import validate_startup


# ---------------------------------------------------------------------------
# Baseline valid env — all required keys present, neutral live-trading vars
# ---------------------------------------------------------------------------


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
    "DRY_RUN": "true",  # default to staging in the baseline
}


def _set_env(monkeypatch, **overrides):
    """Apply the baseline env then any per-test overrides.

    A None override removes the variable from the environment entirely
    (used to test 'unset' cases).
    """
    for key, value in {**_BASE_VALID_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, str(value))


# ---------------------------------------------------------------------------
# DoD case 1 — explicit live mode passes
# ---------------------------------------------------------------------------


def test_explicit_live_mode_passes(monkeypatch):
    """DRY_RUN=false + ALLOW_LIVE_TRADING=true → validates clean."""
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    validate_startup()


def test_dry_run_true_passes_without_allow_live(monkeypatch):
    """DRY_RUN=true makes ALLOW_LIVE_TRADING irrelevant for the interlock.

    (MODE=BACKTEST also drops the MODE=LIVE side-gate — test that combo
    so we're not also tripping a different rule.)
    """
    _set_env(
        monkeypatch,
        MODE="BACKTEST",
        DRY_RUN="true",
        ALLOW_LIVE_TRADING="false",
    )
    validate_startup()


# ---------------------------------------------------------------------------
# DoD case 2 — explicit DRY_RUN=false WITHOUT ALLOW_LIVE_TRADING fails
# ---------------------------------------------------------------------------


def test_dry_run_false_without_allow_live_raises(monkeypatch):
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="false")
    with pytest.raises(EnvironmentError, match="ALLOW_LIVE_TRADING=true"):
        validate_startup()


def test_dry_run_false_with_unset_allow_live_raises(monkeypatch):
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING=None)
    with pytest.raises(EnvironmentError, match="ALLOW_LIVE_TRADING=true"):
        validate_startup()


# ---------------------------------------------------------------------------
# DoD case 3 — UNSET DRY_RUN must not silently downgrade to live
# ---------------------------------------------------------------------------


def test_unset_dry_run_with_unset_allow_live_raises(monkeypatch):
    """The hole that S-012 PR E1 closes: unset DRY_RUN + unset
    ALLOW_LIVE_TRADING used to bypass the interlock.
    """
    _set_env(monkeypatch, DRY_RUN=None, ALLOW_LIVE_TRADING=None)
    with pytest.raises(EnvironmentError, match="ALLOW_LIVE_TRADING=true"):
        validate_startup()


def test_unset_dry_run_with_explicit_allow_live_passes(monkeypatch):
    """Unset DRY_RUN + ALLOW_LIVE_TRADING=true is the legitimate live path."""
    _set_env(monkeypatch, DRY_RUN=None, ALLOW_LIVE_TRADING="true")
    validate_startup()


# ---------------------------------------------------------------------------
# DoD case 4 — MODE=LIVE without ALLOW_LIVE_TRADING also fails
# ---------------------------------------------------------------------------


def test_mode_live_without_allow_live_raises(monkeypatch):
    """MODE=LIVE separately requires ALLOW_LIVE_TRADING=true.

    Even when DRY_RUN=true (so the live-order interlock is satisfied),
    declaring LIVE mode without acknowledging live trading is a
    misconfiguration the runtime refuses.
    """
    _set_env(monkeypatch, MODE="LIVE", DRY_RUN="true", ALLOW_LIVE_TRADING="false")
    with pytest.raises(EnvironmentError, match="MODE=LIVE requires ALLOW_LIVE_TRADING=true"):
        validate_startup()


# ---------------------------------------------------------------------------
# Edge cases — case-insensitivity + ambiguous values
# ---------------------------------------------------------------------------


def test_case_insensitive_dry_run(monkeypatch):
    _set_env(monkeypatch, DRY_RUN="TRUE")
    validate_startup()


def test_case_insensitive_allow_live(monkeypatch):
    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="True")
    validate_startup()


def test_dry_run_garbage_value_treated_as_not_true(monkeypatch):
    """DRY_RUN=yes is not 'true' → interlock fires unless live is granted."""
    _set_env(monkeypatch, DRY_RUN="yes", ALLOW_LIVE_TRADING="false")
    with pytest.raises(EnvironmentError, match="ALLOW_LIVE_TRADING=true"):
        validate_startup()
