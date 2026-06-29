"""RiskManager confidence-aware sizing (per-strategy risk removal, 2026-06-29).

Verifies: (1) the account risk_pct is the per-trade basis; (2) the legacy
meta["strategy_risk_pct"] multiplier is IGNORED (sizing no longer reads it);
(3) _confidence_scalar is pure, bounded [floor,1.0], NaN/inf-safe, off→1.0,
and monotone non-decreasing for linear/threshold.
"""
from __future__ import annotations

import math

from src.units.accounts.risk import RiskManager


def _rm(**risk):
    base = {"risk_pct": 0.015, "daily_loss_pct": 0.05}
    base.update(risk)
    return RiskManager(base, account_id="t")


def test_off_mode_returns_flat_basis():
    rm = _rm()  # confidence_sizing defaults to "off"
    assert rm.confidence_sizing_mode == "off"
    for c in (0.0, 0.2, 0.5, 0.9, 1.0):
        assert rm._confidence_scalar(c) == 1.0


def test_linear_curve_bounds_and_monotonicity():
    rm = _rm(confidence_sizing="linear", confidence_floor=0.5)
    assert rm._confidence_scalar(0.0) == 0.5          # floor
    assert rm._confidence_scalar(1.0) == 1.0          # cap = basis
    assert math.isclose(rm._confidence_scalar(0.5), 0.75)
    prev = -1.0
    for c in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = rm._confidence_scalar(c)
        assert 0.5 <= v <= 1.0
        assert v >= prev  # monotone non-decreasing
        prev = v


def test_threshold_curve():
    rm = _rm(confidence_sizing="threshold", confidence_floor=0.4, confidence_knee=0.7)
    assert rm._confidence_scalar(0.7) == 1.0          # at knee → full basis
    assert rm._confidence_scalar(0.9) == 1.0          # above knee → flat
    assert rm._confidence_scalar(0.0) == 0.4          # floor
    assert 0.4 < rm._confidence_scalar(0.35) < 1.0    # mid-ramp


def test_scalar_never_exceeds_one_and_is_nan_safe():
    rm = _rm(confidence_sizing="linear", confidence_floor=0.5)
    assert rm._confidence_scalar(2.0) == 1.0          # clamp high
    assert rm._confidence_scalar(-1.0) == 0.5         # clamp low → floor
    assert rm._confidence_scalar(float("nan")) == 1.0  # NaN → fail-safe basis
    assert rm._confidence_scalar(float("inf")) == 1.0
    assert rm._confidence_scalar("x") == 1.0           # bad type → fail-safe


def test_unknown_mode_is_flat_basis():
    rm = _rm(confidence_sizing="bogus")
    assert rm._confidence_scalar(0.3) == 1.0


def test_position_size_ignores_legacy_strategy_risk_pct_meta():
    """A leftover meta multiplier must NOT change the size — sizing is the
    account basis only (confidence off)."""
    from src.core.coordinator import OrderPackage  # OrderPackage shape

    def _pkg(meta):
        return OrderPackage(
            strategy="trend_donchian", symbol="BTCUSDT", direction="long",
            entry=100.0, sl=99.0, tp=110.0, confidence=0.8, meta=meta,
        )

    rm = _rm()  # off → flat 1.5% basis
    qty_no_meta = rm.position_size(_pkg({}), balance_usd=10_000.0)
    qty_with_legacy = rm.position_size(_pkg({"strategy_risk_pct": 0.3}), balance_usd=10_000.0)
    assert qty_no_meta == qty_with_legacy  # the legacy multiplier is ignored
