"""ICT detection module — clean public API for signal and filter components.

Formalizes the ``src/ict_detection/`` package as a reusable module available
to all strategies as a filter layer (S8 of the M11 multi-strategy refactor).
Callers should import from here rather than reaching into sub-modules directly.

Available detectors
-------------------
FVGDetector       — Fair Value Gap detection (bullish / bearish imbalances)
LiquidityDetector — Liquidity pool and sweep identification (equal H/L, sweeps)
OrderBlockDetector — Institutional order block zones (OB, BOS / CHoCH)
SwingPointDetector — Structural swing high / low identification
KeyLevelsDetector  — Key price levels (previous H/L, round numbers, POI)

Trend utilities
---------------
ema(series, length)              — Exponential moving average
htf_trend_bias(df, ...)          — Higher-timeframe trend bias classifier

Sub-module imports remain valid for callers that already use them.
"""
from __future__ import annotations

from src.ict_detection.fvg_detector import FVGDetector
from src.ict_detection.liquidity import LiquidityDetector
from src.ict_detection.order_blocks import OrderBlockDetector
from src.ict_detection.swing_points import SwingPointDetector
from src.ict_detection.key_levels import KeyLevelsDetector
from src.ict_detection.trend import ema, htf_trend_bias

__all__ = [
    "FVGDetector",
    "LiquidityDetector",
    "OrderBlockDetector",
    "SwingPointDetector",
    "KeyLevelsDetector",
    "ema",
    "htf_trend_bias",
]
