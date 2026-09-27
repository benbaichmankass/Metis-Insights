"""scripts/research/regime_weight.py — the shared soft regime-weight lever.

Pure-function tests (no pandas / no harness), mirroring the coverage shape of
the trail-lever rule this module is modelled on.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))
from regime_weight import soft_regime_weight, soft_weight_armed  # noqa: E402


def test_armed_requires_both_anchors():
    assert soft_weight_armed(None, None) is False
    assert soft_weight_armed(15.0, None) is False
    assert soft_weight_armed(None, 25.0) is False
    assert soft_weight_armed(15.0, 25.0) is True


def test_weight_is_one_at_or_above_ceiling():
    assert soft_regime_weight(25.0, floor=15.0, ceiling=25.0) == 1.0
    assert soft_regime_weight(40.0, floor=15.0, ceiling=25.0) == 1.0


def test_weight_is_min_at_or_below_floor():
    assert soft_regime_weight(15.0, floor=15.0, ceiling=25.0, weight_min=0.2) == 0.2
    assert soft_regime_weight(5.0, floor=15.0, ceiling=25.0, weight_min=0.2) == 0.2


def test_weight_ramps_linearly_between_anchors():
    # Midpoint of a [15, 25] band -> halfway between weight_min and 1.0.
    w = soft_regime_weight(20.0, floor=15.0, ceiling=25.0, weight_min=0.0)
    assert math.isclose(w, 0.5, rel_tol=1e-9)
    w2 = soft_regime_weight(20.0, floor=15.0, ceiling=25.0, weight_min=0.4)
    assert math.isclose(w2, 0.4 + 0.6 * 0.5, rel_tol=1e-9)


def test_default_weight_min_can_flatten_to_zero():
    assert soft_regime_weight(0.0, floor=15.0, ceiling=25.0) == 0.0


def test_undefined_adx_returns_weight_min_not_full_weight():
    # NaN / None ADX (warm-up) is treated like the hard gate's "never admit an
    # undefined regime" — the cautious direction, never a silent full-weight.
    assert soft_regime_weight(None, floor=15.0, ceiling=25.0, weight_min=0.3) == 0.3
    assert soft_regime_weight(float("nan"), floor=15.0, ceiling=25.0, weight_min=0.3) == 0.3


def test_degenerate_band_is_a_step_not_a_crash():
    # ceiling <= floor must report a determinate result, not raise.
    assert soft_regime_weight(30.0, floor=20.0, ceiling=20.0, weight_min=0.1) == 1.0
    assert soft_regime_weight(10.0, floor=20.0, ceiling=20.0, weight_min=0.1) == 0.1
    assert soft_regime_weight(10.0, floor=20.0, ceiling=10.0, weight_min=0.1) == 0.1


def test_weight_never_exceeds_bounds():
    for adx in (-50.0, 0.0, 10.0, 14.999, 15.0, 18.0, 25.0, 100.0):
        w = soft_regime_weight(adx, floor=15.0, ceiling=25.0, weight_min=0.25)
        assert 0.25 - 1e-9 <= w <= 1.0 + 1e-9
