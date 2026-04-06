from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from src.runtime.notify import notify_operator, send_via_alert_manager
from src.runtime.orders import safe_place_order

logger = logging.getLogger(__name__)


def default_signal_builder(settings: dict) -> Dict[str, Any]:
    return {
        "symbol": settings.get("SYMBOL", "BTCUSDT"),
        "side": "buy",
        "qty": 1,
    }


def _build_killzone_exchange(settings: dict):
    exchange_name = str(settings.get("EXCHANGE", "bybit")).strip().lower()
    mode = str(settings.get("MODE", "testnet")).strip().lower()
    testnet = mode != "live"

    if exchange_name == "binance":
        from src.exchange.binance_connector import BinanceConnector
        return BinanceConnector(
            api_key=settings.get("BINANCE_API_KEY"),
            api_secret=settings.get("BINANCE_API_SECRET"),
            testnet=testnet,
        )

    if exchange_name == "bybit":
        from src.exchange.bybit_connector import BybitConnector
        return BybitConnector(
            api_key=settings.get("BYBIT_API_KEY"),
            api_secret=settings.get("BYBIT_API_SECRET"),
            testnet=testnet,
        )

    raise ValueError(f"Unsupported EXCHANGE value: {exchange_name}")


def _killzone_symbol(settings: dict) -> str:
    configured = settings.get("SYMBOL")
    if configured:
        return configured

    exchange_name = str(settings.get("EXCHANGE", "bybit")).strip().lower()
    if exchange_name == "binance":
        return "BTC/USDT"

    return "BTC/USDT:USDT"


def killzone_signal_builder(settings: dict) -> Dict[str, Any]:
    """Use KillZoneScalperBot with the selected exchange connector for market data."""
    from src.core.automated_trading_loop import KillZoneScalperBot

    symbol = _killzone_symbol(settings)
    exchange = _build_killzone_exchange(settings)

    bot = KillZoneScalperBot(
        exchange=exchange,
        symbol=symbol,
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
            "exchange": str(settings.get("EXCHANGE", "bybit")).strip().lower(),
            "market_data_symbol": symbol,
        },
    }


def run_pipeline(
    settings: dict,
    exchange_client: Any = None,
    telegram_client: Any = None,
    signal_builder: Optional[Callable[[dict], Dict[str, Any]]] = None,
) -> dict:
    """Thread 2 integration adapter. Uses killzone_signal_builder by default."""
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
