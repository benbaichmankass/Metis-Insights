import os
import ccxt
import pandas as pd
from datetime import datetime


def _read_testnet_flag() -> bool:
    raw = os.getenv("BINANCE_TESTNET", "false").strip().lower()
    return raw == "true"


class BinanceConnector:
    """
    Binance connector for USDT-margined linear perpetual futures via ccxt.
    Default symbol: BTC/USDT:USDT  (futures, not spot).
    Testnet / live mode controlled by BINANCE_TESTNET env var.
    """

    def __init__(self, api_key=None, api_secret=None, testnet=None):
        if testnet is None:
            testnet = _read_testnet_flag()
        self.testnet = testnet

        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })

        if testnet:
            try:
                self.exchange.set_sandbox_mode(True)
                print("Testnet BINANCE FUTURES")
            except Exception as e:
                print(f"Could not enable Binance sandbox: {e}")
        else:
            print("LIVE BINANCE FUTURES")

    def get_price(self, symbol="BTC/USDT:USDT"):
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker.get("last")
        except Exception as e:
            print(f"[Binance] Error fetching price: {e}")
            return None

    def get_ohlcv(self, symbol="BTC/USDT:USDT", timeframe="15m", limit=100):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            print(f"[Binance] Error fetching OHLCV: {e}")
            return None

    def get_balance(self):
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            print(f"[Binance] Error fetching balance: {e}")
            return None

    def get_positions(self):
        """Return only positions with non-zero size."""
        try:
            positions = self.exchange.fetch_positions()
            return [p for p in positions if float(p.get("contracts", 0) or 0) > 0]
        except Exception as e:
            print(f"[Binance] Error fetching positions: {e}")
            return []

    def place_market_order(self, symbol, side, amount, params=None):
        try:
            if params is None:
                params = {}
            order = self.exchange.create_market_order(symbol=symbol, side=side, amount=amount, params=params)
            mode = "TESTNET" if self.testnet else "LIVE"
            print(f"[BINANCE {mode}] Market {side.upper()}: {amount} {symbol}")
            return order
        except Exception as e:
            print(f"[Binance] Error placing order: {e}")
            raise


if __name__ == "__main__":
    print("BinanceConnector loaded (futures mode)")
    print(f"Testnet from env: {_read_testnet_flag()}")
