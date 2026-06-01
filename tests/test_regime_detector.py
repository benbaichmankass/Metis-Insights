"""Tests for the regime detector (PERF-20260601-002 phase 1).

Verifies the detector's:

  * threshold mapping (regime_label) matches the regime-roster matrix
    (chop <20, transitional 20-25, trending >=25),
  * Wilder ADX matches the live fade_breakout_4h._adx implementation
    bit-for-bit (the design intent — one ADX source of truth),
  * detect_regime is robust to missing columns / empty frames / NaN
    (must NEVER raise — phase 1 is logging-only, must not break ticks),
  * the audit-row stamp shape (regime / adx / source fields) is stable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.runtime.regime import (
    CHOP_MAX_ADX,
    TREND_MIN_ADX,
    detect_regime,
    regime_label,
    wilder_adx,
)
from src.units.strategies.fade_breakout_4h import _adx as live_fade_adx


def _ramp(n: int, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    """OHLC frame for a steady uptrend — ADX should classify as trending."""
    closes = np.array([start + i * step for i in range(n)], dtype=float)
    return pd.DataFrame({
        "high": closes + 0.5,
        "low": closes - 0.5,
        "close": closes,
        "open": np.concatenate(([closes[0]], closes[:-1])),
    })


def _flat(n: int, price: float = 100.0, noise: float = 0.1) -> pd.DataFrame:
    """Choppy frame with no directional drift — ADX should classify as chop."""
    rng = np.random.default_rng(seed=42)
    noise_arr = rng.normal(0, noise, n)
    closes = np.full(n, price) + noise_arr
    return pd.DataFrame({
        "high": closes + abs(noise) * 1.5,
        "low": closes - abs(noise) * 1.5,
        "close": closes,
        "open": np.concatenate(([closes[0]], closes[:-1])),
    })


# --- threshold mapping ------------------------------------------------------

def test_regime_label_thresholds():
    assert regime_label(0.0) == "chop"
    assert regime_label(19.99) == "chop"
    assert regime_label(CHOP_MAX_ADX) == "transitional"  # 20.0 is transitional
    assert regime_label(22.0) == "transitional"
    assert regime_label(24.99) == "transitional"
    assert regime_label(TREND_MIN_ADX) == "trending"     # 25.0 is trending
    assert regime_label(40.0) == "trending"


def test_regime_label_unknown_paths():
    assert regime_label(None) == "unknown"
    assert regime_label(float("nan")) == "unknown"
    assert regime_label("not a number") == "unknown"


# --- ADX parity with the live fade unit ------------------------------------

def test_wilder_adx_matches_live_fade_impl():
    """The detector's ADX must equal fade_breakout_4h._adx bit-for-bit so
    the live regime stream and the matrix tag the same bars identically."""
    df = _ramp(120)
    a = wilder_adx(df, period=14).dropna()
    b = live_fade_adx(df, period=14).dropna()
    # Align on common index (Wilder warmup may differ by one NaN; compare
    # the bars where both are defined).
    common = a.index.intersection(b.index)
    assert len(common) > 50, "expected meaningful overlap after warmup"
    np.testing.assert_allclose(a.loc[common].values, b.loc[common].values, rtol=1e-10)


# --- detect_regime: happy path classifications ------------------------------

def test_detect_regime_uptrend_classifies_as_trending():
    df = _ramp(120, step=1.0)
    out = detect_regime(df)
    assert out["regime"] == "trending"
    assert out["adx"] is not None and out["adx"] >= TREND_MIN_ADX
    assert out["source"] == "adx-14"


def test_detect_regime_flat_classifies_as_chop():
    df = _flat(120, noise=0.05)
    out = detect_regime(df)
    assert out["regime"] == "chop"
    assert out["adx"] is not None and out["adx"] < CHOP_MAX_ADX
    assert out["source"] == "adx-14"


# --- detect_regime: robustness — must NEVER raise --------------------------

def test_detect_regime_handles_none_input():
    out = detect_regime(None)
    assert out == {"regime": "unknown", "adx": None, "source": "adx-14"}


def test_detect_regime_handles_empty_frame():
    out = detect_regime(pd.DataFrame())
    assert out["regime"] == "unknown"
    assert out["adx"] is None
    assert out["source"] == "adx-14"


def test_detect_regime_handles_missing_columns():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})  # missing high / low
    out = detect_regime(df)
    assert out["regime"] == "unknown"
    assert out["adx"] is None


def test_detect_regime_handles_flat_zero_movement_bars():
    """Reproduces the 2026-05-25 fade-breakout crash condition (flat 4h MES
    bars produced plus_dm/minus_dm both 0 → divide-by-zero → object dtype
    upcast → 'No numeric types to aggregate' on the trailing ewm). Detector
    must return cleanly rather than raise."""
    df = pd.DataFrame({
        "high": [100.0] * 50,
        "low": [100.0] * 50,
        "close": [100.0] * 50,
        "open": [100.0] * 50,
    })
    out = detect_regime(df)  # must not raise
    # ADX is undefined on a perfectly-flat series; expect unknown (NaN-guarded).
    assert out["regime"] in ("unknown", "chop")
    assert out["source"] == "adx-14"


def test_detect_regime_handles_non_dataframe():
    """Belt-and-suspenders: builder bugs can pass non-frames; phase 1 must
    log unknown rather than crash."""
    out = detect_regime("not a frame")  # type: ignore[arg-type]
    assert out["regime"] == "unknown"
    assert out["adx"] is None


# --- detect_regime: output shape stable ------------------------------------

def test_detect_regime_output_shape_is_stable():
    """The audit consumer (regime stream / dashboard / matrix-base-rate
    verifier) reads these three keys. Adding a new key is fine; renaming
    or dropping one is a contract break."""
    out = detect_regime(_ramp(60))
    assert set(out.keys()) >= {"regime", "adx", "source"}
    assert out["regime"] in ("chop", "transitional", "trending", "unknown")
    assert out["source"] == "adx-14"
    if out["adx"] is not None:
        assert isinstance(out["adx"], float)
