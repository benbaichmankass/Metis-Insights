"""Tests for the ICT detection module public API (S8, M11 refactor).

Validates:
  - All five detector classes importable via the package public API
  - Trend utilities importable via the package public API
  - Sub-module imports still work (no regressions for existing callers)
  - Basic instantiation smoke tests for each detector
  - __all__ is complete and consistent
"""
from __future__ import annotations

class TestPackagePublicAPI:
    def test_fvg_detector_importable(self):
        from src.ict_detection import FVGDetector
        assert FVGDetector is not None

    def test_liquidity_detector_importable(self):
        from src.ict_detection import LiquidityDetector
        assert LiquidityDetector is not None

    def test_order_block_detector_importable(self):
        from src.ict_detection import OrderBlockDetector
        assert OrderBlockDetector is not None

    def test_swing_point_detector_importable(self):
        from src.ict_detection import SwingPointDetector
        assert SwingPointDetector is not None

    def test_key_levels_detector_importable(self):
        from src.ict_detection import KeyLevelsDetector
        assert KeyLevelsDetector is not None

    def test_ema_importable(self):
        from src.ict_detection import ema
        assert callable(ema)

    def test_htf_trend_bias_importable(self):
        from src.ict_detection import htf_trend_bias
        assert callable(htf_trend_bias)

    def test_all_is_complete(self):
        import src.ict_detection as pkg
        expected = {
            "FVGDetector", "LiquidityDetector", "OrderBlockDetector",
            "SwingPointDetector", "KeyLevelsDetector", "ema", "htf_trend_bias",
        }
        assert expected.issubset(set(pkg.__all__))


class TestSubmoduleBackwardCompat:
    """Existing callers import from sub-modules directly — must not break."""

    def test_fvg_from_submodule(self):
        from src.ict_detection.fvg_detector import FVGDetector
        assert FVGDetector is not None

    def test_liquidity_from_submodule(self):
        from src.ict_detection.liquidity import LiquidityDetector
        assert LiquidityDetector is not None

    def test_order_blocks_from_submodule(self):
        from src.ict_detection.order_blocks import OrderBlockDetector
        assert OrderBlockDetector is not None

    def test_swing_points_from_submodule(self):
        from src.ict_detection.swing_points import SwingPointDetector
        assert SwingPointDetector is not None

    def test_key_levels_from_submodule(self):
        from src.ict_detection.key_levels import KeyLevelsDetector
        assert KeyLevelsDetector is not None


class TestDetectorSmoke:
    """Instantiation smoke tests — no data needed."""

    def test_fvg_detector_instantiates(self):
        from src.ict_detection import FVGDetector
        d = FVGDetector()
        assert d is not None

    def test_fvg_detector_min_gap_size(self):
        from src.ict_detection import FVGDetector
        d = FVGDetector(min_gap_size=10.0)
        assert d.min_gap_size == 10.0

    def test_liquidity_detector_instantiates(self):
        from src.ict_detection import LiquidityDetector
        d = LiquidityDetector()
        assert d is not None

    def test_order_block_detector_instantiates(self):
        from src.ict_detection import OrderBlockDetector
        d = OrderBlockDetector()
        assert d is not None

    def test_swing_point_detector_instantiates(self):
        from src.ict_detection import SwingPointDetector
        d = SwingPointDetector()
        assert d is not None

    def test_key_levels_detector_instantiates(self):
        from src.ict_detection import KeyLevelsDetector
        d = KeyLevelsDetector()
        assert d is not None

    def test_package_and_submodule_are_same_class(self):
        from src.ict_detection import FVGDetector as Pkg
        from src.ict_detection.fvg_detector import FVGDetector as Sub
        assert Pkg is Sub
