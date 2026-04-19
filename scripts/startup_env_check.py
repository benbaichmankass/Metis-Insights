
import os, sys, requests
from dotenv import load_dotenv

load_dotenv("/home/ubuntu/ict-trading-bot/.env")

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

REQUIRED_STRINGS = ["MODE", "SYMBOL", "TIMEFRAME", "EXCHANGE"]
REQUIRED_FLOATS  = ["RISK_PER_TRADE", "MAX_QTY"]
SAFETY_FLAGS     = ["DRY_RUN", "ALLOW_LIVE_TRADING", "BYBIT_TESTNET"]

lines  = ["*ICT Trader - VM Startup Check*", ""]
issues = []

for key in REQUIRED_STRINGS:
    val = os.getenv(key, "")
    ok  = bool(val)
    lines.append(("OK " if ok else "MISSING ") + key + " = " + (val or "NOT SET"))
    if not ok:
        issues.append(key)

for key in REQUIRED_FLOATS:
    val = os.getenv(key, "")
    try:
        float(val)
        lines.append("OK " + key + " = " + val)
    except Exception:
        lines.append("INVALID " + key + " = " + (val or "NOT SET"))
        issues.append(key)

lines.append("")
for key in SAFETY_FLAGS:
    val = os.getenv(key, "NOT SET")
    lines.append("FLAG " + key + " = " + val)

lines.append("")
if issues:
    lines.append("WARNING: " + str(len(issues)) + " issue(s): " + ", ".join(issues))
    lines.append("Trader will NOT start. Fix via Colab or SSH.")
else:
    lines.append("All required env vars OK. Starting live trader...")

msg = "\n".join(lines)

if TOKEN and CHAT_ID:
    try:
        requests.post(
            "https://api.telegram.org/bot" + TOKEN + "/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print("Telegram send failed:", e)
else:
    print("No TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID — skipping notification")

print(msg)
sys.exit(1 if issues else 0)
