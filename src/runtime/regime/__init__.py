"""Regime-detection package (regime-aware routing, phase 1).

Single source of truth for the ADX-14 regime classifier the live strategies
(and the regime-roster matrix) gate on. See
``docs/research/regime-router-design-2026-06-01.md`` for the design.
"""
from src.runtime.regime.detector import (
    CHOP_MAX_ADX,
    TREND_MIN_ADX,
    detect_regime,
    regime_label,
    wilder_adx,
)
from src.runtime.regime.policy import load_policy, would_gate

__all__ = [
    "CHOP_MAX_ADX",
    "TREND_MIN_ADX",
    "detect_regime",
    "load_policy",
    "regime_label",
    "wilder_adx",
    "would_gate",
]
