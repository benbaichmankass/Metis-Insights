# ICT Trading Bot - Bybit Testnet Config
import os
from typing import Optional

# Testnet API credentials (get from Bybit testnet dashboard)
API_KEY = os.getenv("BYBIT_TESTNET_API_KEY", "your_testnet_api_key_here")
API_SECRET = os.getenv("BYBIT_TESTNET_API_SECRET", "your_testnet_api_secret_here")

# Testnet base URLs
TESTNET_BASE_URL = "https://api-testnet.bybit.com"
FUNDING_WALLET = "unified"

# Trading params
SYMBOL = "BTCUSDT"
TIMEFRAME = "5m"
RISK_PER_TRADE = 0.01  # 1% risk per trade
MAX_DAILY_LOSS = 0.05  # 5% max daily loss

# Kill Zone (London session)
KZ_START_HOUR = 8   # UTC
KZ_END_HOUR = 11    # UTC

# Current balance (will be updated live)
USDT_BALANCE: Optional[float] = None

print(f"Configured for testnet: {SYMBOL} @ {TIMEFRAME}")
print(f"Kill zone: {KZ_START_HOUR}:00-{KZ_END_HOUR}:00 UTC")
