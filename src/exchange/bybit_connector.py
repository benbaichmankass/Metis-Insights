import ccxt
import pandas as pd
from datetime import datetime

class BybitConnector:
    """
    Updated Bybit connector for Unified Trading Account
    Works with Cross Margin and linear perpetual contracts
    """
    
    def __init__(self, api_key=None, api_secret=None, testnet=True):
        self.testnet = testnet

        self.exchange = ccxt.bybit({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'linear'
            }
        })

        if testnet:
            self.exchange.set_sandbox_mode(True)
            print("🧪 Connected to BYBIT TESTNET (Unified Trading Account)")
            print("   ✅ Using Cross Margin mode - Safe for testing!")
        else:
            print("⚡ Connected to BYBIT LIVE")
    
    def get_price(self, symbol='BTC/USDT:USDT'):
        """Get current market price"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            print(f"❌ Error fetching price: {e}")
            return None
    
    def get_ohlcv(self, symbol='BTC/USDT:USDT', timeframe='15m', limit=100):
        """Fetch candlestick data"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"❌ Error fetching OHLCV: {e}")
            return None
    
    def get_balance(self):
        """Get account balance - works with Unified account"""
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except Exception as e:
            print(f"❌ Error fetching balance: {e}")
            return None
    
    def place_market_order(self, symbol, side, amount, params=None):
        """Place market order on Unified account"""
        try:
            if params is None:
                params = {}
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side,
                amount=amount,
                params=params
            )
            mode = "TESTNET" if self.testnet else "LIVE"
            print(f"✅ [{mode}] Market {side.upper()}: {amount} {symbol}")
            return order
        except Exception as e:
            print(f"❌ Error placing order: {e}")
            return None

if __name__ == "__main__":
    print("✅ BybitConnector module loaded!")
    print("📡 Ready for Unified Trading Account with Cross Margin!")
