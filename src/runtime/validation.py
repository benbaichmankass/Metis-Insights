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
    # Paper-trading is intentionally not a supported mode. The bot trades live
    # on real exchange accounts; backtests are run via the dedicated backtest
    # CLI, not through this runtime path.
    #
    # MODE accepts the case-insensitive aliases ``live`` (the natural-language
    # form the operator was setting before BUG-031) and ``backtest`` in
    # addition to ``LIVE``/``BACKTEST``. We normalise to upper for the
    # downstream checks below.
    mode = _env("MODE").upper()
    if mode not in ("LIVE", "BACKTEST"):
        errors.append(f"MODE must be LIVE or BACKTEST, got {mode!r}")

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
    # Live is the **default** per CLAUDE.md "Autonomous live-trading rule":
    # the system trades autonomously and the safety rails are
    # `RiskManager` + `safe_place_order` + the `/halt` kill-switch.
    # The interlock therefore only fails closed in one direction:
    #
    #   DRY_RUN truthy  AND ALLOW_LIVE_TRADING truthy → contradiction; refuse
    #
    # Truthy is the broad set normalised by `trading_mode.is_live_truthy`
    # / `is_dry_truthy` — i.e. the literal "live" the operator was setting
    # before BUG-031 is now equivalent to "true".
    from src.runtime.trading_mode import is_dry_truthy, is_live_truthy
    dry_run_raw = _env("DRY_RUN")
    allow_live_raw = _env("ALLOW_LIVE_TRADING")
    dry_run_set = bool(dry_run_raw) and is_dry_truthy(dry_run_raw)
    allow_live_set = bool(allow_live_raw) and is_live_truthy(allow_live_raw)
    if dry_run_set and allow_live_set:
        errors.append(
            "DRY_RUN and ALLOW_LIVE_TRADING are both truthy — pick one. "
            f"(DRY_RUN={dry_run_raw!r}, ALLOW_LIVE_TRADING={allow_live_raw!r})"
        )

    # ---- MODE=LIVE requires either no DRY_RUN, or explicit ALLOW_LIVE ------
    # MODE=LIVE with DRY_RUN truthy is the contradictory state we still
    # refuse. MODE=LIVE with both flags unset is now valid (default-live).
    if mode == "LIVE" and dry_run_set and not allow_live_set:
        errors.append(
            "MODE=LIVE with DRY_RUN truthy is contradictory. "
            "Either unset DRY_RUN (default is live) or set MODE=BACKTEST."
        )

    # ---- Raise if any errors found -----------------------------------------
    if errors:
        msg = "Startup validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise EnvironmentError(msg)


def build_settings_from_env() -> dict:
    """
    Build a settings dict from validated environment variables.
    Call validate_startup() first.

    Live-mode flags are emitted under BOTH casings: the lowercase
    keys are kept for back-compat with callers that read settings
    like a typed config object; the UPPERCASE keys match
    safe_place_order()'s lookups (which expects env-var-style names).
    Without the uppercase aliases the per-tick order-layer interlock
    silently rejects every order with reason
    "ALLOW_LIVE_TRADING=true is required for live submission" even
    when the env was set correctly (S-012 hotfix).

    BUG-031: defaults are now **live** when the env vars are unset, and
    the truthy parser accepts the literal "live" alongside "true".
    """
    from src.runtime.trading_mode import (
        LIVE_DEFAULTS,
        is_dry_truthy,
        is_live_truthy,
    )
    dry_run_raw = _env("DRY_RUN") or LIVE_DEFAULTS["DRY_RUN"]
    allow_live_raw = _env("ALLOW_LIVE_TRADING") or LIVE_DEFAULTS["ALLOW_LIVE_TRADING"]
    dry_run_bool = is_dry_truthy(dry_run_raw)
    allow_live_bool = is_live_truthy(allow_live_raw)
    return {
        "exchange":           _env("EXCHANGE").lower(),
        "mode":               _env("MODE").upper(),
        "symbol":             _env("SYMBOL"),
        "timeframe":          _env("TIMEFRAME"),
        "risk_per_trade":     float(_env("RISK_PER_TRADE")),
        "max_qty":            float(_env("MAX_QTY")),
        "dry_run":            dry_run_bool,
        "allow_live_trading": allow_live_bool,
        "log_level":          _env("LOG_LEVEL") or "INFO",
        "tick_interval":      int(_env("TICK_INTERVAL_SECONDS") or "900"),
        "loop":               _env("LOOP").lower() == "true",
        # Hard order-layer risk guards — uppercase keys match safe_place_order() lookups.
        # None when unset; safe_place_order() skips the guard when value is None.
        "MAX_POSITION_USD":   _env("MAX_POSITION_USD") or None,
        "MAX_DAILY_LOSS_USD": _env("MAX_DAILY_LOSS_USD") or None,
        "MAX_OPEN_POSITIONS": _env("MAX_OPEN_POSITIONS") or None,
        # S-012 hotfix: uppercase aliases for the live-mode flags so
        # safe_place_order's _get_value(settings, "DRY_RUN", ...) /
        # _get_value(settings, "ALLOW_LIVE_TRADING", ...) lookups find
        # them. Without these the order layer treats every signal as
        # "ALLOW_LIVE_TRADING is unset".
        "DRY_RUN":            dry_run_bool,
        "ALLOW_LIVE_TRADING": allow_live_bool,
        "MAX_QTY":            float(_env("MAX_QTY")),
    }
