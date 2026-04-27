"""Legacy compatibility shim — Bybit credentials only.

Do not hardcode credentials here.
Set BYBIT_TESTNET_API_KEY and BYBIT_TESTNET_API_SECRET in .env,
Colab userdata, GitHub secrets, or VM environment variables.
"""
from __future__ import annotations

import os

BYBIT_TESTNET_API_KEY = os.getenv("BYBIT_TESTNET_API_KEY", "")
BYBIT_TESTNET_API_SECRET = os.getenv("BYBIT_TESTNET_API_SECRET", "")
