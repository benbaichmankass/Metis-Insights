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
    "TELEGRAM_BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",  # fake, shape-valid
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
    # Operator directive 2026-05-03 removed DRY_RUN/ALLOW_LIVE_TRADING/mode
    # from build_settings_from_env. Operator directive 2026-06-24 removed the
    # MAX_QTY / max_qty / MAX_POSITION_USD notional+quantity ceilings — they
    # are no longer in the settings dict. Updated to the current key set.
    #
    # HALT_FLAG_PATH added 2026-08-16 (Tier-3, operator-approved;
    # BL-20260813-ORDERS-HALT-CHECK-INERT-WITHOUT-SETTINGS-KEY). This
    # assertion is the reason the gap survived so long AND the reason it is
    # now safe: it pins the key set exactly, so the missing key was invisible
    # (nothing asserts a key that should exist), but a future removal of
    # HALT_FLAG_PATH will now fail here rather than silently re-disarming the
    # orders-layer kill switch. Do not relax this to a subset check.
    env = {**BASE_ENV}
    with pytest.MonkeyPatch().context() as mp:
        for k in list(os.environ.keys()):
            mp.delenv(k, raising=False)
        for k, v in env.items():
            mp.setenv(k, v)
        s = build_settings_from_env()
    assert set(s.keys()) == {
        "exchange", "symbol", "timeframe",
        "risk_per_trade", "log_level", "tick_interval", "loop",
        "MAX_DAILY_LOSS_USD", "MAX_OPEN_POSITIONS",
        "HALT_FLAG_PATH",
    }
    assert s["exchange"] == "bybit"
    assert s["risk_per_trade"] == 0.01
    assert s["tick_interval"] == 900
    assert s["loop"] is True


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
# Telegram DECOUPLED from trader liveness — Tier-3 operator-approved 2026-08-01
# (BL-20260801-TELEGRAM-CRED-CRASHLOOPS-MONEY-LOOP option (b)): a missing/malformed
# Telegram credential is a LOUD NON-FATAL warning, never a startup error. The prior
# hard-require crashlooped the money loop for ~85 min while killing its own alarm.
# ---------------------------------------------------------------------------

def test_telegram_token_missing_is_non_fatal():
    """Missing TELEGRAM_BOT_TOKEN must NOT halt the trader (option b)."""
    run(remove=["TELEGRAM_BOT_TOKEN"])  # must not raise


def test_telegram_chat_id_missing_is_non_fatal():
    """Missing TELEGRAM_CHAT_ID must NOT halt the trader (option b)."""
    run(remove=["TELEGRAM_CHAT_ID"])  # must not raise


def test_malformed_telegram_token_is_non_fatal():
    """A shape-invalid token (only the secret half pasted) must NOT halt the trader."""
    run(overrides={"TELEGRAM_BOT_TOKEN": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmno"})


def test_missing_telegram_flags_degraded_alerting(monkeypatch):
    """The degradation is surfaced (logged + WARN outcome for the app banner),
    not swallowed — the safety half of option (b)."""
    import src.runtime.validation as val

    calls = []
    monkeypatch.setattr(val, "_warn_degraded_alerting", lambda reason: calls.append(reason))
    run(remove=["TELEGRAM_BOT_TOKEN"])
    assert calls, "a missing Telegram credential must flag degraded alerting"
    assert "TELEGRAM_BOT_TOKEN" in calls[0]


def test_exchange_creds_stay_fatal_even_without_telegram():
    """The asymmetry: exchange creds are still required to trade CORRECTLY, so a
    missing one stays fatal even though Telegram no longer is."""
    with pytest.raises(EnvironmentError, match="BYBIT_API_KEY"):
        run(remove=["BYBIT_API_KEY", "TELEGRAM_BOT_TOKEN"])


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
# MAX_QTY validation — REMOVED 2026-06-24.
# The MAX_QTY (and MAX_POSITION_USD) notional/quantity ceilings were deleted
# (operator directive): position size is bounded only by balance+margin,
# risk-per-trade, and the exchange's own lot size. validate_startup no longer
# validates MAX_QTY, so a leftover MAX_QTY=0/-1/"lots" value is simply ignored
# rather than raising — the former zero/negative/non-float pins are gone.
# ---------------------------------------------------------------------------
