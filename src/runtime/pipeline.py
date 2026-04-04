from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from src.runtime.orders import safe_place_order
from src.runtime.notify import notify_operator, send_via_alert_manager

logger = logging.getLogger(__name__)


def default_signal_builder(settings: dict) -> Dict[str, Any]:
    return {
        "symbol": settings.get("SYMBOL", "BTCUSDT"),
        "side": "buy",
        "qty": 1,
    }


def killzone_signal_builder(settings: dict) -> Dict[str, Any]:
    """
    Use the existing KillZoneScalperBot to generate a trade signal.
    This assumes core.automated_trading_loop.KillZoneScalperBot is wired correctly
    and uses BybitConnector under the hood for data.
    """
    from src.core.automated_trading_loop import KillZoneScalperBot

    api_key = settings.get("BYBIT_API_KEY")
    api_secret = settings.get("BYBIT_API_SECRET")
    mode = str(settings.get("MODE", "testnet")).lower()
    testnet = mode != "live"

    bot = KillZoneScalperBot(
        api_key=api_key,
        api_secret=api_secret,
        symbol=settings.get("SYMBOL", "BTC/USDT:USDT"),
        testnet=testnet,
    )

    signal, price, fvg_data = bot.analyze_market()
    if not signal:
        logger.info("KillZoneScalperBot returned no signal; staying flat.")
        return {
            "symbol": settings.get("SYMBOL", "BTCUSDT"),
            "side": "none",
            "qty": 0,
        }

    side = "buy" if signal.lower() == "long" else "sell"
    return {
        "symbol": settings.get("SYMBOL", "BTCUSDT"),
        "side": side,
        "qty": float(settings.get("MAX_QTY", 1) or 1),
        "meta": {
            "price": price,
            "fvg": fvg_data,
            "raw_signal": signal,
        },
    }


def run_pipeline(
    settings: dict,
    exchange_client: Any = None,
    telegram_client: Any = None,
    signal_builder: Optional[Callable[[dict], Dict[str, Any]]] = None,
) -> dict:
    """
    Thread 2 integration adapter.
    Uses killzone_signal_builder by default, but can be overridden in tests.
    """
    logger.info("Pipeline start")

    builder = signal_builder or killzone_signal_builder
    signal = builder(settings)

    logger.info("Generated signal: %s", signal)

    if signal.get("side") in ("none", "", None) or float(signal.get("qty", 0)) <= 0:
        logger.info("No actionable signal; skipping order placement.")
        result = {"status": "skipped", "reason": "no_signal", "signal": signal}
    else:
        result = safe_place_order(signal, settings, exchange_client)

    status = result.get("status", "unknown")
    reason = result.get("reason")
    symbol = signal.get("symbol", "?")
    side = signal.get("side", "?")
    qty = signal.get("qty", "?")

    message = f"Pipeline result: status={status} | symbol={symbol} | side={side} | qty={qty}"
    if reason:
        message += f" | reason={reason}"

    if telegram_client is not None:
        notify_operator(telegram_client, message)
    else:
        send_via_alert_manager(message)

    logger.info("Pipeline complete: %s", result)
    return {
        "signal": signal,
        "order_result": result,
    }
