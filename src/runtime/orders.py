from __future__ import annotations

import logging
from typing import Any, Dict


logger = logging.getLogger(__name__)


def _get_value(settings: Any, key: str, default: Any = None) -> Any:
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def safe_place_order(order: Dict[str, Any], settings: Any, client: Any) -> dict[str, Any]:
    """
    Validate order payload before real submission.
    Returns a structured status dict for logging/tests.
    """
    if not isinstance(order, dict):
        raise ValueError("order must be a dictionary")

    symbol = str(order.get("symbol", "")).strip().upper()
    side = str(order.get("side", "")).strip().lower()
    qty_raw = order.get("qty", 0)

    if not symbol:
        raise RuntimeError("Order rejected: symbol is required.")

    if side not in {"buy", "sell"}:
        raise RuntimeError("Order rejected: side must be 'buy' or 'sell'.")

    try:
        qty = float(qty_raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"Order rejected: invalid qty={qty_raw!r}")

    if qty <= 0:
        raise RuntimeError(f"Order rejected: qty must be > 0, got {qty}")

    max_qty_raw = _get_value(settings, "MAX_QTY", None)
    if max_qty_raw not in (None, ""):
        max_qty = float(max_qty_raw)
        if qty > max_qty:
            raise RuntimeError(
                f"Order rejected: qty {qty} exceeds MAX_QTY {max_qty}"
            )

    if _as_bool(_get_value(settings, "DRY_RUN", "true")):
        logger.info("DRY_RUN enabled; simulated order: %s", order)
        return {
            "status": "simulated",
            "order": order,
        }

    logger.info("Submitting live/testnet order: %s", order)
    result = client.place_order(**order)
    return {
        "status": "submitted",
        "order": order,
        "exchange_result": result,
    }
