"""Tests for the range-based volatility estimators (S-MLOPT-S9)."""
from __future__ import annotations

import math

from ml.datasets.volatility_estimators import (
    _sqrt_or_zero,
    garman_klass_var,
    parkinson_var,
    rogers_satchell_var,
    yang_zhang_var,
)


def test_parkinson_known_value():
    # Single bar H=110, L=100: ln(1.1)^2 / (4 ln2).
    expected = (math.log(1.1) ** 2) / (4.0 * math.log(2.0))
    assert math.isclose(parkinson_var([110.0], [100.0]), expected, rel_tol=1e-12)


def test_parkinson_tracks_range_width():
    # A wider intrabar range => higher Parkinson variance.
    narrow = parkinson_var([101.0, 101.0], [99.0, 99.0])
    wide = parkinson_var([120.0, 120.0], [80.0, 80.0])
    assert wide > narrow > 0


def test_garman_klass_positive_and_zero_on_flat():
    o = [100.0, 100.0]
    h = [105.0, 106.0]
    lo = [95.0, 94.0]
    c = [101.0, 99.0]
    assert garman_klass_var(o, h, lo, c) > 0
    # Flat bars (O=H=L=C) → zero variance.
    flat = [100.0, 100.0]
    assert garman_klass_var(flat, flat, flat, flat) == 0.0


def test_rogers_satchell_positive_and_drift_independent():
    # Pure trend with no intrabar excursion beyond O/C → RS ~ 0 (drift-independent).
    o = [100.0, 110.0]
    c = [110.0, 120.0]
    h = [110.0, 120.0]
    lo = [100.0, 110.0]  # H=max(O,C), L=min(O,C)
    rs = rogers_satchell_var(o, h, lo, c)
    assert rs is not None and rs >= 0 and rs < 1e-9
    # Intrabar excursion beyond the body → strictly positive.
    rs2 = rogers_satchell_var([100.0], [115.0], [90.0], [105.0])
    assert rs2 > 0


def test_yang_zhang_combines_terms():
    o = [100.0, 101.0, 102.0]
    h = [103.0, 104.0, 105.0]
    lo = [98.0, 99.0, 100.0]
    c = [101.0, 102.0, 103.0]
    prev = [99.0, 100.5, 101.5]
    yz = yang_zhang_var(o, h, lo, c, prev)
    assert yz is not None and yz > 0


def test_yang_zhang_needs_two_usable_bars():
    # Only one usable overnight return → None.
    assert yang_zhang_var([100.0], [101.0], [99.0], [100.5], [99.5]) is None
    # A None prev_close drops that bar's overnight term.
    assert yang_zhang_var(
        [100.0, 101.0], [102.0, 103.0], [98.0, 99.0], [101.0, 102.0], [None, 100.0]
    ) is None  # only 1 usable bar after dropping the None-prev bar


def test_estimators_skip_nonpositive_and_empty():
    assert parkinson_var([], []) is None
    assert garman_klass_var([0.0], [10.0], [5.0], [8.0]) is None  # bad open skipped → empty
    assert rogers_satchell_var([-1.0], [10.0], [5.0], [8.0]) is None


def test_sqrt_or_zero():
    assert _sqrt_or_zero(None) == 0.0
    assert _sqrt_or_zero(-1.0) == 0.0
    assert math.isclose(_sqrt_or_zero(0.04), 0.2)
