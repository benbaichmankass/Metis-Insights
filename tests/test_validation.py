"""
tests/test_validation.py

Unit tests for src/runtime/validation.py
-- exchange-aware key validation
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.runtime.validation import validate_startup, build_settings_from_env


# ---------------------------------------------------------------------------
# Helper: run validate_startup() with a controlled environment
# ---------------------------------------------------------------------------

BASE_ENV = {
    "EXCHANGE":            "bybit",
    "BYBIT_API_KEY":       "test-key",
    "BYBIT_API_SECRET":    "test-secret",
    "TELEGRAM_BOT_TOKEN": "tok",
    "TELEGRAM_CHAT_ID":   "123",
    "MODE":               "BACKTEST",
    "SYMBOL":             "BTCUSDT",
    "TIMEFRAME":          "15m",
    "RISK_PER_TRADE":     "0.01",
    "MAX_QTY":            "0.001",
    "DRY_RUN":            "true",
    "ALLOW_LIVE_TRADING": "false",
    "LOG_LEVEL":          "INFO",
    "TICK_INTERVAL_SECONDS": "900",
    "LOOP":               "true",
}


def run(overrides=None, remove=None):
    env = {**BASE_ENV, **(overrides or {})}
    for key in (remove or []):
        env.pop(key, None)
    with pytest.MonkeyPatch().context() as mp:
        # Clear ALL env vars first so nothing leaks from the Colab env
        for k in list(os.environ.keys()):
            mp.delenv(k, raising=False)
        for k, v in env.items():
            mp.setenv(k, v)
        validate_startup()


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------

def test_bybit_passes_with_keys():
    """EXCHANGE=bybit passes with its key pair."""
    run()


def test_live_trading_interlock_allowed():
    run(overrides={"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"})


def test_build_settings_from_env_keys():
    # (b) OUTDATED CONTRACT — operator directive 2026-05-03 removed
    # DRY_RUN, ALLOW_LIVE_TRADING, mode, dry_run, allow_live_trading from
    # build_settings_from_env (validation.py:115-157).  The S-012 uppercase-
    # alias fix was superseded.  Updated to reflect the current key set.
    env = {**BASE_ENV}
    with pytest.MonkeyPatch().context() as mp:
        for k in list(os.environ.keys()):
            mp.delenv(k, raising=False)
        for k, v in env.items():
            mp.setenv(k, v)
        s = build_settings_from_env()
    assert set(s.keys()) == {
        "exchange", "symbol", "timeframe",
        "risk_per_trade", "max_qty", "log_level", "tick_interval", "loop",
        "MAX_POSITION_USD", "MAX_DAILY_LOSS_USD", "MAX_OPEN_POSITIONS",
        # MAX_QTY uppercase alias retained: safe_place_order still looks up
        # MAX_QTY from settings (orders.py:203).
        "MAX_QTY",
    }
    assert s["exchange"] == "bybit"
    assert s["risk_per_trade"] == 0.01
    assert s["tick_interval"] == 900
    assert s["loop"] is True
    assert s["MAX_QTY"] == s["max_qty"]


# ---------------------------------------------------------------------------
# Bybit credential requirements
# ---------------------------------------------------------------------------

def test_bybit_requires_api_key():
    with pytest.raises(EnvironmentError, match="BYBIT_API_KEY"):
        run(remove=["BYBIT_API_KEY"])


def test_bybit_requires_api_secret():
    with pytest.raises(EnvironmentError, match="BYBIT_API_SECRET"):
        run(remove=["BYBIT_API_SECRET"])


# ---------------------------------------------------------------------------
# Telegram always required
# ---------------------------------------------------------------------------

def test_telegram_token_always_required():
    with pytest.raises(EnvironmentError, match="TELEGRAM_BOT_TOKEN"):
        run(remove=["TELEGRAM_BOT_TOKEN"])


def test_telegram_chat_id_always_required():
    with pytest.raises(EnvironmentError, match="TELEGRAM_CHAT_ID"):
        run(remove=["TELEGRAM_CHAT_ID"])


# ---------------------------------------------------------------------------
# Invalid EXCHANGE
# ---------------------------------------------------------------------------

def test_invalid_exchange_raises():
    with pytest.raises(EnvironmentError, match="EXCHANGE"):
        run(overrides={"EXCHANGE": "kraken"})


# ---------------------------------------------------------------------------
# DRY_RUN interlock
# ---------------------------------------------------------------------------

def test_dry_run_false_without_allow_live_passes():
    """BUG-031: live is the default. DRY_RUN=false with ALLOW_LIVE_TRADING=false
    used to require an explicit opt-in. Per the operator rule
    (CLAUDE.md: 'default is live'), this is now a valid live config and
    validate_startup must accept it.
    """
    run(overrides={"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "false"})


def test_dry_run_and_allow_live_both_truthy_is_no_longer_checked():
    """(b) OUTDATED CONTRACT — operator directive 2026-05-03 removed the
    DRY_RUN+ALLOW_LIVE_TRADING contradiction check from validate_startup
    (validation.py:115-125).  The per-account accounts.yaml ``mode`` field
    is the sole toggle; process-level interlocks were removed to eliminate
    BUG-026/031/038 drift.  validate_startup must now ACCEPT this
    combination without raising."""
    # Must not raise — the interlock is gone.
    run(overrides={"DRY_RUN": "true", "ALLOW_LIVE_TRADING": "true",
                   "MODE": "BACKTEST"})


def test_allow_live_accepts_literal_live_string():
    """BUG-031: validate_startup must accept the natural-language 'live'."""
    run(overrides={"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "live"})


# ---------------------------------------------------------------------------
# RISK_PER_TRADE validation
# ---------------------------------------------------------------------------

def test_risk_per_trade_zero_raises():
    with pytest.raises(EnvironmentError, match="RISK_PER_TRADE"):
        run(overrides={"RISK_PER_TRADE": "0"})


def test_risk_per_trade_above_one_raises():
    with pytest.raises(EnvironmentError, match="RISK_PER_TRADE"):
        run(overrides={"RISK_PER_TRADE": "1.5"})


def test_risk_per_trade_non_float_raises():
    with pytest.raises(EnvironmentError, match="RISK_PER_TRADE"):
        run(overrides={"RISK_PER_TRADE": "abc"})


# ---------------------------------------------------------------------------
# MAX_QTY validation
# ---------------------------------------------------------------------------

def test_max_qty_zero_raises():
    with pytest.raises(EnvironmentError, match="MAX_QTY"):
        run(overrides={"MAX_QTY": "0"})


def test_max_qty_negative_raises():
    with pytest.raises(EnvironmentError, match="MAX_QTY"):
        run(overrides={"MAX_QTY": "-1"})


def test_max_qty_non_float_raises():
    with pytest.raises(EnvironmentError, match="MAX_QTY"):
        run(overrides={"MAX_QTY": "lots"})
