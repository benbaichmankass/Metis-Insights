\"\"\"Local Bybit credential presence check.

This script intentionally does not print key values or call Bybit.
\"\"\"
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

api_key = os.getenv("BYBIT_API_KEY") or os.getenv("BYBIT_TESTNET_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET") or os.getenv("BYBIT_TESTNET_API_SECRET")

missing = []
if not api_key:
    missing.append("BYBIT_API_KEY or BYBIT_TESTNET_API_KEY")
if not api_secret:
    missing.append("BYBIT_API_SECRET or BYBIT_TESTNET_API_SECRET")

if missing:
    raise SystemExit("Missing environment variables: " + ", ".join(missing))

print("Bybit credential environment variables are present. Values were not printed.")
