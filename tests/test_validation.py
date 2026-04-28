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
    }
    assert s["exchange"] == "binance"
    assert s["risk_per_trade"] == 0.01
    assert s["tick_interval"] == 900
    assert s["loop"] is True


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

def test_dry_run_false_without_allow_live_raises():
    with pytest.raises(EnvironmentError, match="ALLOW_LIVE_TRADING"):
        run(overrides={"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "false"})


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
