
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
from src.strategies_manager import StrategyManager

_HF_DATASET_REPO = "bentzbk/ict-trading-bot-btcusdt-1m"
_HF_CSV_FILE = "btcusdt_1m.csv"
_LOCAL_CSV = REPO_ROOT / "ml" / "data" / "raw" / "btcusdt_1m.csv"


def _load_raw_df() -> pd.DataFrame:
    """Download 1m OHLCV CSV from HF Hub (cached); fall back to local copy."""
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
        path = hf_hub_download(repo_id=_HF_DATASET_REPO, filename=_HF_CSV_FILE, repo_type="dataset")
        return pd.read_csv(path)
    except Exception:
        return pd.read_csv(_LOCAL_CSV)


raw_df = _load_raw_df()
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
