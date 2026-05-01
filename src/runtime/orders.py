from __future__ import annotations

import logging
import os
from typing import Any, Dict


logger = logging.getLogger(__name__)


from src.runtime.trading_mode import (
    LIVE_DEFAULTS,
    is_dry_truthy,
    is_live_truthy,
)


def _get_value(settings: Any, key: str, default: Any = None) -> Any:
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


def _as_bool(value: Any) -> bool:
    # Back-compat shim: tests and external callers import this from
    # src.runtime.orders. The semantics are now "live truthy" (which
    # also accepts the literal string "live").
    return is_live_truthy(value)


def _resolve_price(order: Dict[str, Any]) -> float | None:
    price = order.get("price")
    if price is None:
        meta = order.get("meta") or {}
        price = meta.get("price") or meta.get("entry_price")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


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

    # Halt flag — checked before any risk math.
    halt_flag_path = _get_value(settings, "HALT_FLAG_PATH", None)
    if halt_flag_path and os.path.exists(halt_flag_path):
        logger.warning("Order blocked: halt flag active at %s", halt_flag_path)
        return {"status": "halted", "reason": "halt_flag_active", "order": order}

    # Hard risk guards — raise immediately; no soft fallback.
    max_pos_raw = _get_value(settings, "MAX_POSITION_USD", None)
    if max_pos_raw not in (None, ""):
        max_pos_usd = float(max_pos_raw)
        price = _resolve_price(order)
        if price is not None:
            notional_usd = qty * price
            if notional_usd > max_pos_usd:
                raise ValueError(
                    f"Order aborted: notional {notional_usd:.2f} USD exceeds MAX_POSITION_USD {max_pos_usd}"
                )

    max_daily_loss_raw = _get_value(settings, "MAX_DAILY_LOSS_USD", None)
    if max_daily_loss_raw not in (None, ""):
        max_daily_loss = float(max_daily_loss_raw)
        current_loss_raw = _get_value(settings, "CURRENT_DAILY_LOSS_USD", None)
        if current_loss_raw not in (None, ""):
            if float(current_loss_raw) >= max_daily_loss:
                raise ValueError(
                    f"Order aborted: daily loss {float(current_loss_raw):.2f} USD has reached"
                    f" MAX_DAILY_LOSS_USD {max_daily_loss}"
                )

    max_open_raw = _get_value(settings, "MAX_OPEN_POSITIONS", None)
    if max_open_raw not in (None, ""):
        max_open = int(float(max_open_raw))
        current_open_raw = _get_value(settings, "CURRENT_OPEN_POSITIONS", None)
        if current_open_raw not in (None, ""):
            if int(float(current_open_raw)) >= max_open:
                raise ValueError(
                    f"Order aborted: open positions {int(float(current_open_raw))}"
                    f" has reached MAX_OPEN_POSITIONS {max_open}"
                )

    # Per-strategy caps (S-005 M2): only applied when the order carries a strategy name.
    _strategy_name = (order.get("meta") or {}).get("strategy_name")
    if _strategy_name:
        max_strat_pos_raw = _get_value(settings, "MAX_POS_PER_STRATEGY", None)
        if max_strat_pos_raw not in (None, ""):
            max_strat_pos = int(float(max_strat_pos_raw))
            cur_strat_pos_raw = _get_value(settings, "STRATEGY_OPEN_POSITIONS", None)
            if cur_strat_pos_raw not in (None, ""):
                if int(float(cur_strat_pos_raw)) >= max_strat_pos:
                    return {
                        "status": "refused",
                        "reason": (
                            f"strategy '{_strategy_name}' open positions "
                            f"{int(float(cur_strat_pos_raw))} has reached "
                            f"MAX_POS_PER_STRATEGY {max_strat_pos}"
                        ),
                        "order": order,
                    }

        max_strat_loss_raw = _get_value(settings, "MAX_DAILY_LOSS_PER_STRATEGY_USD", None)
        if max_strat_loss_raw not in (None, ""):
            max_strat_loss = float(max_strat_loss_raw)
            cur_strat_pnl_raw = _get_value(settings, "STRATEGY_DAILY_PNL", None)
            if cur_strat_pnl_raw not in (None, ""):
                strat_loss = abs(min(0.0, float(cur_strat_pnl_raw)))
                if strat_loss >= max_strat_loss:
                    return {
                        "status": "refused",
                        "reason": (
                            f"strategy '{_strategy_name}' daily loss {strat_loss:.2f} USD "
                            f"has reached MAX_DAILY_LOSS_PER_STRATEGY_USD {max_strat_loss}"
                        ),
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

    # Default to live: DRY_RUN unset / ALLOW_LIVE_TRADING unset means
    # the system trades live. The risk manager + halt flag are the
    # safety rails (see CLAUDE.md "Autonomous live-trading rule").
    if is_dry_truthy(_get_value(settings, "DRY_RUN", LIVE_DEFAULTS["DRY_RUN"])):
        logger.info("DRY_RUN enabled; order not submitted: %s", order)
        return {
            "status": "dry_run",
            "order": order,
        }

    if not is_live_truthy(
        _get_value(settings, "ALLOW_LIVE_TRADING", LIVE_DEFAULTS["ALLOW_LIVE_TRADING"])
    ):
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
