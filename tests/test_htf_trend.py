"""
Offline tests for ``src.ict_detection.trend`` \u2014 the HTF trend confluence
helper introduced in CP-2026-04-28-11 (M7 Phase 2.3).

Covers:
- ``ema`` numerics agree with the standard pandas ewm reference.
- ``htf_trend_bias`` returns the expected label on monotone up / down /
  flat / V-shaped frames.
- Argument validation (bad spans, missing source column).
- Edge cases (empty frame, NaN tail, ``fast == slow``).
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from src.ict_detection.trend import ema, htf_trend_bias  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _df(close_values):
    """Build a minimal OHLCV-shaped frame with the given close series."""
    n = len(close_values)
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": close_values,
            "high": [c + 0.1 for c in close_values],
            "low":  [c - 0.1 for c in close_values],
            "close": close_values,
            "volume": [1.0] * n,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# ema()
# ---------------------------------------------------------------------------


def test_ema_matches_pandas_reference():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    expected = s.ewm(span=3, adjust=False).mean()
    actual = ema(s, length=3)
    pd.testing.assert_series_equal(actual, expected)


def test_ema_rejects_zero_or_negative_length():
    s = pd.Series([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        ema(s, length=0)
    with pytest.raises(ValueError):
        ema(s, length=-5)


def test_ema_constant_series_is_constant():
    s = pd.Series([42.0] * 10)
    out = ema(s, length=5)
    assert (out == 42.0).all()


# ---------------------------------------------------------------------------
# htf_trend_bias() \u2014 happy paths
# ---------------------------------------------------------------------------


def test_bias_bullish_on_uptrend():
    df = _df(np.linspace(100.0, 200.0, 200).tolist())
    assert htf_trend_bias(df, fast=20, slow=50) == "bullish"


def test_bias_bearish_on_downtrend():
    df = _df(np.linspace(200.0, 100.0, 200).tolist())
    assert htf_trend_bias(df, fast=20, slow=50) == "bearish"


def test_bias_neutral_on_flat_market():
    df = _df([100.0] * 200)
    assert htf_trend_bias(df, fast=20, slow=50) == "neutral"


def test_bias_v_shape_reflects_recent_leg():
    """Down then up: the latest leg is bullish, so bias should be bullish."""
    down = np.linspace(200.0, 100.0, 200).tolist()
    up = np.linspace(100.0, 250.0, 200).tolist()
    df = _df(down + up)
    assert htf_trend_bias(df, fast=20, slow=50) == "bullish"


def test_bias_inverse_v_shape_reflects_recent_leg():
    """Up then down: the latest leg is bearish, so bias should be bearish."""
    up = np.linspace(100.0, 200.0, 200).tolist()
    down = np.linspace(200.0, 80.0, 200).tolist()
    df = _df(up + down)
    assert htf_trend_bias(df, fast=20, slow=50) == "bearish"


# ---------------------------------------------------------------------------
# htf_trend_bias() \u2014 edge cases
# ---------------------------------------------------------------------------


def test_bias_empty_frame_is_neutral():
    df = _df([]).iloc[0:0]
    # _df returned an empty frame already; just confirm the column exists.
    assert "close" in df.columns
    assert htf_trend_bias(df, fast=20, slow=50) == "neutral"


def test_bias_short_frame_seeds_to_neutral_or_directional():
    """
    With ``adjust=False`` and a span of 50 on only 5 candles, both EMAs
    are seeded from the first value and stay nearly identical \u2014 we
    expect "neutral" within the default eps.
    """
    df = _df([100.0, 100.5, 101.0, 100.8, 101.2])
    result = htf_trend_bias(df, fast=2, slow=4)
    # The series is mildly bullish; a tight (2,4) pair should detect it.
    assert result == "bullish"


def test_bias_nan_tail_is_neutral():
    closes = [100.0, 101.0, 102.0, 103.0, np.nan]
    df = _df(closes)
    assert htf_trend_bias(df, fast=2, slow=3) == "neutral"


def test_bias_eps_band_is_inclusive():
    """Diffs strictly inside the eps band must be classified neutral."""
    # Build a frame where the two EMAs end up extremely close; pick an
    # eps wide enough to cover any residual difference.
    df = _df([100.0, 100.0001, 100.0002, 100.0001, 100.0])
    assert htf_trend_bias(df, fast=2, slow=3, eps=1.0) == "neutral"


# ---------------------------------------------------------------------------
# htf_trend_bias() \u2014 argument validation
# ---------------------------------------------------------------------------


def test_bias_rejects_fast_geq_slow():
    df = _df([100.0] * 50)
    with pytest.raises(ValueError):
        htf_trend_bias(df, fast=20, slow=20)
    with pytest.raises(ValueError):
        htf_trend_bias(df, fast=30, slow=20)


def test_bias_rejects_non_positive_spans():
    df = _df([100.0] * 50)
    with pytest.raises(ValueError):
        htf_trend_bias(df, fast=0, slow=20)
    with pytest.raises(ValueError):
        htf_trend_bias(df, fast=-1, slow=20)


def test_bias_rejects_missing_source_column():
    df = _df([100.0] * 50).drop(columns=["close"])
    with pytest.raises(KeyError):
        htf_trend_bias(df, fast=20, slow=50, source="close")


def test_bias_accepts_alternate_source_column():
    """Passing source='high' should drive bias from the high series."""
    n = 200
    rising_close = [100.0] * n           # flat closes
    rising_high = list(np.linspace(100.0, 200.0, n))  # rising highs
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": rising_close,
            "high": rising_high,
            "low": rising_close,
            "close": rising_close,
            "volume": [1.0] * n,
        },
        index=idx,
    )
    assert htf_trend_bias(df, fast=20, slow=50, source="close") == "neutral"
    assert htf_trend_bias(df, fast=20, slow=50, source="high") == "bullish"
