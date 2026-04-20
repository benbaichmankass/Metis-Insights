from __future__ import annotations
from src.runtime.signal_writer import write_signal
from src.utils.signal_audit_logger import log_signal

import os
from dotenv import load_dotenv
if os.path.exists(".env.live"):
    load_dotenv(".env.live")
elif os.path.exists(".env.paper"):
    load_dotenv(".env.paper")

import logging
from typing import Any, Callable, Dict, Optional

import pandas as pd

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
            "strategy_name": "killzone",
        },
    }


def breakout_model_signal_builder(settings: dict) -> Dict[str, Any]:
    """Use the trained breakout confirmation model to generate live buy/skip decisions."""
    from src.strategies_manager import StrategyManager

    symbol = _killzone_symbol(settings)
    exchange = _build_killzone_exchange(settings)

    candles = exchange.fetch_ohlcv(symbol, "1m", limit=100)
    candles_df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    candles_df["datetime_utc"] = pd.to_datetime(candles_df["timestamp"], unit="ms", utc=True)

    manager = StrategyManager()
    model_signal = manager.get_signal("breakout_confirmation", candles_df)

    if model_signal.get("signal") not in ["CONFIRM", "STRONG_CONFIRM"]:
        logger.info("Breakout model returned non-actionable signal: %s", model_signal)
        return {
            "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
            "side": "none",
            "qty": 0,
            "meta": {
                "strategy_name": "breakout_confirmation",
                "model_signal": model_signal,
            },
        }

    risk_per_trade = float(
        settings.get("RISK_PER_TRADE", settings.get("risk_per_trade", 0.01)) or 0.01
    )
    fallback_qty = float(settings.get("MAX_QTY", settings.get("max_qty", 1)) or 1)

    atr = float(model_signal.get("atr_14", 0) or 0)
    if atr <= 0:
        qty = fallback_qty
    else:
        qty = fallback_qty

    return {
        "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
        "side": "buy",
        "qty": qty,
        "meta": {
            "strategy_name": "breakout_confirmation",
            "model_signal": model_signal,
            "prob_tp": model_signal.get("prob_tp"),
            "entry_price": model_signal.get("entry_price"),
            "atr_14": model_signal.get("atr_14"),
            "risk_per_trade": risk_per_trade,
        },
    }


def run_pipeline(
    settings: dict,
    exchange_client: Any = None,
    telegram_client: Any = None,
    signal_builder: Optional[Callable[[dict], Dict[str, Any]]] = None,
) -> dict:
    """Pipeline adapter. Chooses strategy from STRATEGY env var, defaults to killzone."""
    logger.info("Pipeline start")

    strategy_name = str(os.environ.get("STRATEGY", "killzone")).strip().lower()

    if signal_builder is not None:
        builder = signal_builder
    elif strategy_name == "breakout":
        builder = breakout_model_signal_builder
    else:
        builder = killzone_signal_builder

    logger.info("Using strategy builder: %s", strategy_name)
    signal = builder(settings)

    if signal.get("side") in ("buy", "sell"):
        meta = signal.get("meta", {}) or {}
        price = meta.get("price", meta.get("entry_price", signal.get("price")))

        write_signal(
            symbol=signal.get("symbol", "UNKNOWN"),
            signal_type="ml_breakout" if meta.get("strategy_name") == "breakout_confirmation" else ("fvg" if meta.get("fvg") else "trade_signal"),
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
