import ccxt
import pandas as pd
from datetime import datetime


class BinanceConnector:
    """
    Simple Binance connector for spot trading via ccxt.
    Intended primarily for paper/dev trading (sandbox/demo).
    """

    def __init__(self, api_key=None, api_secret=None, testnet=True):
        self.testnet = testnet

        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })

        if testnet:
            try:
                self.exchange.set_sandbox_mode(True)
                print("🧪 Connected to BINANCE DEMO (sandbox mode)")
            except Exception as e:
                print(f"⚠️ Could not enable Binance sandbox mode: {e}")
        else:
            print("⚡ Connected to BINANCE LIVE")

    def get_price(self, symbol="BTC/USDT"):
        """Get current market price"
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker.get("last")
        except Exception as e:
            print(f"❌ [Binance] Error fetching price: {e}")
            return None

    def get_ohlcv(self, symbol="BTC/USDT", timeframe="15m", limit=100):
        """Fetch candlestick data"
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            print(f"❌ [Binance] Error fetching OHLCV: {e}")
            return None

    def get_balance(self):
        """Get account balance"
        """
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except Exception as e:
            print(f"❌ [Binance] Error fetching balance: {e}")
            return None

    def place_market_order(self, symbol, side, amount, params=None):
        """Place market order (spot)"
        """
        try:
            if params is None:
                params = {}
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side,
                amount=amount,
                params=params,
            )
            mode = "DEMO" if self.testnet else "LIVE"
            print(f"✅ [BINANCE {mode}] Market {side.upper()}: {amount} {symbol}")
            return order
        except Exception as e:
            print(f"❌ [Binance] Error placing order: {e}")
            raise


if __name__ == "__main__":
    print("✅ BinanceConnector module loaded!")
