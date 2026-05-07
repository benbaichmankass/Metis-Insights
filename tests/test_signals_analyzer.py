"""
Unit tests for src.core.signals.ICTSignalsAnalyzer.

All tests use synthetic OHLCV DataFrames so no external data files or
network access are required.

Skipped automatically when pandas / numpy are not installed.
"""

import random
import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from src.core.signals import ICTSignalsAnalyzer, ict_signal_from_df, _validate_ohlcv  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_df(n: int = 60, seed: int = 42) -> "pd.DataFrame":
    """Return a realistic-looking 5-minute OHLCV DataFrame."""
    rng = random.Random(seed)
    index = pd.date_range("2024-01-15 08:00", periods=n, freq="5min", tz="UTC")
    prices = [40_000.0]
    for _ in range(n - 1):
        prices.append(prices[-1] + rng.gauss(0, 50))

    rows = []
    for p in prices:
        noise = rng.uniform(10, 80)
        o = p + rng.gauss(0, 20)
        h = max(o, p) + noise
        lo = min(o, p) - noise
        c = p
        v = rng.uniform(1, 10)
        rows.append({"open": o, "high": h, "low": lo, "close": c, "volume": v})

    return pd.DataFrame(rows, index=index)


def _make_bullish_fvg_df() -> "pd.DataFrame":
    """Three-candle pattern guaranteed to produce a bullish FVG."""
    index = pd.date_range("2024-01-15 12:00", periods=5, freq="5min", tz="UTC")
    # Candle 0: high=100, Candle 2: low=110 → bullish FVG gap [100, 110]
    data = {
        "open":   [95,  105, 112, 115, 118],
        "high":   [100, 108, 120, 122, 125],
        "low":    [90,  100, 110, 112, 116],
        "close":  [98,  106, 118, 120, 122],
        "volume": [1,   1,   1,   1,   1],
    }
    return pd.DataFrame(data, index=index)


def _make_bearish_fvg_df() -> "pd.DataFrame":
    """Three-candle pattern guaranteed to produce a bearish FVG."""
    index = pd.date_range("2024-01-15 12:00", periods=5, freq="5min", tz="UTC")
    # Candle 0: low=110, Candle 2: high=100 → bearish FVG gap [100, 110]
    data = {
        "open":   [115, 105, 98, 96, 94],
        "high":   [120, 108, 100, 98, 96],
        "low":    [110, 100, 88, 86, 84],
        "close":  [112, 102, 90, 88, 86],
        "volume": [1,   1,   1,  1,  1],
    }
    return pd.DataFrame(data, index=index)


# ---------------------------------------------------------------------------
# _validate_ohlcv
# ---------------------------------------------------------------------------

class TestValidateOHLCV:
    def test_passes_valid_df(self):
        _validate_ohlcv(_make_df())

    def test_raises_on_missing_column(self):
        df = _make_df().drop(columns=["volume"])
        with pytest.raises(ValueError, match="missing columns"):
            _validate_ohlcv(df)

    def test_raises_on_too_few_rows(self):
        df = _make_df().head(2)
        with pytest.raises(ValueError, match="at least 3 rows"):
            _validate_ohlcv(df)


# ---------------------------------------------------------------------------
# detect_fvg
# ---------------------------------------------------------------------------

class TestDetectFVG:
    def test_returns_list(self):
        result = ICTSignalsAnalyzer().detect_fvg(_make_df())
        assert isinstance(result, list)

    def test_bullish_fvg_detected(self):
        fvgs = ICTSignalsAnalyzer().detect_fvg(_make_bullish_fvg_df())
        assert any(f["type"] == "bullish" for f in fvgs)

    def test_bearish_fvg_detected(self):
        fvgs = ICTSignalsAnalyzer().detect_fvg(_make_bearish_fvg_df())
        assert any(f["type"] == "bearish" for f in fvgs)

    def test_fvg_dict_keys(self):
        fvgs = ICTSignalsAnalyzer().detect_fvg(_make_bullish_fvg_df())
        assert len(fvgs) > 0
        required = {"type", "start_time", "end_time", "gap_low", "gap_high", "gap_size", "filled"}
        for fvg in fvgs:
            assert required.issubset(fvg.keys())

    def test_gap_size_positive(self):
        for fvg in ICTSignalsAnalyzer().detect_fvg(_make_df(n=200)):
            assert fvg["gap_size"] > 0

    def test_fvg_min_gap_filters(self):
        fvgs = ICTSignalsAnalyzer(fvg_min_gap=1_000_000).detect_fvg(_make_df())
        assert fvgs == []


# ---------------------------------------------------------------------------
# detect_order_blocks
# ---------------------------------------------------------------------------

