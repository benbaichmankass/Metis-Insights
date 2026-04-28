"""
src/runtime/validation.py

Startup validation for the ICT trading bot.
Exchange-aware: only the keys for the configured exchange are required.
"""
from __future__ import annotations

import os
from typing import Any


def _env(key: str) -> str:
    """Return stripped env-var value or empty string."""
    return os.environ.get(key, "").strip()


def _missing(keys: list) -> list:
    """Return subset of keys that are absent/empty in the environment."""
    return [k for k in keys if not _env(k)]


def validate_startup() -> None:
    """
    Validate all required environment variables before the bot starts.

    Raises EnvironmentError if any required variable is missing or invalid.
    """
    errors: list = []

    # ---- Exchange selection ------------------------------------------------
    exchange = _env("EXCHANGE").lower()
    valid_exchanges = ("binance", "bybit")
    if exchange not in valid_exchanges:
        errors.append(
            f"EXCHANGE must be one of {valid_exchanges}, got {exchange!r}"
        )
    else:
        # ---- Exchange-specific API keys ------------------------------------
        # Only require keys for the *configured* exchange.
        if exchange == "binance":
            for key in _missing(["BINANCE_API_KEY", "BINANCE_API_SECRET"]):
                errors.append(f"Missing required Binance credential: {key}")
        elif exchange == "bybit":
            for key in _missing(["BYBIT_API_KEY", "BYBIT_API_SECRET"]):
                errors.append(f"Missing required Bybit credential: {key}")

    # ---- Telegram (always required, regardless of exchange) ----------------
    for key in _missing(["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]):
        errors.append(f"Missing required Telegram credential: {key}")

    # ---- Trading mode ------------------------------------------------------
    mode = _env("MODE").upper()
    if mode not in ("LIVE", "PAPER", "BACKTEST"):
        errors.append(f"MODE must be LIVE, PAPER, or BACKTEST, got {mode!r}")

    # ---- Symbol & timeframe ------------------------------------------------
    if not _env("SYMBOL"):
        errors.append("SYMBOL is required (e.g. BTCUSDT)")
    if not _env("TIMEFRAME"):
        errors.append("TIMEFRAME is required (e.g. 15m)")

    # ---- Risk management ---------------------------------------------------
    risk_raw = _env("RISK_PER_TRADE")
    if not risk_raw:
        errors.append("RISK_PER_TRADE is required")
    else:
        try:
            risk = float(risk_raw)
            if not (0 < risk <= 1):
                errors.append(
                    f"RISK_PER_TRADE must be between 0 (exclusive) and 1 (inclusive), "
                    f"got {risk}"
                )
        except ValueError:
            errors.append(f"RISK_PER_TRADE must be a float, got {risk_raw!r}")

    max_qty_raw = _env("MAX_QTY")
    if not max_qty_raw:
        errors.append("MAX_QTY is required")
    else:
        try:
            max_qty = float(max_qty_raw)
            if max_qty <= 0:
                errors.append(f"MAX_QTY must be > 0, got {max_qty}")
        except ValueError:
            errors.append(f"MAX_QTY must be a float, got {max_qty_raw!r}")

    # ---- Hard order-layer risk guards (all optional; validated if set) -----
    _max_pos_raw = _env("MAX_POSITION_USD")
    if _max_pos_raw:
        try:
            if float(_max_pos_raw) <= 0:
                errors.append(f"MAX_POSITION_USD must be > 0, got {_max_pos_raw!r}")
        except ValueError:
            errors.append(f"MAX_POSITION_USD must be a positive number, got {_max_pos_raw!r}")

    _max_daily_loss_raw = _env("MAX_DAILY_LOSS_USD")
    if _max_daily_loss_raw:
        try:
            if float(_max_daily_loss_raw) <= 0:
                errors.append(f"MAX_DAILY_LOSS_USD must be > 0, got {_max_daily_loss_raw!r}")
        except ValueError:
            errors.append(f"MAX_DAILY_LOSS_USD must be a positive number, got {_max_daily_loss_raw!r}")

    _max_open_raw = _env("MAX_OPEN_POSITIONS")
    if _max_open_raw:
        try:
            if int(float(_max_open_raw)) <= 0:
                errors.append(f"MAX_OPEN_POSITIONS must be > 0, got {_max_open_raw!r}")
        except ValueError:
            errors.append(f"MAX_OPEN_POSITIONS must be a positive integer, got {_max_open_raw!r}")

    # ---- DRY_RUN / live-trading interlock ----------------------------------
    dry_run = _env("DRY_RUN").lower()
    allow_live = _env("ALLOW_LIVE_TRADING").lower()
    if dry_run == "false" and allow_live != "true":
        errors.append(
            "DRY_RUN=false requires ALLOW_LIVE_TRADING=true "
            "(set explicitly to enable real order placement)"
        )

    # ---- MODE=LIVE requires explicit live-trading gate ---------------------
    # Fail closed: MODE=LIVE without ALLOW_LIVE_TRADING=true is rejected at
    # startup even if DRY_RUN is also set, to prevent misconfiguration from
    # silently running in a live-adjacent state.
    if mode == "LIVE" and allow_live != "true":
        errors.append(
            "MODE=LIVE requires ALLOW_LIVE_TRADING=true "
            "(set explicitly to acknowledge live order placement)"
        )

    # ---- Raise if any errors found -----------------------------------------
    if errors:
        msg = "Startup validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise EnvironmentError(msg)


def build_settings_from_env() -> dict:
    """
    Build a settings dict from validated environment variables.
    Call validate_startup() first.
    """
    return {
        "exchange":           _env("EXCHANGE").lower(),
        "mode":               _env("MODE").upper(),
        "symbol":             _env("SYMBOL"),
        "timeframe":          _env("TIMEFRAME"),
        "risk_per_trade":     float(_env("RISK_PER_TRADE")),
        "max_qty":            float(_env("MAX_QTY")),
        "dry_run":            _env("DRY_RUN").lower() == "true",
        "allow_live_trading": _env("ALLOW_LIVE_TRADING").lower() == "true",
        "log_level":          _env("LOG_LEVEL") or "INFO",
        "tick_interval":      int(_env("TICK_INTERVAL_SECONDS") or "900"),
        "loop":               _env("LOOP").lower() == "true",
        # Hard order-layer risk guards — uppercase keys match safe_place_order() lookups.
        # None when unset; safe_place_order() skips the guard when value is None.
        "MAX_POSITION_USD":   _env("MAX_POSITION_USD") or None,
        "MAX_DAILY_LOSS_USD": _env("MAX_DAILY_LOSS_USD") or None,
        "MAX_OPEN_POSITIONS": _env("MAX_OPEN_POSITIONS") or None,
    }
