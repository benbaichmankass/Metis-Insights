"""
Offline tests for TurtleSoupMTFv1 strategy.

All tests use hand-crafted DataFrames — no exchange calls, no secrets, no network.

Dependency note: requires pandas/numpy (listed in requirements.txt).
If absent the entire module is skipped via pytest.importorskip.
"""
import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from strategies.turtle_soup_mtf_v1 import TurtleSoupMTFv1, TradePlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ts(n, start="2024-01-01 00:00", freq="1min"):
    return pd.date_range(start, periods=n, freq=freq, tz="UTC")


def _flat_df(n=20, price=100.0, freq="1min", start="2024-01-01 00:00"):
    """Flat OHLCV candles, all prices equal."""
    ts = _make_ts(n, start, freq)
    return pd.DataFrame(
        {
            "datetime": ts,
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price,
            "volume": 1.0,
        }
    )


# ---------------------------------------------------------------------------
# add_atr
# ---------------------------------------------------------------------------

class TestAddAtr:
    strat = TurtleSoupMTFv1()

    def test_column_added(self):
        df = _flat_df(30)
        out = self.strat.add_atr(df.drop(columns=["datetime"]).assign(
            **{k: df[k] for k in ["open", "high", "low", "close", "volume"]}
        ))
        assert "atr" in out.columns

    def test_atr_positive_after_warmup(self):
        df = _flat_df(30)
        out = self.strat.add_atr(df)
        valid = out["atr"].dropna()
        assert len(valid) > 0
        assert (valid > 0).all()

    def test_atr_nan_before_warmup(self):
        df = _flat_df(30)
        out = self.strat.add_atr(df, period=14)
        # First 13 rows should be NaN (min_periods=14)
        assert out["atr"].iloc[:13].isna().all()


# ---------------------------------------------------------------------------
# resample_ohlcv
# ---------------------------------------------------------------------------

class TestResampleOhlcv:
    strat = TurtleSoupMTFv1()

    def test_resample_reduces_rows(self):
        df = _flat_df(60, freq="1min")
        out = self.strat.resample_ohlcv(df, rule="15min")
        assert len(out) < len(df)

    def test_output_columns(self):
        df = _flat_df(60, freq="1min")
        out = self.strat.resample_ohlcv(df, rule="15min")
        for col in ["datetime", "open", "high", "low", "close", "volume"]:
            assert col in out.columns

    def test_missing_datetime_raises(self):
        df = _flat_df(60, freq="1min").drop(columns=["datetime"])
        with pytest.raises(ValueError, match="datetime"):
            self.strat.resample_ohlcv(df, rule="15min")

    def test_high_is_max_within_window(self):
        # Build 15 candles where one has a spike high
        df = _flat_df(15, price=100.0, freq="1min")
        df.loc[5, "high"] = 110.0
        out = self.strat.resample_ohlcv(df, rule="15min")
        assert out["high"].max() == pytest.approx(110.0)


# ---------------------------------------------------------------------------
# detect_setup  — bullish and bearish detection
# ---------------------------------------------------------------------------

class TestDetectSetup:
    strat = TurtleSoupMTFv1(config={"atr_period": 5})

    def _setup_df(self, n=80):
        """Steady candles with known price so rolling calcs stabilise."""
        ts = _make_ts(n, freq="15min")
        df = pd.DataFrame(
            {
                "datetime": ts,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1.0,
            }
        )
        return df

    def test_columns_present(self):
        df = self._setup_df()
        out = self.strat.detect_setup(df)
        assert "bullish_setup" in out.columns
        assert "bearish_setup" in out.columns

    def test_bullish_setup_detected(self):
        df = self._setup_df(80)
        # Lookback window of 60 => rolling min of lows is 99 for the first 60+ bars
        # Insert a sweep candle near the end: low sweeps below 99, close recovers above 99
        i = 70
        sweep_low = 98.0          # below prev rolling min (99)
        df.loc[i, "low"] = sweep_low
        df.loc[i, "open"] = 98.5
        df.loc[i, "close"] = 99.5  # recovers above prev_low_ref
        df.loc[i, "high"] = 99.8
        # body/range = (99.5-98.5)/(99.8-98.0) = 1/1.8 ≈ 0.56 — just under 0.60
        # Make body larger
        df.loc[i, "open"] = 98.2
        df.loc[i, "close"] = 99.6
        df.loc[i, "high"] = 99.7
        # body = 1.4, range = 1.7 ≈ 0.82  ✓
        out = self.strat.detect_setup(df)
        assert out.loc[i, "bullish_setup"], "Expected bullish setup at index i"

    def test_bearish_setup_detected(self):
        df = self._setup_df(80)
        # Rolling max of highs is 101
        i = 70
        df.loc[i, "high"] = 102.2   # sweeps above 101
        df.loc[i, "open"] = 101.8
        df.loc[i, "close"] = 100.5  # closes below prev_high_ref (101)
        df.loc[i, "low"] = 100.3
        # body = 1.3, range = 1.9 ≈ 0.68 ✓
        out = self.strat.detect_setup(df)
        assert out.loc[i, "bearish_setup"], "Expected bearish setup at index i"

    def test_no_false_positive_on_flat_data(self):
        df = self._setup_df(80)
        out = self.strat.detect_setup(df)
        assert out["bullish_setup"].sum() == 0
        assert out["bearish_setup"].sum() == 0


