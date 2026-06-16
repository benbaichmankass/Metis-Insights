"""Confidence calibrators — map a raw, strategy-specific score to a calibrated
``P(win)`` on a common basis (design doc § 4a).

Each strategy normalizes a *different* geometric quantity to ``[0, 1]`` (breakout
depth in ATRs, VWAP deviation in sigma, body/range blends, ...), so a raw ``0.7``
is not comparable across strategies. A calibrator fit on ``(raw_confidence, won)``
pairs maps each strategy's raw score onto realized ``P(win)`` so the downstream
conviction blend (``src/runtime/conviction.py``) adds comparable quantities.

**Fit/predict split (deliberate):** fitting uses sklearn (isotonic/logistic) and
runs *offline* on the trainer/CI. ``predict`` is **pure Python from a serialized
dict** so the live trader venv needs no sklearn/numpy to score a calibrated
confidence at signal time.

Calibrators serialize via ``to_dict()`` / ``from_dict()`` (JSON-safe) so a fitted
calibrator can ship as a small artifact the live path loads read-only.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Sequence


# --------------------------------------------------------------------------- #
# Pure-Python predict-side calibrators (no sklearn/numpy at predict time)
# --------------------------------------------------------------------------- #
@dataclass
class Calibrator:
    """A fitted raw-score -> P(win) mapping. ``predict`` is pure Python."""

    method: str

    def predict(self, x: float) -> float:  # pragma: no cover - overridden
        raise NotImplementedError

    def predict_many(self, xs: Sequence[float]) -> list[float]:
        return [self.predict(float(x)) for x in xs]

    def to_dict(self) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- (de)serialization dispatch -------------------------------------- #
    @staticmethod
    def from_dict(d: dict) -> "Calibrator":
        method = d.get("method")
        if method == "isotonic":
            return IsotonicCalibrator.from_dict(d)
        if method == "platt":
            return PlattCalibrator.from_dict(d)
        if method == "decile":
            return DecileCalibrator.from_dict(d)
        if method == "constant":
            return ConstantCalibrator.from_dict(d)
        raise ValueError(f"unknown calibrator method: {method!r}")


@dataclass
class ConstantCalibrator(Calibrator):
    """Degenerate fallback for tiny samples: always returns the base win-rate.

    Used when there are too few rows to fit any monotone mapping; honest about
    having no discriminating signal rather than overfitting noise.
    """

    rate: float = 0.5

    def __init__(self, rate: float = 0.5):
        super().__init__(method="constant")
        self.rate = _clip01(rate)

    def predict(self, x: float) -> float:
        return self.rate

    def to_dict(self) -> dict:
        return {"method": "constant", "rate": self.rate}

    @staticmethod
    def from_dict(d: dict) -> "ConstantCalibrator":
        return ConstantCalibrator(rate=float(d["rate"]))


@dataclass
class PlattCalibrator(Calibrator):
    """Logistic (1-parameter sigmoid) calibration: ``sigmoid(a*x + b)``.

    Robust for small/medium samples; assumes a monotone sigmoid relationship.
    """

    a: float = 1.0
    b: float = 0.0

    def __init__(self, a: float, b: float):
        super().__init__(method="platt")
        self.a = float(a)
        self.b = float(b)

    def predict(self, x: float) -> float:
        z = self.a * float(x) + self.b
        # numerically stable logistic
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def to_dict(self) -> dict:
        return {"method": "platt", "a": self.a, "b": self.b}

    @staticmethod
    def from_dict(d: dict) -> "PlattCalibrator":
        return PlattCalibrator(a=float(d["a"]), b=float(d["b"]))


@dataclass
class IsotonicCalibrator(Calibrator):
    """Monotone piecewise-linear calibration from sorted breakpoints.

    Handles the saturation/non-linearity seen in raw strategy confidences (e.g.
    the old htf_pullback ``min(depth, 1)`` that pinned at 1.0). Predict is a
    clamped linear interpolation over the stored ``(x, y)`` breakpoints.
    """

    xs: list[float] = None  # type: ignore[assignment]
    ys: list[float] = None  # type: ignore[assignment]

    def __init__(self, xs: Sequence[float], ys: Sequence[float]):
        super().__init__(method="isotonic")
        if not xs or len(xs) != len(ys):
            raise ValueError("isotonic breakpoints must be non-empty and aligned")
        # ensure sorted by x
        pairs = sorted(zip((float(x) for x in xs), (float(y) for y in ys)))
        self.xs = [p[0] for p in pairs]
        self.ys = [_clip01(p[1]) for p in pairs]

    def predict(self, x: float) -> float:
        x = float(x)
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect.bisect_right(xs, x)
        x0, x1 = xs[i - 1], xs[i]
        y0, y1 = ys[i - 1], ys[i]
        if x1 == x0:
            return y1
        frac = (x - x0) / (x1 - x0)
        return _clip01(y0 + frac * (y1 - y0))

    def to_dict(self) -> dict:
        return {"method": "isotonic", "xs": self.xs, "ys": self.ys}

    @staticmethod
    def from_dict(d: dict) -> "IsotonicCalibrator":
        return IsotonicCalibrator(xs=list(d["xs"]), ys=list(d["ys"]))


@dataclass
class DecileCalibrator(Calibrator):
    """Equal-frequency binning: empirical win-rate per bin (most robust for
    small samples). ``edges`` has ``len(rates)+1`` entries; predict looks up the
    bin and returns its win rate.
    """

    edges: list[float] = None  # type: ignore[assignment]
    rates: list[float] = None  # type: ignore[assignment]

    def __init__(self, edges: Sequence[float], rates: Sequence[float]):
        super().__init__(method="decile")
        if len(edges) != len(rates) + 1:
            raise ValueError("edges must have len(rates)+1 entries")
        self.edges = [float(e) for e in edges]
        self.rates = [_clip01(r) for r in rates]

    def predict(self, x: float) -> float:
        x = float(x)
        if x <= self.edges[0]:
            return self.rates[0]
        if x >= self.edges[-1]:
            return self.rates[-1]
        i = bisect.bisect_right(self.edges, x) - 1
        i = max(0, min(i, len(self.rates) - 1))
        return self.rates[i]

    def to_dict(self) -> dict:
        return {"method": "decile", "edges": self.edges, "rates": self.rates}

    @staticmethod
    def from_dict(d: dict) -> "DecileCalibrator":
        return DecileCalibrator(edges=list(d["edges"]), rates=list(d["rates"]))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _clip01(v: float) -> float:
    v = float(v)
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v
