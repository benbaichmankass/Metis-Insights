"""Offline tests for scripts/research/cointegration_stability.py — numpy-only,
synthetic series (no network, no data files)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "cointegration_stability",
    str(REPO_ROOT / "scripts" / "research" / "cointegration_stability.py"))
cs = importlib.util.module_from_spec(_SPEC)
sys.modules["cointegration_stability"] = cs
_SPEC.loader.exec_module(cs)  # type: ignore[union-attr]


def test_half_life_of_ou_is_finite_and_positive():
    # OU / AR(1) mean-reverting spread with known phi -> a finite positive half-life.
    rng = np.random.default_rng(3)
    n = 5000
    phi = 0.9  # AR(1) coeff => lambda = phi-1 = -0.1 => HL = -ln2/ln(0.9) ~ 6.58 bars
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = phi * s[i - 1] + rng.normal(0, 1)
    hl = cs._half_life_bars(s)
    assert hl is not None
    assert 4.0 < hl < 10.0  # ~6.58 expected


def test_random_walk_has_no_half_life():
    # A pure random walk (phi=1) is NOT mean-reverting -> lambda ~ 0 -> None.
    rng = np.random.default_rng(5)
    s = np.cumsum(rng.normal(0, 1, 5000))
    hl = cs._half_life_bars(s)
    assert hl is None  # not mean-reverting


def test_short_series_returns_none():
    assert cs._half_life_bars(np.arange(5.0)) is None


def test_rolling_beta_shape_and_leakage_safe():
    la = np.linspace(1, 2, 100)
    lb = np.linspace(1, 2, 100) * 2.0
    beta = cs._rolling_beta(la, lb, 10)
    assert beta.shape == (100,)
    # first value is the fillna(1.0) default (shifted + warmup) -> not NaN
    assert np.isfinite(beta[-1])