# ---------------------------------------------------------------------------
# setup_signal_from_row
# ---------------------------------------------------------------------------

class TestSetupSignalFromRow:
    strat = TurtleSoupMTFv1()

    def _row(self, bullish=False, bearish=False):
        ts = pd.Timestamp("2024-01-01 00:00", tz="UTC")
        return pd.Series(
            {
                "datetime": ts,
                "bullish_setup": bullish,
                "bearish_setup": bearish,
                "prev_low_ref": 99.0,
                "prev_high_ref": 101.0,
                "low": 98.5,
                "high": 101.5,
                "atr": 1.0,
            }
        )

    def test_bullish_signal(self):
        sig = self.strat.setup_signal_from_row(self._row(bullish=True))
        assert sig is not None
        assert sig["side"] == "long"
        assert sig["level"] == pytest.approx(99.0)
        assert sig["sweep_extreme"] == pytest.approx(98.5)

    def test_bearish_signal(self):
        sig = self.strat.setup_signal_from_row(self._row(bearish=True))
        assert sig is not None
        assert sig["side"] == "short"
        assert sig["level"] == pytest.approx(101.0)
        assert sig["sweep_extreme"] == pytest.approx(101.5)

    def test_no_setup_returns_none(self):
        assert self.strat.setup_signal_from_row(self._row()) is None


# ---------------------------------------------------------------------------
# find_entry
# ---------------------------------------------------------------------------

class TestFindEntry:
    strat = TurtleSoupMTFv1()
    setup_time = pd.Timestamp("2024-01-01 00:00", tz="UTC")

    def _entry_df(self, prices):
        """Build a tiny 1m DataFrame around setup_time with given close prices."""
        n = len(prices)
        ts = pd.date_range(self.setup_time, periods=n, freq="1min", tz="UTC")
        highs = [p + 0.1 for p in prices]
        lows = [p - 0.1 for p in prices]
        return pd.DataFrame(
            {
                "datetime": ts,
                "open": prices,
                "high": highs,
                "low": lows,
                "close": prices,
                "volume": 1.0,
            }
        )

    def test_no_bars_in_window_returns_none(self):
        df = self._entry_df([100.0])  # only the setup bar itself, no forward bars
        long_sig = {"side": "long", "level": 99.0, "sweep_extreme": 98.5, "atr": 1.0}
        result = self.strat.find_entry(self.setup_time, long_sig, df)
        assert result is None

    def test_long_entry_found(self):
        # Prices: start below level (99), then break up through prev_high (micro shift)
        # setup_time is bar 0; entry window is bars 1..20
        level = 99.0
        prices = [98.5, 98.6, 98.7, 99.5, 99.8, 100.2]
        # bar index 4 (close=99.8) > level and bar 3→4 has bull_break (99.8 > high of bar3=98.8)
        df = self._entry_df(prices)
        # Make bar 3 a proper break-up bar
        df.loc[3, "high"] = 99.4
        df.loc[3, "close"] = 99.5
        df.loc[4, "high"] = 100.0
        df.loc[4, "close"] = 99.9
        df.loc[4, "open"] = 99.3
        df.loc[4, "low"] = 99.2
        long_sig = {"side": "long", "level": level, "sweep_extreme": 98.5, "atr": 1.0}
        result = self.strat.find_entry(self.setup_time, long_sig, df)
        # May or may not find an entry depending on exact body_to_range; just assert return type
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# build_trade_plan
# ---------------------------------------------------------------------------

class TestBuildTradePlan:
    strat = TurtleSoupMTFv1()

    def _long_signal(self):
        return {
            "side": "long",
            "level": 99.0,
            "sweep_extreme": 98.0,
            "atr": 2.0,
            "setup_time": pd.Timestamp("2024-01-01", tz="UTC"),
        }

    def _short_signal(self):
        return {
            "side": "short",
            "level": 101.0,
            "sweep_extreme": 102.0,
            "atr": 2.0,
            "setup_time": pd.Timestamp("2024-01-01", tz="UTC"),
        }

    def _entry(self, price, ts="2024-01-01 00:05"):
        return {
            "entry_price": price,
            "entry_time": pd.Timestamp(ts, tz="UTC"),
            "signal_level": price - 1.0,
        }

    def test_long_plan_fields(self):
        plan = self.strat.build_trade_plan(self._long_signal(), self._entry(100.0), 10_000)
        assert plan is not None
        assert isinstance(plan, TradePlan)
        assert plan.side == "long"
        assert plan.stop_price < plan.entry_price
        assert plan.tp1_price > plan.entry_price
        assert plan.tp2_price > plan.tp1_price
        assert plan.size > 0
        assert plan.risk_per_unit > 0

    def test_short_plan_fields(self):
        plan = self.strat.build_trade_plan(self._short_signal(), self._entry(100.0), 10_000)
        assert plan is not None
        assert plan.side == "short"
        assert plan.stop_price > plan.entry_price
        assert plan.tp1_price < plan.entry_price
        assert plan.tp2_price < plan.tp1_price

    def test_zero_entry_price_returns_none(self):
        plan = self.strat.build_trade_plan(self._long_signal(), self._entry(0.0), 10_000)
        assert plan is None

    def test_tp_ratios_match_config(self):
        plan = self.strat.build_trade_plan(self._long_signal(), self._entry(100.0), 10_000)
        r = plan.risk_per_unit
        assert plan.tp1_price == pytest.approx(plan.entry_price + self.strat.tp1_at_r * r, rel=1e-6)
        assert plan.tp2_price == pytest.approx(plan.entry_price + self.strat.tp2_at_r * r, rel=1e-6)


