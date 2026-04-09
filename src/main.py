from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

from src.exchange.binance_connector import BinanceConnector
from src.exchange.bybit_connector import BybitConnector
from src.runtime.pipeline import run_pipeline
from src.runtime.validation import build_settings_from_env, validate_startup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("src.main")


class BybitExchangeAdapter:
    """Thin adapter so BybitConnector works with safe_place_order."""
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
    """Thin adapter so BinanceConnector works with safe_place_order."""
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


class _AlertManagerAdapter:
    """Wraps AlertManager.send_alert() as send_message() for pipeline compatibility."""
    def __init__(self, alert_manager):
        self._am = alert_manager

    def send_message(self, message: str):
        self._am.send_alert(message)


def _build_telegram_client():
    """Use real Telegram client if credentials are present, else fall back to dummy."""
    try:
        from src.bot.alert_manager import AlertManager
        am = AlertManager()
        if am.enabled:
            return _AlertManagerAdapter(am)
    except Exception as exc:
        logger.warning("Could not initialise real Telegram client: %s", exc)
    return DummyTelegramClient()


def _build_exchange_adapter(settings: dict):
    exchange_name = settings.get("EXCHANGE", "bybit").lower()
    mode = str(settings.get("MODE", "testnet")).lower()
    testnet = mode != "live"
    symbol = settings.get("SYMBOL", "BTCUSDT")

    if exchange_name == "binance":
        connector = BinanceConnector(
            api_key=settings.get("BINANCE_API_KEY"),
            api_secret=settings.get("BINANCE_API_SECRET"),
            testnet=testnet,
        )
        return BinanceExchangeAdapter(connector, symbol)

    connector = BybitConnector(
        api_key=settings.get("BYBIT_API_KEY"),
        api_secret=settings.get("BYBIT_API_SECRET"),
        testnet=testnet,
    )
    return BybitExchangeAdapter(connector, symbol)


def run_one_tick(settings: dict, exchange_client, telegram_client) -> dict:
    """Run a single pipeline tick and return the result."""
    result = run_pipeline(
        settings=settings,
        exchange_client=exchange_client,
        telegram_client=telegram_client,
    )
    logger.info("Tick result: %s", result)
    return result


def main() -> None:
    load_dotenv()
    settings = build_settings_from_env(os.environ)

    # Safety: force DRY_RUN true if not explicitly set
    if not settings.get("DRY_RUN"):
        settings["DRY_RUN"] = "true"

    validate_startup(settings)
    logger.info(
        "Startup validation passed. EXCHANGE=%s MODE=%s DRY_RUN=%s ALLOW_LIVE_TRADING=%s",
        settings.get("EXCHANGE"),
        settings.get("MODE"),
        settings.get("DRY_RUN"),
        settings.get("ALLOW_LIVE_TRADING"),
    )

    exchange_client = _build_exchange_adapter(settings)
    telegram_client = _build_telegram_client()

    # LOOP=false means run one tick only (used in Colab and tests)
    loop = str(os.environ.get("LOOP", "true")).strip().lower() not in {"false", "0", "no"}
    interval = int(os.environ.get("TICK_INTERVAL_SECONDS", "900"))

    if not loop:
        logger.info("LOOP=false: running single tick.")
        run_one_tick(settings, exchange_client, telegram_client)
        return

    logger.info("Starting continuous loop. TICK_INTERVAL_SECONDS=%s", interval)
    while True:
        try:
            run_one_tick(settings, exchange_client, telegram_client)
        except Exception as exc:
            logger.exception("Tick failed with unhandled exception: %s", exc)
            try:
                telegram_client.send_message(f"[ICT Bot] Tick error: {exc}")
            except Exception:
                pass
        logger.info("Sleeping %s seconds until next tick.", interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
