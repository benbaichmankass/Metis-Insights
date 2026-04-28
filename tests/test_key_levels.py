"""
Tests for src/ict_detection/key_levels.py.

Combines:
- Regression tests for pandas-2.x API compatibility
  (`fillna(method='ffill')` -> `ffill()`, removed in pandas 2.2).
- Smoke tests for KeyLevelsDetector.calculate_daily_levels,
  calculate_weekly_levels, identify_session_opens, get_all_key_levels,
  and the detect_key_levels convenience function.

Requires pandas / numpy — skipped automatically when not installed,
and also skipped when another test module has stubbed pandas as a
MagicMock via sys.modules.
"""
import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

# Guard: another test file may have stubbed pandas as MagicMock via sys.modules.
if not isinstance(getattr(pd, "DataFrame", None), type):
    pytest.skip("pandas is mocked by another test module", allow_module_level=True)

from datetime import datetime, timedelta

from src.ict_detection.key_levels import KeyLevelsDetector, detect_key_levels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n: int = 48, start: datetime | None = None) -> pd.DataFrame:
    """Return n rows of 1-hour OHLCV data with a DatetimeIndex (linear prices)."""
    if start is None:
        start = datetime(2026, 1, 6, 0, 0)  # Monday
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    prices = np.linspace(100.0, 120.0, n)
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices + 2,
            "low": prices - 2,
            "close": prices + 1,
            "volume": 1.0,
        },
        index=idx,
    )


def _make_ohlcv(start="2024-01-15 00:00:00", periods=24, freq="1h"):
    """Build a synthetic OHLCV DataFrame using a seeded RNG (random walks)."""
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


# ---------------------------------------------------------------------------
# calculate_daily_levels
# ---------------------------------------------------------------------------

class TestCalculateDailyLevels:
    def test_returns_dataframe(self):
        df = _make_df()
        det = KeyLevelsDetector()
        result = det.calculate_daily_levels(df)
        assert isinstance(result, pd.DataFrame)

    def test_pdh_pdl_columns_added(self):
        df = _make_df()
        det = KeyLevelsDetector()
        result = det.calculate_daily_levels(df)
        assert "pdh" in result.columns
        assert "pdl" in result.columns

    def test_original_not_mutated(self):
        df = _make_df()
        original_cols = set(df.columns)
        KeyLevelsDetector().calculate_daily_levels(df)
        assert set(df.columns) == original_cols

    def test_date_column_removed(self):
        df = _make_df()
        result = KeyLevelsDetector().calculate_daily_levels(df)
        assert "date" not in result.columns

    def test_pdh_ge_pdl_where_both_present(self):
        df = _make_df(72)
        result = KeyLevelsDetector().calculate_daily_levels(df)
        valid = result.dropna(subset=["pdh", "pdl"])
        assert (valid["pdh"] >= valid["pdl"]).all()


# ---------------------------------------------------------------------------
# calculate_weekly_levels
# ---------------------------------------------------------------------------

class TestCalculateWeeklyLevels:
    def test_pwh_pwl_columns_added(self):
        df = _make_df(200)
        result = KeyLevelsDetector().calculate_weekly_levels(df)
        assert "pwh" in result.columns
        assert "pwl" in result.columns

    def test_temp_columns_removed(self):
        df = _make_df(200)
        result = KeyLevelsDetector().calculate_weekly_levels(df)
        for col in ("week", "year", "year_week"):
            assert col not in result.columns

    def test_returns_dataframe(self):
        df = _make_df(200)
        result = KeyLevelsDetector().calculate_weekly_levels(df)
        assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# identify_session_opens — both schema smoke tests AND ffill regression tests
# ---------------------------------------------------------------------------

class TestIdentifySessionOpens:
    def test_session_open_columns_added(self):
        df = _make_df(48)
        result = KeyLevelsDetector().identify_session_opens(df)
        for col in ("asian_open", "london_open", "ny_open",
                    "asian_open_price", "london_open_price", "ny_open_price"):
            assert col in result.columns

    def test_asian_open_at_midnight(self):
        df = _make_df(48)
        result = KeyLevelsDetector().identify_session_opens(df)
        midnight_rows = result[result.index.hour == 0]
        assert midnight_rows["asian_open"].all()

    def test_london_open_at_0800(self):
        df = _make_df(48)
        result = KeyLevelsDetector().identify_session_opens(df)
        london_rows = result[result.index.hour == 8]
        assert london_rows["london_open"].all()

    def test_hour_column_removed(self):
        df = _make_df(48)
        result = KeyLevelsDetector().identify_session_opens(df)
        assert "hour" not in result.columns


class TestSessionOpenPriceFfill:
    """Regression: ffill of *_open_price columns must not raise TypeError
    on pandas >= 2.2 (where fillna(method='ffill') was removed)."""

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

        # After hour-0 has been set, forward fill means hours 1-7 carry the value
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


# ---------------------------------------------------------------------------
# get_all_key_levels — schema, row count, and post-ffill NaN behaviour
# ---------------------------------------------------------------------------

class TestGetAllKeyLevels:
    def test_all_columns_present(self):
        df = _make_df(200)
        result = KeyLevelsDetector().get_all_key_levels(df)
        for col in ("pdh", "pdl", "pwh", "pwl",
                    "asian_open_price", "london_open_price", "ny_open_price"):
            assert col in result.columns

    def test_row_count_preserved(self):
        df = _make_df(200)
        result = KeyLevelsDetector().get_all_key_levels(df)
        assert len(result) == len(df)

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


# ---------------------------------------------------------------------------
# convenience function (top-level)
# ---------------------------------------------------------------------------

def test_detect_key_levels_convenience():
    df = _make_df(200)
    result = detect_key_levels(df)
    assert isinstance(result, pd.DataFrame)
    assert "pdh" in result.columns
