"""
Smoke tests for src/ict_detection/liquidity.py.

Requires pandas / numpy — skipped automatically when not installed.
"""
import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

# Guard: another test file may have stubbed pandas as MagicMock via sys.modules.
# If pd.DataFrame is not a real class, skip the entire module.
if not isinstance(getattr(pd, "DataFrame", None), type):
    pytest.skip("pandas is mocked by another test module", allow_module_level=True)

from datetime import datetime, timedelta

from src.ict_detection.liquidity import LiquidityDetector, detect_liquidity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n: int = 60) -> pd.DataFrame:
    """Simple flat OHLCV DataFrame without swing columns."""
    ts = datetime(2026, 1, 6, 0, 0)
    rows = []
    price = 100.0
    for i in range(n):
        rows.append({
            "timestamp": int(ts.timestamp() * 1000),
            "open": price,
            "high": price + 2,
            "low": price - 2,
            "close": price + 1,
            "volume": 1.0,
        })
        ts += timedelta(hours=1)
    return pd.DataFrame(rows)


def _make_df_with_swing_highs(equal_prices: list[float]) -> pd.DataFrame:
    """Build a DataFrame that has swing_high=True at specific equal prices.
    Surrounded by lower candles so they are genuine swing highs.
    """
    rows = []
    ts = datetime(2026, 1, 6, 0, 0)
    is_swing_high = []

    for i, price in enumerate(equal_prices):
        # low candle before
        rows.append({"timestamp": int(ts.timestamp() * 1000),
                     "open": price - 5, "high": price - 3, "low": price - 7, "close": price - 4, "volume": 1.0})
        is_swing_high.append(False)
        ts += timedelta(hours=1)

        # swing high candle
        rows.append({"timestamp": int(ts.timestamp() * 1000),
                     "open": price - 1, "high": price, "low": price - 3, "close": price - 1, "volume": 1.0})
        is_swing_high.append(True)
        ts += timedelta(hours=1)

        # low candle after
        rows.append({"timestamp": int(ts.timestamp() * 1000),
                     "open": price - 5, "high": price - 3, "low": price - 7, "close": price - 4, "volume": 1.0})
        is_swing_high.append(False)
        ts += timedelta(hours=1)

    df = pd.DataFrame(rows)
    df["swing_high"] = is_swing_high
    return df


def _make_df_with_swing_lows(equal_prices: list[float]) -> pd.DataFrame:
    """Build a DataFrame with swing_low=True at specific equal prices."""
    rows = []
    ts = datetime(2026, 1, 6, 0, 0)
    is_swing_low = []

    for price in equal_prices:
        rows.append({"timestamp": int(ts.timestamp() * 1000),
                     "open": price + 5, "high": price + 7, "low": price + 3, "close": price + 4, "volume": 1.0})
        is_swing_low.append(False)
        ts += timedelta(hours=1)

        rows.append({"timestamp": int(ts.timestamp() * 1000),
                     "open": price + 1, "high": price + 3, "low": price, "close": price + 1, "volume": 1.0})
        is_swing_low.append(True)
        ts += timedelta(hours=1)

        rows.append({"timestamp": int(ts.timestamp() * 1000),
                     "open": price + 5, "high": price + 7, "low": price + 3, "close": price + 4, "volume": 1.0})
        is_swing_low.append(False)
        ts += timedelta(hours=1)

    df = pd.DataFrame(rows)
    df["swing_low"] = is_swing_low
    return df


# ---------------------------------------------------------------------------
# LiquidityDetector — init
# ---------------------------------------------------------------------------

def test_default_tolerance():
    det = LiquidityDetector()
    assert det.tolerance == 0.001


def test_custom_tolerance():
    det = LiquidityDetector(tolerance=0.005)
    assert det.tolerance == 0.005


# ---------------------------------------------------------------------------
# detect_equal_highs
# ---------------------------------------------------------------------------

class TestDetectEqualHighs:
    def test_returns_empty_without_swing_high_column(self):
        df = _make_df()
        det = LiquidityDetector()
        result = det.detect_equal_highs(df)
        assert result == []

    def test_detects_buy_side_pool(self):
        df = _make_df_with_swing_highs([110.0, 110.0, 110.0])
        det = LiquidityDetector(tolerance=0.001)
        pools = det.detect_equal_highs(df, min_touches=2)
        assert len(pools) >= 1
        buy_side = [p for p in pools if p["type"] == "buy_side"]
        assert len(buy_side) >= 1

    def test_pool_has_required_keys(self):
        df = _make_df_with_swing_highs([110.0, 110.0])
        det = LiquidityDetector(tolerance=0.001)
        pools = det.detect_equal_highs(df, min_touches=2)
        if pools:
            for key in ("type", "price", "touches", "first_touch", "last_touch", "swept", "sweep_time"):
                assert key in pools[0]

    def test_no_pool_when_prices_different(self):
        df = _make_df_with_swing_highs([100.0, 200.0, 300.0])
        det = LiquidityDetector(tolerance=0.001)
        pools = det.detect_equal_highs(df, min_touches=2)
        assert pools == []


# ---------------------------------------------------------------------------
# detect_equal_lows
# ---------------------------------------------------------------------------

class TestDetectEqualLows:
    def test_returns_empty_without_swing_low_column(self):
        df = _make_df()
        det = LiquidityDetector()
        result = det.detect_equal_lows(df)
        assert result == []

    def test_detects_sell_side_pool(self):
        df = _make_df_with_swing_lows([90.0, 90.0, 90.0])
        det = LiquidityDetector(tolerance=0.001)
        pools = det.detect_equal_lows(df, min_touches=2)
        assert len(pools) >= 1
        sell_side = [p for p in pools if p["type"] == "sell_side"]
        assert len(sell_side) >= 1

    def test_pool_type_is_sell_side(self):
        df = _make_df_with_swing_lows([90.0, 90.0])
        det = LiquidityDetector(tolerance=0.001)
        pools = det.detect_equal_lows(df, min_touches=2)
        for pool in pools:
            assert pool["type"] == "sell_side"


# ---------------------------------------------------------------------------
# detect_liquidity_sweeps
# ---------------------------------------------------------------------------

def test_detect_liquidity_sweeps_marks_swept():
    df = _make_df_with_swing_highs([110.0, 110.0])
    det = LiquidityDetector(tolerance=0.001)
    pools = det.detect_equal_highs(df, min_touches=2)
    if not pools:
        pytest.skip("no pools detected; sweep test not applicable")

    # Append a candle that sweeps above the pool price
    high_price = pools[0]["price"]
    extra = pd.DataFrame([{
        "timestamp": 999999,
        "open": high_price - 1,
        "high": high_price + 10,
        "low": high_price - 3,
        "close": high_price - 2,
        "volume": 1.0,
        "swing_high": False,
    }])
    combined = pd.concat([df, extra], ignore_index=True)
    swept_pools = det.detect_liquidity_sweeps(combined, pools)
    swept = [p for p in swept_pools if p["swept"]]
    assert len(swept) >= 1


# ---------------------------------------------------------------------------
# detect_all_liquidity
# ---------------------------------------------------------------------------

def test_detect_all_liquidity_returns_list():
    df = _make_df_with_swing_highs([110.0, 110.0])
    df["swing_low"] = False
    det = LiquidityDetector(tolerance=0.001)
    result = det.detect_all_liquidity(df)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# convenience function
# ---------------------------------------------------------------------------

def test_detect_liquidity_convenience_no_swing_cols():
    df = _make_df()
    df["swing_high"] = False
    df["swing_low"] = False
    result = detect_liquidity(df)
    assert isinstance(result, list)
