
import pandas as pd
import numpy as np
import joblib
import json
from pathlib import Path


class BreakoutConfirmationStrategy:
    def __init__(self):
        self.model = joblib.load("ml/models/local/btc_breakout_confirmation_v1.joblib")
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
