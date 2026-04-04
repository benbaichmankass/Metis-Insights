from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from src.exchange.bybit_connector import BybitConnector
from src.exchange.binance_connector import BinanceConnector
from src.runtime.pipeline import run_pipeline
from src.runtime.validation import build_settings_from_env, validate_startup


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("src.main")


class BybitExchangeAdapter:
    """
    Thin adapter exposing place_order(**order) so it works with safe_place_order.
    Wraps the existing BybitConnector.
    """
    def __init__(self, connector: BybitConnector, symbol: str):
        self._connector = connector
        self._symbol = symbol

    def place_order(self, **order):
        side = order.get("side")
        qty = float(order.get("qty", 0))
        symbol = order.get("symbol", self._symbol)
        logger.info("BybitExchangeAdapter.place_order: %s %s %s", symbol, side, qty)
        if qty <= 0:
            raise ValueError(f"Invalid qty for adapter: {qty}")
        return self._connector.place_market_order(symbol, side, qty)



class BinanceExchangeAdapter:
    """
    Thin adapter exposing place_order(**order) so it works with safe_place_order.
    Wraps the BinanceConnector.
    """
    def __init__(self, connector, symbol: str):
        self._connector = connector
        self._symbol = symbol

    def place_order(self, **order):
        side = order.get("side")
        qty = float(order.get("qty", 0))
        symbol = order.get("symbol", self._symbol)
        logger.info("BinanceExchangeAdapter.place_order: %s %s %s", symbol, side, qty)
        if qty <= 0:
            raise ValueError(f"Invalid qty for adapter: {qty}")
        return self._connector.place_market_order(symbol, side, qty)


class DummyTelegramClient:
    def send_message(self, message: str):
        logger.info("DummyTelegramClient.send_message: %s", message)


def main() -> None:
    load_dotenv()
    settings = build_settings_from_env(os.environ)
    exchange_name = settings.get("EXCHANGE", "bybit").lower()

    # Force DRY_RUN by default for safety during Thread 2 integration.
    if not settings.get("DRY_RUN"):
        settings["DRY_RUN"] = "true"

    validate_startup(settings)
    logger.info("Startup validation passed. MODE=%s DRY_RUN=%s", settings.get("MODE"), settings.get("DRY_RUN"))

    mode = str(settings.get("MODE", "testnet")).lower()
    testnet = mode != "live"
    symbol = settings.get("SYMBOL", "BTC/USDT:USDT")

    if exchange_name == "binance":
        api_key = settings.get("BINANCE_API_KEY")
        api_secret = settings.get("BINANCE_API_SECRET")
        connector = BinanceConnector(api_key=api_key, api_secret=api_secret, testnet=testnet)
        exchange_client = BinanceExchangeAdapter(connector, symbol)
    else:
        api_key = settings.get("BYBIT_API_KEY")
        api_secret = settings.get("BYBIT_API_SECRET")
        connector = BybitConnector(api_key=api_key, api_secret=api_secret, testnet=testnet)
        exchange_client = BybitExchangeAdapter(connector, symbol)

    telegram_client = DummyTelegramClient()

    result = run_pipeline(
        settings=settings,
        exchange_client=exchange_client,
        telegram_client=telegram_client,
    )
    logger.info("Runtime result: %s", result)


if __name__ == "__main__":
    main()
