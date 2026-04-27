import logging
import os
import ccxt
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

# NOTE — dual-library design (intentional):
#   BybitConnector (this file) uses ccxt for market data + order placement.
#   telegram_query_bot.py uses pybit for wallet balance + positions display.
#   Both read the same BYBIT_API_KEY / BYBIT_API_SECRET env vars.
#   BYBIT_TESTNET=true  -> sandbox mode
#   BYBIT_TESTNET=false -> live mode (default if var is missing)


def _read_testnet_flag() -> bool:
    raw = os.getenv("BYBIT_TESTNET", "false").strip().lower()
    return raw == "true"


class BybitConnector:
    """
    Bybit connector for Unified Trading Account.
    Works with Cross Margin and linear perpetual contracts.

    Testnet / live mode is controlled by the BYBIT_TESTNET environment
    variable.  Set BYBIT_TESTNET=false in .env.live and .env.paper for
    live trading.  If testnet param is omitted, the env var is read.
    """

    def __init__(self, api_key=None, api_secret=None, testnet=None):
        if testnet is None:
            testnet = _read_testnet_flag()
        self.testnet = testnet

        self.exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "linear"},
        })

        if testnet:
            self.exchange.set_sandbox_mode(True)

        dry_run_raw = os.getenv("DRY_RUN", "true").strip().lower()
        dry_run = dry_run_raw not in {"false", "0", "no"}
        allow_live_raw = os.getenv("ALLOW_LIVE_TRADING", "false").strip().lower()
        allow_live = allow_live_raw in {"true", "1", "yes"}

        logger.info("Bybit market data environment: %s", "testnet" if testnet else "mainnet")
        logger.info("Trading execution mode: %s", "dry-run" if dry_run else "live")
        logger.info("Live order placement allowed: %s", str(not dry_run and allow_live).lower())

    def get_price(self, symbol="BTC/USDT:USDT"):
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker["last"]
        except Exception as e:
            print(f"Error fetching price: {e}")
            return None

    def get_ohlcv(self, symbol="BTC/USDT:USDT", timeframe="15m", limit=100):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            print(f"Error fetching OHLCV: {e}")
            msg = str(e)
            if "Rate Limit" in msg or "Too many visits" in msg or 'retCode":10006' in msg:
                import time
                print("Rate limit hit, sleeping 15s...")
                time.sleep(15)
            return None

    def get_balance(self):
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            print(f"Error fetching balance: {e}")
            return None

    def place_market_order(self, symbol, side, amount, params=None):
        try:
            if params is None:
                params = {}
            order = self.exchange.create_market_order(symbol=symbol, side=side, amount=amount, params=params)
            mode = "TESTNET" if self.testnet else "LIVE"
            print(f"[{mode}] Market {side.upper()}: {amount} {symbol}")
            return order
        except Exception as e:
            print(f"Error placing order: {e}")
            return None


if __name__ == "__main__":
    print("BybitConnector loaded")
    print(f"Testnet from env: {_read_testnet_flag()}")
