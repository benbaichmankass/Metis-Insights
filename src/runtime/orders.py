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
        return {
            "status": "failed_validation",
            "reason": "order must be a dictionary",
            "order": order,
        }

    symbol = str(order.get("symbol", "")).strip().upper()
    side = str(order.get("side", "")).strip().lower()
    qty_raw = order.get("qty", 0)

    if not symbol:
        return {
            "status": "failed_validation",
            "reason": "Order rejected: symbol is required.",
            "order": order,
        }

    if side not in {"buy", "sell"}:
        return {
            "status": "failed_validation",
            "reason": "Order rejected: side must be 'buy' or 'sell'.",
            "order": order,
        }

    try:
        qty = float(qty_raw)
    except (TypeError, ValueError):
        return {
            "status": "failed_validation",
            "reason": f"Order rejected: invalid qty={qty_raw!r}",
            "order": order,
        }

    if qty <= 0:
        return {
            "status": "failed_validation",
            "reason": f"Order rejected: qty must be > 0, got {qty}",
            "order": order,
        }

    max_qty_raw = _get_value(settings, "MAX_QTY", None)
    if max_qty_raw not in (None, ""):
        max_qty = float(max_qty_raw)
        if qty > max_qty:
            return {
                "status": "failed_validation",
                "reason": f"Order rejected: qty {qty} exceeds MAX_QTY {max_qty}",
                "order": order,
            }

    if _as_bool(_get_value(settings, "DRY_RUN", "true")):
        logger.info("DRY_RUN enabled; simulated order: %s", order)
        return {
            "status": "simulated",
            "order": order,
        }

    if not _as_bool(_get_value(settings, "ALLOW_LIVE_TRADING", "false")):
        logger.warning(
            "Live order blocked: DRY_RUN is false but ALLOW_LIVE_TRADING is not enabled. order=%s",
            order,
        )
        return {
            "status": "failed_validation",
            "reason": "ALLOW_LIVE_TRADING=true is required for live submission",
            "order": order,
        }

    logger.info("Submitting live/testnet order: %s", order)
    try:
        result = client.place_order(**order)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Exchange submission failed: %s | order=%s", exc, order)
        return {
            "status": "failed_exchange",
            "reason": str(exc),
            "order": order,
        }

    return {
        "status": "submitted",
        "order": order,
        "exchange_result": result,
    }
