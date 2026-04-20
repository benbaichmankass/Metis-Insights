from __future__ import annotations
from src.runtime.signal_writer import write_signal
from src.utils.signal_audit_logger import log_signal


# Env fallback for .env.live / .env.paper
import os
from dotenv import load_dotenv
if os.path.exists(".env.live"):
    load_dotenv(".env.live")
elif os.path.exists(".env.paper"):
    load_dotenv(".env.paper")



# Env fallback for .env.live / .env.paper
import os
from dotenv import load_dotenv
if os.path.exists(".env.live"):
    load_dotenv(".env.live")
elif os.path.exists(".env.paper"):
    load_dotenv(".env.paper")


import logging
from typing import Any, Callable, Dict, Optional

from src.runtime.notify import notify_operator, send_via_alert_manager
from src.runtime.orders import safe_place_order

logger = logging.getLogger(__name__)


def default_signal_builder(settings: dict) -> Dict[str, Any]:
    return {
        "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
        "side": "buy",
        "qty": 1,
    }


def _build_killzone_exchange(settings: dict):
    exchange_name = str(settings.get("EXCHANGE", settings.get("exchange", "bybit"))).strip().lower()
    bybit_testnet_raw = str(__import__("os").environ.get("BYBIT_TESTNET", "true")).strip().lower()
    testnet = bybit_testnet_raw not in {"false", "0", "no"}

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

    exchange_name = str(settings.get("EXCHANGE", settings.get("exchange", "bybit"))).strip().lower()
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
            "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
            "side": "none",
            "qty": 0,
        }

    side = "buy" if signal.lower() == "long" else "sell"

    return {
        "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
        "side": side,
        "qty": float(settings.get("MAX_QTY", settings.get("max_qty", 1)) or 1),
        "meta": {
            "price": price,
            "fvg": fvg_data,
            "raw_signal": signal,
            "exchange": str(settings.get("EXCHANGE", settings.get("exchange", "bybit"))).strip().lower(),
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

    if signal.get("side") in ("buy", "sell"):
        meta = signal.get("meta", {}) or {}
        price = meta.get("price", signal.get("price"))

        write_signal(
            symbol=signal.get("symbol", "UNKNOWN"),
            signal_type="fvg" if meta.get("fvg") else "trade_signal",
            direction="bullish" if signal.get("side") == "buy" else "bearish",
            price=float(price) if price is not None else None,
            timeframe=settings.get("TIMEFRAME", settings.get("timeframe", "unknown")),
            reason="Actionable pipeline signal",
            metadata=str(signal),
        )

    logger.info("Generated signal: %s", signal)

    if signal.get("side") in ("none", "", None) or float(signal.get("qty", 0)) <= 0:
        logger.info("No actionable signal; skipping order placement.")
        result = {"status": "skipped", "reason": "no_signal", "signal": signal}
    else:
        result = safe_place_order(signal, settings, exchange_client)
    # Audit log of every pipeline result
    try:
        log_signal(
            {
                "event": "pipeline_result",
                "symbol": signal.get("symbol"),
                "side": signal.get("side"),
                "qty": signal.get("qty"),
                "status": result.get("status"),
                "reason": result.get("reason"),
            }
        )
    except Exception:
        # Never let audit logging break the trading loop
        pass


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
