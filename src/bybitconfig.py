import os

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

BASE_URL = "https://api.bybit.com"
SYMBOL = "BTCUSDT"
TIMEFRAME = "5m"

RISK_PER_TRADE = 0.01
MAX_DAILY_LOSS = 0.05

KZ_START_HOUR = 8
KZ_END_HOUR = 11

LIVE_TRADING = False

print(f"Configured symbol: {SYMBOL} @ {TIMEFRAME}")
print(f"Kill zone: {KZ_START_HOUR}:00-{KZ_END_HOUR}:00 UTC")
print(f"LIVE_TRADING={LIVE_TRADING}")
