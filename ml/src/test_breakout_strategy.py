
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
from src.strategies_manager import StrategyManager

raw_df = pd.read_csv(REPO_ROOT / "ml/data/raw/btcusdt_1m.csv")
raw_df["datetime_utc"] = pd.to_datetime(raw_df["datetime_utc"], utc=True)

manager = StrategyManager()
signal = manager.get_signal("breakout_confirmation", raw_df.tail(100))

print("Dry-run signal:")
print(signal)

if signal.get("signal") in ["CONFIRM", "STRONG_CONFIRM"]:
    entry = signal["entry_price"]
    atr = signal["atr_14"]
    tp_price = entry + (1.5 * atr)
    sl_price = entry - (1.0 * atr)

    print("Trade plan:")
    print(f"Entry: {entry}")
    print(f"TP: {tp_price}")
    print(f"SL: {sl_price}")
else:
    print("No trade taken.")
