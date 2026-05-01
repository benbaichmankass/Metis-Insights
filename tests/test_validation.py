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
    "EXCHANGE":            "binance",
    "BINANCE_API_KEY":     "test-key",
    "BINANCE_API_SECRET":  "test-secret",
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

def test_binance_passes_without_bybit_keys():
    """EXCHANGE=binance must not require BYBIT keys."""
    run(remove=["BYBIT_API_KEY", "BYBIT_API_SECRET"])


def test_bybit_passes_without_binance_keys():
    """EXCHANGE=bybit must not require BINANCE keys."""
    run(
        overrides={
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "bk",
            "BYBIT_API_SECRET": "bs",
        },
        remove=["BINANCE_API_KEY", "BINANCE_API_SECRET"],
    )


def test_live_trading_interlock_allowed():
    run(overrides={"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"})


def test_build_settings_from_env_keys():
    env = {**BASE_ENV}
    with pytest.MonkeyPatch().context() as mp:
        for k in list(os.environ.keys()):
            mp.delenv(k, raising=False)
        for k, v in env.items():
            mp.setenv(k, v)
        s = build_settings_from_env()
    assert set(s.keys()) == {
        "exchange", "mode", "symbol", "timeframe",
        "risk_per_trade", "max_qty", "dry_run",
        "allow_live_trading", "log_level", "tick_interval", "loop",
        "MAX_POSITION_USD", "MAX_DAILY_LOSS_USD", "MAX_OPEN_POSITIONS",
        # S-012 hotfix: uppercase aliases for the live-mode flags so
        # safe_place_order's _get_value(settings, "DRY_RUN", ...) /
        # _get_value(settings, "ALLOW_LIVE_TRADING", ...) lookups find
        # them. MAX_QTY is the same value as max_qty, surfaced under
        # safe_place_order's expected key.
        "DRY_RUN", "ALLOW_LIVE_TRADING", "MAX_QTY",
    }
    assert s["exchange"] == "binance"
    assert s["risk_per_trade"] == 0.01
    assert s["tick_interval"] == 900
    assert s["loop"] is True
    # S-012 hotfix invariants: uppercase mirrors the lowercase value.
    assert s["DRY_RUN"] == s["dry_run"]
    assert s["ALLOW_LIVE_TRADING"] == s["allow_live_trading"]
    assert s["MAX_QTY"] == s["max_qty"]


# ---------------------------------------------------------------------------
# Binance credential requirements
# ---------------------------------------------------------------------------

def test_binance_requires_api_key():
    with pytest.raises(EnvironmentError, match="BINANCE_API_KEY"):
        run(remove=["BINANCE_API_KEY"])


def test_binance_requires_api_secret():
    with pytest.raises(EnvironmentError, match="BINANCE_API_SECRET"):
        run(remove=["BINANCE_API_SECRET"])


# ---------------------------------------------------------------------------
# Bybit credential requirements
# ---------------------------------------------------------------------------

def test_bybit_requires_api_key():
    with pytest.raises(EnvironmentError, match="BYBIT_API_KEY"):
        run(
            overrides={"EXCHANGE": "bybit", "BYBIT_API_SECRET": "bs"},
            remove=["BYBIT_API_KEY", "BINANCE_API_KEY", "BINANCE_API_SECRET"],
        )


def test_bybit_requires_api_secret():
    with pytest.raises(EnvironmentError, match="BYBIT_API_SECRET"):
        run(
            overrides={"EXCHANGE": "bybit", "BYBIT_API_KEY": "bk"},
            remove=["BYBIT_API_SECRET", "BINANCE_API_KEY", "BINANCE_API_SECRET"],
        )


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


def test_dry_run_and_allow_live_both_truthy_is_contradiction():
    """The only state validate_startup still refuses: both flags truthy."""
    with pytest.raises(EnvironmentError, match="both truthy"):
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
