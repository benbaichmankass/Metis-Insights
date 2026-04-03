from __future__ import annotations

from typing import Any, Iterable


def _get_value(settings: Any, key: str, default: Any = None) -> Any:
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


def _require_keys(settings: Any, required_keys: Iterable[str]) -> None:
    missing = []
    for key in required_keys:
        value = _get_value(settings, key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(key)

    if missing:
        raise RuntimeError(
            "Missing required settings: " + ", ".join(sorted(missing))
        )


def validate_startup(settings: Any) -> None:
    """
    Run startup checks before the bot begins any exchange or Telegram activity.
    Raises RuntimeError with a human-readable message if anything is invalid.
    """
    required = [
        "BYBIT_API_KEY",
        "BYBIT_API_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SYMBOL",
    ]
    _require_keys(settings, required)

    mode = str(_get_value(settings, "MODE", "testnet")).strip().lower()
    if mode not in {"testnet", "live"}:
        raise RuntimeError(
            f"Invalid MODE={mode!r}. Allowed values: 'testnet', 'live'."
        )

    symbol = str(_get_value(settings, "SYMBOL", "")).strip().upper()
    if not symbol:
        raise RuntimeError("SYMBOL must be a non-empty string.")

    timeframe = str(_get_value(settings, "TIMEFRAME", "15")).strip()
    if not timeframe:
        raise RuntimeError("TIMEFRAME must be set.")

    try:
        risk_per_trade = float(_get_value(settings, "RISK_PER_TRADE", 0.0))
    except (TypeError, ValueError):
        raise RuntimeError("RISK_PER_TRADE must be numeric.")

    if not (0 < risk_per_trade <= 0.02):
        raise RuntimeError(
            f"RISK_PER_TRADE must be between 0 and 0.02 inclusive, got {risk_per_trade}."
        )

    max_qty_raw = _get_value(settings, "MAX_QTY", None)
    if max_qty_raw not in (None, ""):
        try:
            max_qty = float(max_qty_raw)
        except (TypeError, ValueError):
            raise RuntimeError("MAX_QTY must be numeric if provided.")
        if max_qty <= 0:
            raise RuntimeError("MAX_QTY must be greater than 0 if provided.")

    dry_run = str(_get_value(settings, "DRY_RUN", "true")).strip().lower()
    if dry_run not in {"true", "false", "1", "0", "yes", "no"}:
        raise RuntimeError(
            "DRY_RUN must be one of: true, false, 1, 0, yes, no."
        )


def build_settings_from_env(environ: dict[str, str]) -> dict[str, str]:
    """
    Minimal env-to-settings helper for Thread 2.
    This keeps startup validation independent from the repo's final config model.
    """
    keys = [
        "BYBIT_API_KEY",
        "BYBIT_API_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "MODE",
        "SYMBOL",
        "TIMEFRAME",
        "RISK_PER_TRADE",
        "MAX_QTY",
        "DRY_RUN",
    ]
    return {key: environ.get(key, "") for key in keys}
