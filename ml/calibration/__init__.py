"""Confidence calibration (design doc § 4a).

Maps raw, strategy-specific confidences (and model-head scores) onto a common
``P(win)`` basis so the conviction blend adds comparable quantities.

* ``calibrators`` — pure-Python predict-side calibrators (no sklearn at predict).
* ``fit`` — offline fitting (sklearn) + reliability metrics.
"""

from .calibrators import (
    Calibrator,
    ConstantCalibrator,
    DecileCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
)
from .fit import (
    ReliabilityBin,
    brier_score,
    expected_calibration_error,
    fit_calibrator,
    reliability_curve,
)

__all__ = [
    "Calibrator",
    "ConstantCalibrator",
    "DecileCalibrator",
    "IsotonicCalibrator",
    "PlattCalibrator",
    "ReliabilityBin",
    "brier_score",
    "expected_calibration_error",
    "fit_calibrator",
    "reliability_curve",
]