class TestDetectOrderBlocks:
    def test_returns_list(self):
        obs = ICTSignalsAnalyzer().detect_order_blocks(_make_df(n=100))
        assert isinstance(obs, list)

    def test_ob_dict_keys(self):
        obs = ICTSignalsAnalyzer().detect_order_blocks(_make_df(n=100))
        if obs:
            required = {"type", "timestamp", "high", "low", "open", "close", "tested"}
            for ob in obs:
                assert required.issubset(ob.keys())

    def test_ob_type_values(self):
        for ob in ICTSignalsAnalyzer().detect_order_blocks(_make_df(n=100)):
            assert ob["type"] in ("bullish", "bearish")

    def test_ob_high_gte_low(self):
        for ob in ICTSignalsAnalyzer().detect_order_blocks(_make_df(n=100)):
            assert ob["high"] >= ob["low"]


# ---------------------------------------------------------------------------
# get_kill_zones
# ---------------------------------------------------------------------------

class TestGetKillZones:
    def test_returns_three_zones(self):
        kz = ICTSignalsAnalyzer().get_kill_zones(_make_df())
        assert set(kz.keys()) == {"asia", "london", "new_york"}

    def test_mask_is_boolean_series(self):
        for name, series in ICTSignalsAnalyzer().get_kill_zones(_make_df()).items():
            assert isinstance(series, pd.Series)
            assert series.dtype == bool, f"{name} mask is not bool"

    def test_mask_length_matches_df(self):
        df = _make_df(n=80)
        for series in ICTSignalsAnalyzer().get_kill_zones(df).values():
            assert len(series) == len(df)

    def test_raises_on_non_datetime_index(self):
        df = _make_df().reset_index(drop=True)
        with pytest.raises(TypeError, match="DatetimeIndex"):
            ICTSignalsAnalyzer().get_kill_zones(df)

    def test_london_hours_active(self):
        index = pd.date_range("2024-01-15 07:00", periods=4, freq="1h", tz="UTC")
        df = pd.DataFrame(
            {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.0, "volume": 1.0},
            index=index,
        )
        kz = ICTSignalsAnalyzer().get_kill_zones(df)
        assert kz["london"].iloc[0]       # 07:00 UTC – in [7,10)
        assert kz["london"].iloc[2]       # 09:00 UTC – in [7,10)
        assert not kz["london"].iloc[3]   # 10:00 UTC – outside

    def test_new_york_hours_active(self):
        index = pd.date_range("2024-01-15 12:00", periods=4, freq="1h", tz="UTC")
        df = pd.DataFrame(
            {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.0, "volume": 1.0},
            index=index,
        )
        kz = ICTSignalsAnalyzer().get_kill_zones(df)
        assert kz["new_york"].iloc[0]      # 12:00 UTC – in [12,15)
        assert kz["new_york"].iloc[2]      # 14:00 UTC – in [12,15)
        assert not kz["new_york"].iloc[3]  # 15:00 UTC – outside


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

class TestAnalyze:
    def test_returns_dict_with_required_keys(self):
        result = ICTSignalsAnalyzer(symbol="BTC/USDT").analyze(_make_df())
        expected = {
            "symbol", "timeframe_rows", "fvgs", "order_blocks",
            "kill_zones", "latest_signal", "latest_price",
        }
        assert expected.issubset(result.keys())

    def test_symbol_propagated(self):
        assert ICTSignalsAnalyzer(symbol="ETH/USDT").analyze(_make_df())["symbol"] == "ETH/USDT"

    def test_timeframe_rows(self):
        df = _make_df(n=75)
        assert ICTSignalsAnalyzer().analyze(df)["timeframe_rows"] == 75

    def test_latest_signal_valid(self):
        result = ICTSignalsAnalyzer().analyze(_make_df())
        assert result["latest_signal"] in (None, "long", "short")

    def test_kill_zones_serializable(self):
        result = ICTSignalsAnalyzer().analyze(_make_df())
        for v in result["kill_zones"].values():
            assert isinstance(v, dict)


# ---------------------------------------------------------------------------
# ict_signal_from_df
# ---------------------------------------------------------------------------

class TestICTSignalFromDf:
    def test_returns_triple(self):
        result = ict_signal_from_df(_make_df())
        assert isinstance(result, tuple) and len(result) == 3

    def test_none_triple_outside_kill_zones(self):
        # 06:00–06:55 UTC is outside all kill-zone windows
        index = pd.date_range("2024-01-15 06:00", periods=30, freq="1min", tz="UTC")
        df = pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0},
            index=index,
        )
        direction, price, meta = ict_signal_from_df(df)
        assert direction is None
        assert price is None
        assert meta is None

    def test_signal_values_when_present(self):
        direction, price, meta = ict_signal_from_df(_make_df(n=200))
        if direction is not None:
            assert direction in ("long", "short")
            assert isinstance(price, float)
            assert "fvgs" in meta and "order_blocks" in meta


# ---------------------------------------------------------------------------
# plot (smoke test – only if plotly is available)
# ---------------------------------------------------------------------------

class TestPlot:
    def test_returns_none_when_no_plotly(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "plotly.graph_objects":
                raise ImportError("no plotly")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        fig = ICTSignalsAnalyzer().plot(_make_df())
        assert fig is None

    def test_returns_figure_when_plotly_available(self):
        plotly_go = pytest.importorskip("plotly.graph_objects")
        fig = ICTSignalsAnalyzer().plot(_make_df(n=100))
        assert isinstance(fig, plotly_go.Figure)
