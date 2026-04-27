"""
ICT/SMC Signal Analyzer for Bybit OHLCV data.

Detects Fair Value Gaps, Order Blocks, and Kill Zones and packages
them into a single signals dict.  Plotly visualization is optional
(gracefully skipped when plotly is not installed).

Colab-ready: instantiate ICTSignalsAnalyzer and call analyze(df).
Integrates with src.core.automated_trading_loop via analyze_market().
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from src.ict_detection.fvg_detector import FVGDetector
from src.ict_detection.order_blocks import OrderBlockDetector
from src.ict_detection.swing_points import SwingPointDetector

logger = logging.getLogger(__name__)

# ICT kill-zone windows expressed as (start_hour_utc, end_hour_utc, inclusive)
_KILL_ZONES = {
    "asia":   (0,  4),   # 00:00–04:00 UTC
    "london": (7,  10),  # 07:00–10:00 UTC  (London open)
    "new_york": (12, 15),  # 12:00–15:00 UTC (NY open / power hour)
}


class ICTSignalsAnalyzer:
    """
    Unified ICT/SMC signal detector.

    Parameters
    ----------
    symbol : str
        Market symbol label (informational only).
    fvg_min_gap : float
        Minimum price gap size to record an FVG.
    ob_lookback : int
        Candles to look back when searching for the origin candle of an OB.
    swing_bars : int
        Bars on each side required to confirm a swing high/low.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        fvg_min_gap: float = 0.0,
        ob_lookback: int = 20,
        swing_bars: int = 5,
    ) -> None:
        self.symbol = symbol
        self._fvg = FVGDetector(min_gap_size=fvg_min_gap)
        self._ob = OrderBlockDetector(lookback=ob_lookback)
        self._swing = SwingPointDetector(left_bars=swing_bars, right_bars=swing_bars)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_fvg(self, df: pd.DataFrame) -> list[dict]:
        """
        Return all bullish and bearish Fair Value Gaps present in *df*.

        Each entry has keys:
          type, start_time, end_time, gap_low, gap_high, gap_size, filled
        """
        _validate_ohlcv(df)
        return self._fvg.detect_all_fvgs(df)

    def detect_order_blocks(self, df: pd.DataFrame) -> list[dict]:
        """
        Return bullish and bearish Order Blocks (support/resistance zones).

        Requires at least ``2 * swing_bars + 1`` rows.  Each entry has keys:
          type, timestamp, high, low, open, close, tested
        """
        _validate_ohlcv(df)
        df_with_swings = df.copy()
        df_with_swings["swing_high"] = self._swing.detect_swing_highs(df)
        df_with_swings["swing_low"] = self._swing.detect_swing_lows(df)
        _, obs = self._ob.mark_obs_on_dataframe(df_with_swings)
        return obs

    def get_kill_zones(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        """
        Return a boolean mask Series for each named kill zone.

        Keys: ``"asia"``, ``"london"``, ``"new_york"``.
        The index matches *df*.  Requires a DatetimeIndex (UTC or tz-naive).
        """
        _validate_ohlcv(df)
        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError("df must have a DatetimeIndex")
        hour = df.index.hour
        return {
            name: pd.Series(
                (hour >= start) & (hour < end),
                index=df.index,
                name=f"kz_{name}",
            )
            for name, (start, end) in _KILL_ZONES.items()
        }

    def analyze(self, df: pd.DataFrame) -> dict:
        """
        Run all detectors and return a consolidated signals dict.

        Returns
        -------
        dict with keys:
          symbol, timeframe_rows, fvgs, order_blocks, kill_zones,
          latest_signal (str or None), latest_price (float or None)
        """
        _validate_ohlcv(df)
        fvgs = self.detect_fvg(df)
        obs = self.detect_order_blocks(df)
        kill_zones = self.get_kill_zones(df)

        # Derive a simple directional bias from the latest unfilled FVGs
        latest_signal, latest_price = _derive_signal(df, fvgs, kill_zones)

        signals = {
            "symbol": self.symbol,
            "timeframe_rows": len(df),
            "fvgs": fvgs,
            "order_blocks": obs,
            "kill_zones": {k: v.to_dict() for k, v in kill_zones.items()},
            "latest_signal": latest_signal,
            "latest_price": latest_price,
        }
        logger.info(
            "ICTSignalsAnalyzer: %s fvgs=%d obs=%d signal=%s price=%s",
            self.symbol,
            len(fvgs),
            len(obs),
            latest_signal,
            latest_price,
        )
        return signals

    def plot(
        self,
        df: pd.DataFrame,
        signals: Optional[dict] = None,
        last_n: int = 200,
    ):
        """
        Build a Plotly candlestick chart with FVG and Order Block overlays.

        Returns a ``plotly.graph_objects.Figure`` or ``None`` if plotly is
        not installed.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data (full frame; only the last *last_n* rows are plotted).
        signals : dict, optional
            Output of ``analyze()``.  Computed fresh if not supplied.
        last_n : int
            Candles to display.
        """
        try:
            import plotly.graph_objects as go
        except ImportError:
            logger.warning("plotly not installed – skipping chart")
            return None

        if signals is None:
            signals = self.analyze(df)

        plot_df = df.tail(last_n).copy()
        fig = go.Figure()

        # Candlestick
        fig.add_trace(
            go.Candlestick(
                x=plot_df.index,
                open=plot_df["open"],
                high=plot_df["high"],
                low=plot_df["low"],
                close=plot_df["close"],
                name="Price",
            )
        )

        # FVG rectangles
        for fvg in signals["fvgs"]:
            if fvg["start_time"] < plot_df.index[0]:
                continue
            color = "rgba(0,200,100,0.15)" if fvg["type"] == "bullish" else "rgba(200,50,50,0.15)"
            border = "rgba(0,200,100,0.6)" if fvg["type"] == "bullish" else "rgba(200,50,50,0.6)"
            fig.add_shape(
                type="rect",
                x0=fvg["start_time"],
                x1=fvg["end_time"],
                y0=fvg["gap_low"],
                y1=fvg["gap_high"],
                fillcolor=color,
                line=dict(color=border, width=1),
                layer="below",
            )

        # Order Block zones
        for ob in signals["order_blocks"]:
            if ob["timestamp"] < plot_df.index[0]:
                continue
            color = "rgba(30,144,255,0.15)" if ob["type"] == "bullish" else "rgba(255,140,0,0.15)"
            border = "rgba(30,144,255,0.6)" if ob["type"] == "bullish" else "rgba(255,140,0,0.6)"
            x1 = plot_df.index[-1]
            fig.add_shape(
                type="rect",
                x0=ob["timestamp"],
                x1=x1,
                y0=ob["low"],
                y1=ob["high"],
                fillcolor=color,
                line=dict(color=border, width=1),
                layer="below",
            )

        # Kill-zone vertical bands
        kz_colors = {"asia": "rgba(200,200,0,0.07)", "london": "rgba(0,150,255,0.07)", "new_york": "rgba(255,100,0,0.07)"}
        for name, mask_dict in signals["kill_zones"].items():
            mask = pd.Series(mask_dict)
            mask = mask[mask.index.isin(plot_df.index)]
            in_zone = False
            zone_start = None
            for ts, active in mask.items():
                if active and not in_zone:
                    in_zone = True
                    zone_start = ts
                elif not active and in_zone:
                    in_zone = False
                    fig.add_vrect(
                        x0=zone_start,
                        x1=ts,
                        fillcolor=kz_colors[name],
                        layer="below",
                        line_width=0,
                        annotation_text=name,
                        annotation_position="top left",
                    )
            if in_zone and zone_start is not None:
                fig.add_vrect(
                    x0=zone_start,
                    x1=plot_df.index[-1],
                    fillcolor=kz_colors[name],
                    layer="below",
                    line_width=0,
                    annotation_text=name,
                    annotation_position="top left",
                )

        fig.update_layout(
            title=f"{self.symbol} – ICT/SMC Signals",
            xaxis_title="Time (UTC)",
            yaxis_title="Price",
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
            height=700,
        )
        return fig


# ------------------------------------------------------------------
# Integration helper – drop-in replacement for turtle_soup_signal
# in automated_trading_loop.KillZoneScalperBot
# ------------------------------------------------------------------

def ict_signal_from_df(
    df: pd.DataFrame,
    symbol: str = "BTC/USDT",
) -> tuple[Optional[str], Optional[float], Optional[dict]]:
    """
    Thin adapter so ICTSignalsAnalyzer can feed into KillZoneScalperBot.

    Returns (direction, entry_price, meta) or (None, None, None).
    """
    analyzer = ICTSignalsAnalyzer(symbol=symbol)
    signals = analyzer.analyze(df)
    direction = signals["latest_signal"]
    price = signals["latest_price"]
    if direction is None:
        return None, None, None
    return direction, price, {"fvgs": signals["fvgs"], "order_blocks": signals["order_blocks"]}


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _validate_ohlcv(df: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing columns: {missing}")
    if len(df) < 3:
        raise ValueError("DataFrame must have at least 3 rows")


def _derive_signal(
    df: pd.DataFrame,
    fvgs: list[dict],
    kill_zones: dict[str, pd.Series],
) -> tuple[Optional[str], Optional[float]]:
    """
    Simple rule: if the last candle is inside a kill zone AND there is an
    unfilled FVG just above (bearish) or below (bullish) current price,
    emit a directional signal.
    """
    if df.empty:
        return None, None

    last = df.iloc[-1]
    last_ts = df.index[-1]
    last_close = float(last["close"])

    in_any_kz = any(bool(mask.get(last_ts, False)) for mask in kill_zones.values())
    if not in_any_kz:
        return None, None

    unfilled = [f for f in fvgs if not f.get("filled", False)]
    if not unfilled:
        return None, None

    # Most recent unfilled FVG
    recent = max(unfilled, key=lambda f: f["end_time"])

    if recent["type"] == "bullish" and last_close > recent["gap_low"]:
        return "long", last_close,
    if recent["type"] == "bearish" and last_close < recent["gap_high"]:
        return "short", last_close

    return None, None
