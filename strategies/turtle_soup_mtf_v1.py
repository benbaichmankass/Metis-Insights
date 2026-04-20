from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

try:
    from strategies.base_strategy import BaseStrategy
except Exception:
    class BaseStrategy:
        def __init__(self, config=None):
            self.config = config or {}

@dataclass
class TradePlan:
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    initial_stop: float
    tp1_price: float
    tp2_price: float
    size: float
    remaining_size: float
    risk_per_unit: float
    realized_pnl: float = 0.0
    took_partial: bool = False
    moved_to_be: bool = False

class TurtleSoupMTFv1(BaseStrategy):
    NAME = "TurtleSoupMTFv1"

    def __init__(self, config=None):
        super().__init__(config or {})
        self.config = config or {}

        # Best sweep configuration
        self.sweep_lookback_15m = 60
        self.min_sweep_buffer_bps = 12
        self.min_body_to_range = 0.60
        self.atr_stop_mult = 0.35
        self.be_at_r = 1.0
        self.tp1_at_r = 1.25
        self.tp2_at_r = 3.0
        self.partial_close_pct = 0.50
        self.trail_atr_mult = 1.2
        self.max_entry_wait_bars_1m = 20
        self.setup_tf = "15m"
        self.entry_tf = "1m"
        self.atr_period = self.config.get("atr_period", 14)
        self.risk_per_trade = self.config.get("risk_per_trade", 0.005)
        self.fee_rate = self.config.get("fee_rate", 0.0006)
        self.slippage_rate = self.config.get("slippage_rate", 0.0002)

    def add_atr(self, df: pd.DataFrame, period: Optional[int] = None) -> pd.DataFrame:
        period = period or self.atr_period
        out = df.copy()
        prev_close = out["close"].shift(1)
        tr = pd.concat([
            (out["high"] - out["low"]).abs(),
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs()
        ], axis=1).max(axis=1)
        out["atr"] = tr.rolling(period, min_periods=period).mean()
        return out

    def resample_ohlcv(self, df: pd.DataFrame, rule: str = "15min") -> pd.DataFrame:
        x = df.copy()
        if "datetime" not in x.columns:
            raise ValueError("Expected a 'datetime' column in input data")
        x["datetime"] = pd.to_datetime(x["datetime"], utc=True, errors="coerce")
        x = x.dropna(subset=["datetime"]).sort_values("datetime").set_index("datetime")

        turnover_col = "turnover" if "turnover" in x.columns else None

        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
        if turnover_col:
            agg["turnover"] = "sum"

        out = x.resample(rule).agg(agg).dropna().reset_index()
        if "turnover" not in out.columns:
            out["turnover"] = np.nan
        return out

    def detect_setup(self, df_setup: pd.DataFrame) -> pd.DataFrame:
        df = df_setup.copy()
        df = self.add_atr(df, self.atr_period)

        df["prev_high_ref"] = df["high"].rolling(self.sweep_lookback_15m).max().shift(1)
        df["prev_low_ref"] = df["low"].rolling(self.sweep_lookback_15m).min().shift(1)
        df["range"] = df["high"] - df["low"]
        df["body"] = (df["close"] - df["open"]).abs()
        df["body_to_range"] = np.where(df["range"] > 0, df["body"] / df["range"], 0)

        sweep_buffer = np.maximum(
            df["close"] * (self.min_sweep_buffer_bps / 10000.0),
            df["atr"].fillna(0) * 0.05
        )

        df["bullish_setup"] = (
            (df["low"] < (df["prev_low_ref"] - sweep_buffer)) &
            (df["close"] > df["prev_low_ref"]) &
            (df["body_to_range"] >= self.min_body_to_range)
        )

        df["bearish_setup"] = (
            (df["high"] > (df["prev_high_ref"] + sweep_buffer)) &
            (df["close"] < df["prev_high_ref"]) &
            (df["body_to_range"] >= self.min_body_to_range)
        )
        return df

    def setup_signal_from_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        if bool(row.get("bullish_setup", False)):
            return {
                "side": "long",
                "level": float(row["prev_low_ref"]),
                "sweep_extreme": float(row["low"]),
                "setup_time": pd.Timestamp(row["datetime"]),
                "atr": float(row["atr"]),
            }
        if bool(row.get("bearish_setup", False)):
            return {
                "side": "short",
                "level": float(row["prev_high_ref"]),
                "sweep_extreme": float(row["high"]),
                "setup_time": pd.Timestamp(row["datetime"]),
                "atr": float(row["atr"]),
            }
        return None

    def find_entry(self, setup_time: pd.Timestamp, signal: Dict[str, Any], df_entry: pd.DataFrame) -> Optional[Dict[str, Any]]:
        x = df_entry.copy()
        x["datetime"] = pd.to_datetime(x["datetime"], utc=True, errors="coerce")
        x = x.dropna(subset=["datetime"]).sort_values("datetime")

        entry_window_start = setup_time + pd.Timedelta(minutes=1)
        entry_window_end = setup_time + pd.Timedelta(minutes=self.max_entry_wait_bars_1m)

        entry_slice = x[
            (x["datetime"] >= entry_window_start) &
            (x["datetime"] <= entry_window_end)
        ].copy().reset_index(drop=True)

        if entry_slice.empty:
            return None

        entry_slice["prev_high"] = entry_slice["high"].shift(1)
        entry_slice["prev_low"] = entry_slice["low"].shift(1)
        entry_slice["bull_break"] = entry_slice["close"] > entry_slice["prev_high"]
        entry_slice["bear_break"] = entry_slice["close"] < entry_slice["prev_low"]
        entry_slice["range"] = entry_slice["high"] - entry_slice["low"]
        entry_slice["body"] = (entry_slice["close"] - entry_slice["open"]).abs()
        entry_slice["body_to_range"] = np.where(entry_slice["range"] > 0, entry_slice["body"] / entry_slice["range"], 0)

        for i in range(2, len(entry_slice)):
            row = entry_slice.iloc[i]
            if row["body_to_range"] < self.min_body_to_range:
                continue

            if signal["side"] == "long":
                reclaimed = row["close"] > signal["level"]
                micro_shift = bool(entry_slice.iloc[max(1, i-1):i+1]["bull_break"].fillna(False).any())
                if reclaimed and micro_shift:
                    return {
                        "entry_time": pd.Timestamp(row["datetime"]),
                        "entry_price": float(row["close"]),
                        "signal_level": float(signal["level"]),
                    }
            else:
                reclaimed = row["close"] < signal["level"]
                micro_shift = bool(entry_slice.iloc[max(1, i-1):i+1]["bear_break"].fillna(False).any())
                if reclaimed and micro_shift:
                    return {
                        "entry_time": pd.Timestamp(row["datetime"]),
                        "entry_price": float(row["close"]),
                        "signal_level": float(signal["level"]),
                    }
        return None

    def build_trade_plan(self, signal: Dict[str, Any], entry: Dict[str, Any], balance: float) -> Optional[TradePlan]:
        entry_price = float(entry["entry_price"])

        if signal["side"] == "long":
            stop = min(signal["sweep_extreme"], signal["level"]) - signal["atr"] * self.atr_stop_mult
            risk_per_unit = entry_price - stop
        else:
            stop = max(signal["sweep_extreme"], signal["level"]) + signal["atr"] * self.atr_stop_mult
            risk_per_unit = stop - entry_price

        if risk_per_unit <= 0:
            return None

        risk_cash = balance * self.risk_per_trade
        size = risk_cash / risk_per_unit

        if signal["side"] == "long":
            tp1 = entry_price + self.tp1_at_r * risk_per_unit
            tp2 = entry_price + self.tp2_at_r * risk_per_unit
        else:
            tp1 = entry_price - self.tp1_at_r * risk_per_unit
            tp2 = entry_price - self.tp2_at_r * risk_per_unit

        return TradePlan(
            side=signal["side"],
            entry_time=entry["entry_time"],
            entry_price=entry_price,
            stop_price=stop,
            initial_stop=stop,
            tp1_price=tp1,
            tp2_price=tp2,
            size=size,
            remaining_size=size,
            risk_per_unit=risk_per_unit,
        )

    def manage_position(self, plan: TradePlan, df_current: pd.DataFrame) -> Optional[Dict[str, Any]]:
        x = df_current.copy()
        x["datetime"] = pd.to_datetime(x["datetime"], utc=True, errors="coerce")
        x = x.dropna(subset=["datetime"]).sort_values("datetime")
        if "atr" not in x.columns:
            x = self.add_atr(x, self.atr_period)

        for _, row in x.iterrows():
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0

            if plan.side == "long":
                unreal_r = (close - plan.entry_price) / plan.risk_per_unit

                if (not plan.moved_to_be) and unreal_r >= self.be_at_r:
                    plan.stop_price = max(plan.stop_price, plan.entry_price)
                    plan.moved_to_be = True

                if (not plan.took_partial) and high >= plan.tp1_price:
                    partial_size = plan.remaining_size * self.partial_close_pct
                    pnl_part = partial_size * (plan.tp1_price - plan.entry_price)
                    fees = partial_size * (plan.entry_price + plan.tp1_price) * self.fee_rate
                    slip = partial_size * plan.tp1_price * self.slippage_rate
                    plan.realized_pnl += pnl_part - fees - slip
                    plan.remaining_size -= partial_size
                    plan.took_partial = True

                if plan.took_partial and atr > 0:
                    plan.stop_price = max(plan.stop_price, close - atr * self.trail_atr_mult)

                if high >= plan.tp2_price:
                    pnl = plan.remaining_size * (plan.tp2_price - plan.entry_price)
                    fees = plan.remaining_size * (plan.entry_price + plan.tp2_price) * self.fee_rate
                    slip = plan.remaining_size * plan.tp2_price * self.slippage_rate
                    total_pnl = plan.realized_pnl + pnl - fees - slip
                    return {
                        "exit_time": pd.Timestamp(row["datetime"]),
                        "exit_price": plan.tp2_price,
                        "exit_reason": "tp2",
                        "pnl": total_pnl,
                        "pnl_r": total_pnl / (plan.size * plan.risk_per_unit),
                    }

                if low <= plan.stop_price:
                    pnl = plan.remaining_size * (plan.stop_price - plan.entry_price)
                    fees = plan.remaining_size * (plan.entry_price + plan.stop_price) * self.fee_rate
                    slip = plan.remaining_size * plan.stop_price * self.slippage_rate
                    total_pnl = plan.realized_pnl + pnl - fees - slip
                    return {
                        "exit_time": pd.Timestamp(row["datetime"]),
                        "exit_price": plan.stop_price,
                        "exit_reason": "stop",
                        "pnl": total_pnl,
                        "pnl_r": total_pnl / (plan.size * plan.risk_per_unit),
                    }

            else:
                unreal_r = (plan.entry_price - close) / plan.risk_per_unit

                if (not plan.moved_to_be) and unreal_r >= self.be_at_r:
                    plan.stop_price = min(plan.stop_price, plan.entry_price)
                    plan.moved_to_be = True

                if (not plan.took_partial) and low <= plan.tp1_price:
                    partial_size = plan.remaining_size * self.partial_close_pct
                    pnl_part = partial_size * (plan.entry_price - plan.tp1_price)
                    fees = partial_size * (plan.entry_price + plan.tp1_price) * self.fee_rate
                    slip = partial_size * plan.tp1_price * self.slippage_rate
                    plan.realized_pnl += pnl_part - fees - slip
                    plan.remaining_size -= partial_size
                    plan.took_partial = True

                if plan.took_partial and atr > 0:
                    plan.stop_price = min(plan.stop_price, close + atr * self.trail_atr_mult)

                if low <= plan.tp2_price:
                    pnl = plan.remaining_size * (plan.entry_price - plan.tp2_price)
                    fees = plan.remaining_size * (plan.entry_price + plan.tp2_price) * self.fee_rate
                    slip = plan.remaining_size * plan.tp2_price * self.slippage_rate
                    total_pnl = plan.realized_pnl + pnl - fees - slip
                    return {
                        "exit_time": pd.Timestamp(row["datetime"]),
                        "exit_price": plan.tp2_price,
                        "exit_reason": "tp2",
                        "pnl": total_pnl,
                        "pnl_r": total_pnl / (plan.size * plan.risk_per_unit),
                    }

                if high >= plan.stop_price:
                    pnl = plan.remaining_size * (plan.entry_price - plan.stop_price)
                    fees = plan.remaining_size * (plan.entry_price + plan.stop_price) * self.fee_rate
                    slip = plan.remaining_size * plan.stop_price * self.slippage_rate
                    total_pnl = plan.realized_pnl + pnl - fees - slip
                    return {
                        "exit_time": pd.Timestamp(row["datetime"]),
                        "exit_price": plan.stop_price,
                        "exit_reason": "stop",
                        "pnl": total_pnl,
                        "pnl_r": total_pnl / (plan.size * plan.risk_per_unit),
                    }

        if x.empty:
            return None

        last = x.iloc[-1]
        final_close = float(last["close"])

        if plan.side == "long":
            pnl = plan.remaining_size * (final_close - plan.entry_price)
        else:
            pnl = plan.remaining_size * (plan.entry_price - final_close)

        fees = plan.remaining_size * (plan.entry_price + final_close) * self.fee_rate
        total_pnl = plan.realized_pnl + pnl - fees

        return {
            "exit_time": pd.Timestamp(last["datetime"]),
            "exit_price": final_close,
            "exit_reason": "month_end",
            "pnl": total_pnl,
            "pnl_r": total_pnl / (plan.size * plan.risk_per_unit),
        }
