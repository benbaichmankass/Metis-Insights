"""
src/runtime/validation.py

Startup validation for the ICT trading bot.
Exchange-aware: only the keys for the configured exchange are required.
"""
from __future__ import annotations

import os


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
    valid_exchanges = ("bybit",)
    if exchange not in valid_exchanges:
        errors.append(
            f"EXCHANGE must be one of {valid_exchanges}, got {exchange!r}"
        )
    else:
        # ---- Exchange-specific API keys ------------------------------------
        # Only require keys for the *configured* exchange.
        if exchange == "bybit":
            for key in _missing(["BYBIT_API_KEY", "BYBIT_API_SECRET"]):
                errors.append(f"Missing required Bybit credential: {key}")

    # ---- Telegram (always required, regardless of exchange) ----------------
    for key in _missing(["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]):
        errors.append(f"Missing required Telegram credential: {key}")

    # ---- Trading mode — REMOVED (operator directive 2026-05-03).
    # The MODE env var is no longer required. Per-account
    # ``mode: live | dry_run`` in ``config/accounts.yaml`` is the only
    # toggle. Backtests run via the dedicated backtest CLI, not this
    # runtime path, so a process-level MODE flag carried no information
    # the per-account config doesn't already encode.

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

    # NOTE: MAX_QTY / MAX_POSITION_USD are intentionally NOT validated or
    # required — they were arbitrary notional/quantity ceilings, removed
    # 2026-06-24 (operator directive). Position size is a pure function of
    # available balance+margin and risk-per-trade (per-account RiskManager),
    # bounded only by the exchange's own lot size. A leftover MAX_QTY /
    # MAX_POSITION_USD value in the environment is ignored.

    # ---- Hard order-layer risk guards (all optional; validated if set) -----
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

    # ---- DRY_RUN / ALLOW_LIVE_TRADING / MODE — REMOVED (operator directive 2026-05-03)
    # The single dry/live toggle in the codebase is per-account
    # ``mode: live | dry_run`` in ``config/accounts.yaml``, applied by
    # ``RiskManager.dry_run`` and checked inside ``RiskManager.evaluate()``.
    # Process-level interlocks were a recurring source of drift
    # (BUG-026, BUG-031, BUG-038) and have been removed entirely. The
    # ``MODE`` env var is no longer required either — the system runs
    # whatever ``config/accounts.yaml`` says, and ``BACKTEST`` mode is
    # invoked through the dedicated backtest CLI rather than this
    # runtime path.

    # ---- Raise if any errors found -----------------------------------------
    if errors:
        msg = "Startup validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise EnvironmentError(msg)


def build_settings_from_env() -> dict:
    """Build a settings dict from validated environment variables.

    Operator directive 2026-05-03: the dry/live mode is no longer in env.
    Per-account ``mode: live | dry_run`` in ``config/accounts.yaml`` is
    the single source of truth (see ``src/units/accounts/__init__.py``).
    This dict carries only the runtime parameters the trader needs that
    are NOT account-scoped (exchange selection, symbol/timeframe,
    process-level risk caps, log/tick params).
    """
    return {
        "exchange":           _env("EXCHANGE").lower(),
        "symbol":             _env("SYMBOL"),
        "timeframe":          _env("TIMEFRAME"),
        "risk_per_trade":     float(_env("RISK_PER_TRADE")),
        "log_level":          _env("LOG_LEVEL") or "INFO",
        "tick_interval":      int(_env("TICK_INTERVAL_SECONDS") or "60"),
        "loop":               _env("LOOP").lower() == "true",
        # Hard order-layer risk guards — uppercase keys match safe_place_order() lookups.
        # None when unset; safe_place_order() skips the guard when value is None.
        # (MAX_QTY / MAX_POSITION_USD removed 2026-06-24 — no notional/qty cap.)
        "MAX_DAILY_LOSS_USD": _env("MAX_DAILY_LOSS_USD") or None,
        "MAX_OPEN_POSITIONS": _env("MAX_OPEN_POSITIONS") or None,
    }
