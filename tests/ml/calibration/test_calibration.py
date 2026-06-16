"""Tests for ml.calibration (design doc § 4a).

Covers: pure-Python predict round-trips through ``to_dict``/``from_dict``;
fit auto-selection by sample size; monotonicity of isotonic/platt; that fitting
recovers a known signal; and the reliability/Brier/ECE metrics.
"""

from __future__ import annotations

import random

import pytest

from ml.calibration import (
    Calibrator,
    ConstantCalibrator,
    DecileCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    brier_score,
    expected_calibration_error,
    fit_calibrator,
    reliability_curve,
)


def _synthetic(n: int, seed: int = 0):
    """raw confidence ~U(0,1); P(win) = raw (well-specified) -> won ~ Bernoulli."""
    rng = random.Random(seed)
    xs = [rng.random() for _ in range(n)]
    ys = [1 if rng.random() < x else 0 for x in xs]
    return xs, ys


# --------------------------------------------------------------------------- #
# pure-Python calibrators: predict + serialization round-trip
# --------------------------------------------------------------------------- #
def test_constant_calibrator_roundtrip():
    c = ConstantCalibrator(rate=0.42)
    assert c.predict(0.0) == 0.42
    assert c.predict(1.0) == 0.42
    c2 = Calibrator.from_dict(c.to_dict())
    assert isinstance(c2, ConstantCalibrator)
    assert c2.predict(0.3) == pytest.approx(0.42)


def test_platt_calibrator_monotone_and_roundtrip():
    c = PlattCalibrator(a=4.0, b=-2.0)  # sigmoid centered at x=0.5
    assert c.predict(0.0) < c.predict(0.5) < c.predict(1.0)
    assert 0.0 <= c.predict(-10) <= 1.0
    assert 0.0 <= c.predict(10) <= 1.0
    c2 = Calibrator.from_dict(c.to_dict())
    assert c2.predict(0.5) == pytest.approx(c.predict(0.5))


def test_isotonic_calibrator_interpolation_and_clip():
    c = IsotonicCalibrator(xs=[0.0, 0.5, 1.0], ys=[0.1, 0.5, 0.9])
    assert c.predict(0.0) == pytest.approx(0.1)
    assert c.predict(0.25) == pytest.approx(0.3)  # linear interp
    assert c.predict(1.0) == pytest.approx(0.9)
    assert c.predict(-5) == pytest.approx(0.1)    # clamp low
    assert c.predict(5) == pytest.approx(0.9)     # clamp high
    c2 = Calibrator.from_dict(c.to_dict())
    assert c2.predict(0.75) == pytest.approx(c.predict(0.75))


def test_isotonic_sorts_unordered_input():
    c = IsotonicCalibrator(xs=[1.0, 0.0, 0.5], ys=[0.9, 0.1, 0.5])
    assert c.xs == [0.0, 0.5, 1.0]
    assert c.predict(0.25) == pytest.approx(0.3)


def test_decile_calibrator_lookup_and_roundtrip():
    c = DecileCalibrator(edges=[0.0, 0.5, 1.0], rates=[0.2, 0.8])
    assert c.predict(0.1) == pytest.approx(0.2)
    assert c.predict(0.9) == pytest.approx(0.8)
    assert c.predict(-1) == pytest.approx(0.2)
    assert c.predict(2) == pytest.approx(0.8)
    c2 = Calibrator.from_dict(c.to_dict())
    assert c2.predict(0.6) == pytest.approx(0.8)


def test_decile_bad_shape_raises():
    with pytest.raises(ValueError):
        DecileCalibrator(edges=[0.0, 1.0], rates=[0.2, 0.8])


def test_from_dict_unknown_method_raises():
    with pytest.raises(ValueError):
        Calibrator.from_dict({"method": "nope"})


# --------------------------------------------------------------------------- #
# fit: auto-selection by sample size
# --------------------------------------------------------------------------- #
def test_fit_auto_selects_isotonic_for_large_n():
    xs, ys = _synthetic(600)
    c = fit_calibrator(xs, ys, method="auto")
    assert c.method == "isotonic"


def test_fit_auto_selects_platt_for_medium_n():
    xs, ys = _synthetic(80)
    c = fit_calibrator(xs, ys, method="auto")
    assert c.method == "platt"


def test_fit_auto_selects_decile_for_small_n():
    xs, ys = _synthetic(30)
    c = fit_calibrator(xs, ys, method="auto")
    assert c.method == "decile"


def test_fit_constant_for_tiny_n():
    xs, ys = _synthetic(5)
    c = fit_calibrator(xs, ys, method="auto")
    assert c.method == "constant"


def test_fit_degenerate_label_returns_constant_baserate():
    xs = [random.random() for _ in range(400)]
    ys = [1] * 400  # all wins
    c = fit_calibrator(xs, ys, method="auto")
    assert c.method == "constant"
    assert c.predict(0.5) == pytest.approx(1.0)


def test_fit_empty_returns_constant_half():
    c = fit_calibrator([], [], method="auto")
    assert c.method == "constant"
    assert c.predict(0.5) == 0.5


# --------------------------------------------------------------------------- #
# fit recovers a real signal (calibration is meaningful)
# --------------------------------------------------------------------------- #
def test_isotonic_recovers_monotone_signal():
    xs, ys = _synthetic(2000)
    c = fit_calibrator(xs, ys, method="isotonic")
    # predicted P(win) should rise from low-raw to high-raw region
    lo = sum(c.predict(x) for x in (0.05, 0.1, 0.15)) / 3
    hi = sum(c.predict(x) for x in (0.85, 0.9, 0.95)) / 3
    assert hi > lo + 0.3


def test_platt_recovers_monotone_signal():
    xs, ys = _synthetic(2000, seed=7)
    c = fit_calibrator(xs, ys, method="platt")
    assert c.predict(0.9) > c.predict(0.1)


# --------------------------------------------------------------------------- #
# reliability metrics
# --------------------------------------------------------------------------- #
def test_reliability_curve_and_ece_on_wellcalibrated():
    xs, ys = _synthetic(3000, seed=3)
    c = fit_calibrator(xs, ys, method="isotonic")
    preds = c.predict_many(xs)
    curve = reliability_curve(ys, preds, bins=10)
    assert curve  # non-empty
    # well-specified data -> low calibration error
    ece = expected_calibration_error(ys, preds, bins=10)
    assert ece < 0.1


def test_brier_score_bounds():
    assert brier_score([1, 0], [1.0, 0.0]) == 0.0
    assert brier_score([1, 0], [0.0, 1.0]) == pytest.approx(1.0)
    assert brier_score([], []) == 0.0
