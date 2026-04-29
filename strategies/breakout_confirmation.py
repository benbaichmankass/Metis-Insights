
import pandas as pd
import numpy as np
import joblib
import json
import os
from pathlib import Path

_HF_MODEL_REPO = "bentzbk/ict-trading-bot-rf-breakout-v1"
_HF_MODEL_FILE = "btc_breakout_confirmation_v1.joblib"
# Legacy fallback path — used only when both HF Hub and the registry are unavailable.
_LEGACY_LOCAL_MODEL = Path(__file__).resolve().parent.parent / "ml" / "models" / "local" / _HF_MODEL_FILE


def _local_model_path() -> Path:
    """Return the local model artifact path from the strategy registry.

    Falls back to the legacy hard-coded path when the registry is unavailable
    (e.g. pyyaml not installed in a minimal deploy environment).
    """
    try:
        from src.strategy_registry import model_path as _registry_model_path  # type: ignore
        p = _registry_model_path("breakout_confirmation")
        if p:
            return Path(p)
    except Exception:
        pass
    return _LEGACY_LOCAL_MODEL


def _load_model():
    """Download model from HF Hub (cached); fall back to local copy if unavailable.

    Raises FileNotFoundError with a clear message when neither source is
    available, rather than letting joblib emit a confusing OSError.
    """
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
        path = hf_hub_download(repo_id=_HF_MODEL_REPO, filename=_HF_MODEL_FILE, repo_type="model")
        return joblib.load(path)
    except Exception:
        local = _local_model_path()
        if not local.exists():
            raise FileNotFoundError(
                f"Breakout model not found at {local}. "
                "Either run 'huggingface_hub.snapshot_download' to populate the HF cache, "
                "or place the artifact at the path configured in config/strategies.yaml "
                "(model: btc_v1.joblib under <repo_root>/models/)."
            )
        return joblib.load(str(local))


class BreakoutConfirmationStrategy:
    def __init__(self):
        self.model = _load_model()
        self.feature_names = json.loads(Path("ml/config/features_v1.json").read_text())
        self.thresholds = json.loads(Path("ml/config/thresholds_v1.json").read_text())
        self.lookback_bars = 20

    def score_breakout(self, candles_df):
        df = candles_df.copy().reset_index(drop=True)

        if "datetime_utc" in df.columns:
            df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True, errors="coerce")

        df["returns_1"] = df["close"].pct_change(1)
        df["returns_3"] = df["close"].pct_change(3)
        df["returns_5"] = df["close"].pct_change(5)

        df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
        df["ema_10"] = df["close"].ewm(span=10, adjust=False).mean()
        df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()

        df["ema_5_minus_10"] = df["ema_5"] - df["ema_10"]
        df["ema_10_minus_20"] = df["ema_10"] - df["ema_20"]
        df["ema_5_slope"] = df["ema_5"].diff()

        prev_close = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        df["true_range"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr_14"] = df["true_range"].rolling(14).mean()

        df["range_pct"] = (df["high"] - df["low"]) / df["close"]
        df["volume_ratio_20"] = df["volume"] / df["volume"].rolling(20).mean()

        df["high_lookback_20"] = df["high"].rolling(self.lookback_bars).max().shift(1)
        df["low_lookback_20"] = df["low"].rolling(self.lookback_bars).min().shift(1)

        df["label_breakout"] = (
            (df["high"] > df["high_lookback_20"]) &
            (df["close"] > df["high_lookback_20"])
        ).astype(int)

        df["breakout_distance"] = np.where(
            df["label_breakout"] == 1,
            df["close"] - df["high_lookback_20"],
            0.0
        )

        candle_range = (df["high"] - df["low"]).replace(0, np.nan)
        df["breakout_close_strength"] = (
            ((df["close"] - df["low"]) / candle_range).clip(0, 1).fillna(0)
        )

        df["distance_to_high_20"] = df["high_lookback_20"] - df["close"]

        if "datetime_utc" in df.columns:
            df["hour_utc"] = df["datetime_utc"].dt.hour
        else:
            df["hour_utc"] = 0

        df["session_london_ny_overlap"] = df["hour_utc"].between(13, 16).astype(int)

        feature_cols = [
            f for f in self.feature_names
            if f not in ["close", "high_lookback_20", "low_lookback_20"]
        ]

        recent_breakout = df[df["label_breakout"] == 1].tail(1)
        if recent_breakout.empty:
            return {"signal": "NO_BREAKOUT"}

        X = recent_breakout[feature_cols].dropna()
        if X.empty:
            return {"signal": "NO_DATA"}

        prob_tp = self.model.predict_proba(X)[0, 1]

        if prob_tp >= self.thresholds["strong_confirm_threshold"]:
            signal = "STRONG_CONFIRM"
        elif prob_tp >= self.thresholds["confirm_threshold"]:
            signal = "CONFIRM"
        else:
            signal = "REJECT"

        return {
            "signal": signal,
            "prob_tp": float(prob_tp),
            "entry_price": float(recent_breakout["close"].iloc[0]),
            "atr_14": float(recent_breakout["atr_14"].iloc[0]),
            "datetime": str(recent_breakout["datetime_utc"].iloc[0]) if "datetime_utc" in recent_breakout.columns else "N/A"
        }
