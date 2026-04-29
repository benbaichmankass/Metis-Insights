"""
Regression tests for key_levels.py pandas-2.x API compatibility.

PR fix: fillna(method='ffill') -> ffill() (removed in pandas 2.2).
"""

import pandas as pd
import numpy as np
import pytest
from datetime import datetime, timedelta

from src.ict_detection.key_levels import KeyLevelsDetector, detect_key_levels


def _make_ohlcv(start="2024-01-15 00:00:00", periods=24, freq="1h"):
    """Build a minimal synthetic OHLCV DataFrame spanning one day."""
    idx = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    rng = np.random.default_rng(42)
    opens = 100 + rng.uniform(-1, 1, periods).cumsum()
    highs = opens + rng.uniform(0, 0.5, periods)
    lows = opens - rng.uniform(0, 0.5, periods)
    closes = opens + rng.uniform(-0.3, 0.3, periods)
    volume = rng.uniform(10, 100, periods)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
        index=idx,
    )


class TestSessionOpenPriceFfill:
    """Regression: ffill of *_open_price columns must not raise TypeError."""

    def test_identify_session_opens_no_error(self):
        df = _make_ohlcv()
        detector = KeyLevelsDetector()
        result = detector.identify_session_opens(df)
        assert "asian_open_price" in result.columns
        assert "london_open_price" in result.columns
        assert "ny_open_price" in result.columns

    def test_asian_open_price_forward_filled(self):
        df = _make_ohlcv(start="2024-01-15 00:00:00", periods=24, freq="1h")
        detector = KeyLevelsDetector()
        result = detector.identify_session_opens(df)

        # Hour-0 row must have the open price set
        asian_row = result[result.index.hour == 0]
        assert not asian_row["asian_open_price"].isna().any()

        # All subsequent rows within the session should be forward-filled (not NaN
        # unless a new session open resets the value — we just check no all-NaN block)
        non_asian = result[result.index.hour != 0]
        # After hour-0 has been set, forward fill means all hours 1-7 carry the value
        hours_1_to_7 = result[(result.index.hour >= 1) & (result.index.hour < 8)]
        assert not hours_1_to_7["asian_open_price"].isna().any()

    def test_london_open_price_forward_filled(self):
        df = _make_ohlcv(start="2024-01-15 00:00:00", periods=24, freq="1h")
        detector = KeyLevelsDetector()
        result = detector.identify_session_opens(df)

        hours_9_to_12 = result[(result.index.hour >= 9) & (result.index.hour < 13)]
        assert not hours_9_to_12["london_open_price"].isna().any()

    def test_ny_open_price_forward_filled(self):
        df = _make_ohlcv(start="2024-01-15 00:00:00", periods=24, freq="1h")
        detector = KeyLevelsDetector()
        result = detector.identify_session_opens(df)

        hours_14_to_23 = result[(result.index.hour >= 14) & (result.index.hour <= 23)]
        assert not hours_14_to_23["ny_open_price"].isna().any()

    def test_open_price_values_match_session_candle(self):
        """The ffilled value must equal the open price of the session-open candle."""
        df = _make_ohlcv(start="2024-01-15 00:00:00", periods=24, freq="1h")
        detector = KeyLevelsDetector()
        result = detector.identify_session_opens(df)

        asian_open_val = result.loc[result.index.hour == 0, "open"].iloc[0]
        for _, row in result[(result.index.hour >= 0) & (result.index.hour < 8)].iterrows():
            assert row["asian_open_price"] == pytest.approx(asian_open_val)

        london_open_val = result.loc[result.index.hour == 8, "open"].iloc[0]
        for _, row in result[(result.index.hour >= 8) & (result.index.hour < 13)].iterrows():
            assert row["london_open_price"] == pytest.approx(london_open_val)

        ny_open_val = result.loc[result.index.hour == 13, "open"].iloc[0]
        for _, row in result[(result.index.hour >= 13) & (result.index.hour <= 23)].iterrows():
            assert row["ny_open_price"] == pytest.approx(ny_open_val)


class TestGetAllKeyLevels:
    """Smoke test for the full pipeline via get_all_key_levels."""

    def test_returns_dataframe(self):
        df = _make_ohlcv()
        result = detect_key_levels(df)
        assert isinstance(result, pd.DataFrame)

    def test_expected_columns_present(self):
        df = _make_ohlcv()
        result = detect_key_levels(df)
        for col in ("asian_open_price", "london_open_price", "ny_open_price"):
            assert col in result.columns, f"Missing column: {col}"

    def test_no_nan_after_first_session_open(self):
        """After the Asian open at hour 0, the price must propagate all day."""
        df = _make_ohlcv(start="2024-01-15 00:00:00", periods=24, freq="1h")
        result = detect_key_levels(df)
        # All 24 hours start with Asian, so asian_open_price should have no NaNs
        assert result["asian_open_price"].isna().sum() == 0