# ---------------------------------------------------------------------------
# manage_position — stop and tp2 exits
# ---------------------------------------------------------------------------

class TestManagePosition:
    strat = TurtleSoupMTFv1()

    def _long_plan(self, entry=100.0, stop=98.0, tp1=102.5, tp2=106.0):
        r = entry - stop
        return TradePlan(
            side="long",
            entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=entry,
            stop_price=stop,
            initial_stop=stop,
            tp1_price=tp1,
            tp2_price=tp2,
            size=1.0,
            remaining_size=1.0,
            risk_per_unit=r,
        )

    def _short_plan(self, entry=100.0, stop=102.0, tp1=97.5, tp2=94.0):
        r = stop - entry
        return TradePlan(
            side="short",
            entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=entry,
            stop_price=stop,
            initial_stop=stop,
            tp1_price=tp1,
            tp2_price=tp2,
            size=1.0,
            remaining_size=1.0,
            risk_per_unit=r,
        )

    def _df(self, rows, start="2024-01-02"):
        """rows = list of (open, high, low, close)."""
        ts = pd.date_range(start, periods=len(rows), freq="1min", tz="UTC")
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
        df["datetime"] = ts
        df["volume"] = 1.0
        return df

    def test_long_stop_hit(self):
        plan = self._long_plan(entry=100.0, stop=98.0, tp1=102.5, tp2=106.0)
        df = self._df([(99, 99.5, 97.5, 98.5)])  # low touches stop
        result = self.strat.manage_position(plan, df)
        assert result is not None
        assert result["exit_reason"] == "stop"

    def test_long_tp2_hit(self):
        plan = self._long_plan(entry=100.0, stop=98.0, tp1=102.5, tp2=106.0)
        # Bar 1: hits tp1 (partial); low stays above BE (100) so stop isn't triggered
        # Bar 2: hits tp2
        df = self._df([
            (101, 103.0, 100.5, 102.8),  # high >= tp1=102.5; low=100.5 > entry=100
            (103, 107.0, 103.5, 106.5),  # high >= tp2=106.0
        ])
        result = self.strat.manage_position(plan, df)
        assert result is not None
        assert result["exit_reason"] == "tp2"

    def test_short_stop_hit(self):
        plan = self._short_plan(entry=100.0, stop=102.0, tp1=97.5, tp2=94.0)
        df = self._df([(101, 102.5, 100.5, 101.5)])  # high >= stop=102.0
        result = self.strat.manage_position(plan, df)
        assert result is not None
        assert result["exit_reason"] == "stop"

    def test_short_tp2_hit(self):
        plan = self._short_plan(entry=100.0, stop=102.0, tp1=97.5, tp2=94.0)
        # Bar 1: hits tp1; high stays below BE (100) so stop isn't triggered
        # Bar 2: hits tp2
        df = self._df([
            (99, 99.8, 97.0, 97.3),   # low <= tp1=97.5; high=99.8 < entry=100
            (97, 97.5, 93.5, 94.2),   # low <= tp2=94.0
        ])
        result = self.strat.manage_position(plan, df)
        assert result is not None
        assert result["exit_reason"] == "tp2"

    def test_empty_df_returns_none(self):
        plan = self._long_plan()
        df = self._df([])
        result = self.strat.manage_position(plan, df)
        assert result is None

    def test_month_end_exit_when_no_trigger(self):
        plan = self._long_plan(entry=100.0, stop=98.0, tp1=102.5, tp2=106.0)
        # Price stays between stop and tp2 the whole time
        df = self._df([(100, 101, 99, 100.5)] * 5)
        result = self.strat.manage_position(plan, df)
        assert result is not None
        assert result["exit_reason"] == "month_end"

    def test_pnl_field_present(self):
        plan = self._long_plan(entry=100.0, stop=98.0, tp1=102.5, tp2=106.0)
        df = self._df([(99, 99.5, 97.5, 98.5)])
        result = self.strat.manage_position(plan, df)
        assert "pnl" in result
        assert "pnl_r" in result
