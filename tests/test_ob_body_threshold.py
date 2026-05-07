"""
Tests for the ``body_min_pct`` filter on ``OrderBlockDetector``.

Covers:

1. Backward compatibility — default ``body_min_pct=0.0`` reproduces the
   original behaviour bit-for-bit.
2. Filtering — a high threshold drops small-body origin candles and
   produces strictly fewer OBs.
3. Non-empty OB detection on a synthetic strong-trend fixture (the case
   the Phase-1 research notebook flagged at the old threshold of 1.5).
4. The threshold is forwarded by ``ICTSignalsAnalyzer.__init__``.

All tests are offline and self-contained.
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from src.ict_detection.order_blocks import OrderBlockDetector, detect_order_blocks  # noqa: E402
from src.ict_detection.swing_points import SwingPointDetector  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _attach_swings(df: "pd.DataFrame", left: int = 2, right: int = 2) -> "pd.DataFrame":
    """Attach ``swing_high`` / ``swing_low`` columns expected by OB detector."""
    swing = SwingPointDetector(left_bars=left, right_bars=right)
    out = df.copy()
    out["swing_high"] = swing.detect_swing_highs(out)
    out["swing_low"] = swing.detect_swing_lows(out)
    return out


def _make_strong_trend_df(n: int = 60) -> "pd.DataFrame":
    """
    Build a deterministic OHLCV frame engineered to produce confirmed
    swing lows and bearish (down) origin candles right before each swing.
    Each "V" pattern is:

        idx-2 idx-1 idx   idx+1 idx+2
         flat flat  bear flat  flat

    The bear candle at ``idx`` has the lowest low of the 5-bar window, so
    ``SwingPointDetector(left_bars=2, right_bars=2)`` flags it as a swing
    low. Its body is ~0.8% of close — above 0.5%, below 1.5% — matching
    the research-notebook regime.
    """
    index = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    base = 100.0
    bear_idxs = {10, 22, 34, 46}
    rows = []
    for i in range(n):
        c = base  # flat baseline so OB origin candles can dominate the window
        if i in bear_idxs:
            # Strong bearish candle: open above close, body ~0.8% of close,
            # low well below baseline so it forms the local swing minimum.
            o = c + (c * 0.008)
            h = o + 0.05
            lo = c - 1.0  # clearly below the surrounding flat lows
            close_ = c
        else:
            # Tiny flat bullish candle.
            o = c - 0.02
            h = c + 0.05
            lo = o - 0.05
            close_ = c
        rows.append({"open": o, "high": h, "low": lo, "close": close_,
                     "volume": 1.0})
    df = pd.DataFrame(rows, index=index)
    return df


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_default_body_threshold_is_zero():
    det = OrderBlockDetector()
    assert det.body_min_pct == 0.0


def test_default_behaviour_matches_old_api():
    """Default-arg construction must produce the same OBs as before."""
    df = _attach_swings(_make_strong_trend_df())
    old_style = OrderBlockDetector(lookback=20)             # only positional arg
    new_style = OrderBlockDetector(lookback=20, body_min_pct=0.0)

    assert old_style.detect_all_order_blocks(df) == new_style.detect_all_order_blocks(df)


# ---------------------------------------------------------------------------
# Filter behaviour
# ---------------------------------------------------------------------------


def test_high_threshold_drops_all_small_body_obs():
    """A 5% body threshold rejects every candle in our synthetic frame."""
    df = _attach_swings(_make_strong_trend_df())
    permissive = OrderBlockDetector(lookback=20, body_min_pct=0.0)
    strict = OrderBlockDetector(lookback=20, body_min_pct=5.0)

    permissive_obs = permissive.detect_all_order_blocks(df)
    strict_obs = strict.detect_all_order_blocks(df)

    assert len(strict_obs) <= len(permissive_obs)
    assert len(strict_obs) == 0
    assert len(permissive_obs) > 0


def test_lowered_threshold_produces_non_zero_obs():
    """
    The headline check from the sprint plan: with the body filter set to
    0.5% (between 0.0 and the research-notebook's blocking 1.5%), the
    detector still produces order blocks on a real trending fixture.
    """
    df = _attach_swings(_make_strong_trend_df())
    det = OrderBlockDetector(lookback=20, body_min_pct=0.5)
    obs = det.detect_all_order_blocks(df)
    assert len(obs) > 0, "expected non-zero OB events at 0.5% body threshold"


def test_threshold_filter_is_monotonic():
    """Raising the body threshold can only remove OBs, never add them."""
    df = _attach_swings(_make_strong_trend_df())
    counts = []
    for thr in (0.0, 0.3, 0.6, 1.0, 2.0):
        det = OrderBlockDetector(lookback=20, body_min_pct=thr)
        counts.append(len(det.detect_all_order_blocks(df)))
    assert counts == sorted(counts, reverse=True), counts


# ---------------------------------------------------------------------------
# Numerical edge cases
# ---------------------------------------------------------------------------


def test_passes_body_filter_zero_close():
    """A zero close price never passes a non-zero filter (no div-by-zero)."""
    det = OrderBlockDetector(body_min_pct=0.5)
    assert det._passes_body_filter(candle_open=10.0, candle_close=0.0) is False


def test_passes_body_filter_disabled_accepts_anything():
    det = OrderBlockDetector(body_min_pct=0.0)
    assert det._passes_body_filter(0.001, 0.0011) is True


# ---------------------------------------------------------------------------
# Convenience function & ICTSignalsAnalyzer wiring
# ---------------------------------------------------------------------------


def test_detect_order_blocks_helper_forwards_threshold():
    df = _attach_swings(_make_strong_trend_df())
    _, lenient = detect_order_blocks(df, lookback=20, body_min_pct=0.0)
    _, strict = detect_order_blocks(df, lookback=20, body_min_pct=5.0)
    assert len(strict) <= len(lenient)
    assert len(strict) == 0


def test_ict_signals_analyzer_forwards_body_threshold():
    """ICTSignalsAnalyzer must accept and forward ``ob_body_min_pct``."""
    from src.core.signals import ICTSignalsAnalyzer

    a = ICTSignalsAnalyzer(symbol="TEST", ob_body_min_pct=0.7)
    assert a._ob.body_min_pct == 0.7

    # Default still zero
    b = ICTSignalsAnalyzer(symbol="TEST")
    assert b._ob.body_min_pct == 0.0
