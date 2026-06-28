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
from src.runtime.regime.ml_vol_verdict import ml_vol_regime, ml_vol_regime_for_symbol
from src.runtime.regime.policy import load_policy, would_gate
from src.runtime.regime.vol_detector import (
    VOL_CALM,
    VOL_UNKNOWN,
    VOL_VOLATILE,
    detect_vol_regime,
    resolve_vol_specs,
    vol_regime_from_spec,
)

__all__ = [
    "CHOP_MAX_ADX",
    "TREND_MIN_ADX",
    "VOL_CALM",
    "VOL_UNKNOWN",
    "VOL_VOLATILE",
    "detect_regime",
    "detect_vol_regime",
    "load_policy",
    "ml_vol_regime",
    "ml_vol_regime_for_symbol",
    "regime_label",
    "resolve_vol_specs",
    "vol_regime_from_spec",
    "wilder_adx",
    "would_gate",
]
