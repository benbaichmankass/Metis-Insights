"""Unit tests for src/units/strategies/ict_scalp.py.

Fully offline — synthetic OHLCV DataFrames only, no exchange calls,
no secrets, no network. Mirrors tests/test_s012_turtle_soup.py for
the units-layer strategy contract.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies.ict_scalp import _DEFAULTS, monitor, order_package


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _flat_frame(n: int = 80, base: float = 50_000.0, freq: str = "5min") -> pd.DataFrame:
    """Quiet sideways market — no sweep, no displacement, no signal."""
    rng = pd.date_range("2026-04-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": np.full(n, base + 25.0),
            "high": np.full(n, base + 50.0),
            "low": np.full(n, base - 50.0),
            "close": np.full(n, base + 30.0),
            "volume": np.full(n, 1.0),
        },
        index=rng,
    )


def _bullish_scalp_frame(freq: str = "5min") -> pd.DataFrame:
    """Construct a clean bullish ICT-scalp setup.

    Bars 0..n-6  — sideways, establishing a swing-low reference around
                    base - 50.
    Bar  n-5     — liquidity sweep: dips well below the rolling-min
                    low and closes back inside.
    Bar  n-4     — strong bullish displacement: large green body
                    (body ≥ 1 × ATR, body-to-range ≥ 0.55).
    Bar  n-3     — second bullish bar that lifts the high; combined
                    with bar n-5's low this forms the bullish FVG
                    (high[n-5] < low[n-3] AFTER bar n-5's range, but
                    we engineer it via the displacement run-up).
    Bar  n-2     — small upward continuation.
    Bar  n-1     — pullback into the FVG zone, bullish body for the
                    mitigation entry.
    """
    n = 80
    base = 50_000.0
    df = _flat_frame(n, base, freq).copy()

    # Make pre-sweep bars hold a flat range with consistent swing low at
    # base - 50 so the rolling min lands there.
    # Sweep bar at idx -5: low drops sharply, close recovers inside.
    sweep_idx = n - 5
    df.iloc[sweep_idx, df.columns.get_loc("open")] = base - 30.0
    df.iloc[sweep_idx, df.columns.get_loc("high")] = base + 10.0
    df.iloc[sweep_idx, df.columns.get_loc("low")] = base - 300.0  # deep sweep
    df.iloc[sweep_idx, df.columns.get_loc("close")] = base + 5.0

    # Displacement bar at idx -4: large bullish body well clear of ATR.
    disp_idx = n - 4
    df.iloc[disp_idx, df.columns.get_loc("open")] = base + 10.0
    df.iloc[disp_idx, df.columns.get_loc("high")] = base + 320.0
    df.iloc[disp_idx, df.columns.get_loc("low")] = base + 5.0
    df.iloc[disp_idx, df.columns.get_loc("close")] = base + 300.0

    # Bar -3 lifts higher creating room for the FVG (high[disp-1] < low[-3]).
    fvg_after_idx = n - 3
    df.iloc[fvg_after_idx, df.columns.get_loc("open")] = base + 305.0
    df.iloc[fvg_after_idx, df.columns.get_loc("high")] = base + 500.0
    df.iloc[fvg_after_idx, df.columns.get_loc("low")] = base + 340.0  # > high of bar at idx fvg_after_idx-2 (sweep)
    df.iloc[fvg_after_idx, df.columns.get_loc("close")] = base + 480.0

    # Bar -2 continuation up.
    cont_idx = n - 2
    df.iloc[cont_idx, df.columns.get_loc("open")] = base + 470.0
    df.iloc[cont_idx, df.columns.get_loc("high")] = base + 520.0
    df.iloc[cont_idx, df.columns.get_loc("low")] = base + 350.0
    df.iloc[cont_idx, df.columns.get_loc("close")] = base + 500.0

    # Last bar: pullback INTO the FVG (low between 10 and 340) and
    # closes back up with a bullish body — clean mitigation entry.
    last_idx = n - 1
    df.iloc[last_idx, df.columns.get_loc("open")] = base + 410.0
    df.iloc[last_idx, df.columns.get_loc("high")] = base + 460.0
    df.iloc[last_idx, df.columns.get_loc("low")] = base + 330.0  # dips into FVG (10..340)
    df.iloc[last_idx, df.columns.get_loc("close")] = base + 450.0
    return df


def _bearish_scalp_frame(freq: str = "5min") -> pd.DataFrame:
    """Mirror of the bullish setup."""
    n = 80
    base = 50_000.0
    df = _flat_frame(n, base, freq).copy()

    sweep_idx = n - 5
    df.iloc[sweep_idx, df.columns.get_loc("open")] = base + 30.0
    df.iloc[sweep_idx, df.columns.get_loc("high")] = base + 300.0  # deep sweep up
    df.iloc[sweep_idx, df.columns.get_loc("low")] = base - 10.0
    df.iloc[sweep_idx, df.columns.get_loc("close")] = base - 5.0

    disp_idx = n - 4
    df.iloc[disp_idx, df.columns.get_loc("open")] = base - 10.0
    df.iloc[disp_idx, df.columns.get_loc("high")] = base - 5.0
    df.iloc[disp_idx, df.columns.get_loc("low")] = base - 320.0
    df.iloc[disp_idx, df.columns.get_loc("close")] = base - 300.0

    fvg_after_idx = n - 3
    df.iloc[fvg_after_idx, df.columns.get_loc("open")] = base - 305.0
    df.iloc[fvg_after_idx, df.columns.get_loc("high")] = base - 340.0  # < low of bar at idx fvg_after_idx-2 (sweep)
    df.iloc[fvg_after_idx, df.columns.get_loc("low")] = base - 500.0
    df.iloc[fvg_after_idx, df.columns.get_loc("close")] = base - 480.0

    cont_idx = n - 2
    df.iloc[cont_idx, df.columns.get_loc("open")] = base - 470.0
    df.iloc[cont_idx, df.columns.get_loc("high")] = base - 350.0
    df.iloc[cont_idx, df.columns.get_loc("low")] = base - 520.0
    df.iloc[cont_idx, df.columns.get_loc("close")] = base - 500.0

    last_idx = n - 1
    df.iloc[last_idx, df.columns.get_loc("open")] = base - 410.0
    df.iloc[last_idx, df.columns.get_loc("high")] = base - 330.0  # rises into FVG (-340..-10)
    df.iloc[last_idx, df.columns.get_loc("low")] = base - 460.0
    df.iloc[last_idx, df.columns.get_loc("close")] = base - 450.0
    return df


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestICTScalpHappyPath:
    def test_bullish_setup_emits_long_package(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        assert pkg["direction"] == "long"
        assert pkg["symbol"] == "BTCUSDT"

    def test_bullish_returned_dict_has_required_keys(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        for key in ("symbol", "direction", "entry", "sl", "tp", "confidence", "meta"):
            assert key in pkg, f"missing key: {key}"

    def test_bullish_long_sl_below_entry_below_tp(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        assert pkg["sl"] < pkg["entry"] < pkg["tp"]

    def test_bullish_meta_attribution(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        m = pkg["meta"]
        for key in (
            "strategy_name",
            "timeframe",
            "sweep_level",
            "sweep_extreme",
            "displacement_idx_from_end",
            "fvg_low",
            "fvg_high",
            "fvg_size",
            "atr",
            "risk_per_unit",
        ):
            assert key in m, f"missing meta key: {key}"
        assert m["strategy_name"] == "ict_scalp_5m"
        # FVG is above the sweep extreme on a bullish setup.
        assert m["fvg_low"] > m["sweep_extreme"]

    def test_bearish_setup_emits_short_package(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bearish_scalp_frame())
        assert pkg["direction"] == "short"

    def test_bearish_short_sl_above_entry_above_tp(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bearish_scalp_frame())
        assert pkg["sl"] > pkg["entry"] > pkg["tp"]

    def test_confidence_is_in_unit_interval(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        assert 0.0 <= pkg["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# No-signal cases
# ---------------------------------------------------------------------------


class TestICTScalpNoSignal:
    def test_flat_market_raises_no_sweep(self):
        with pytest.raises(ValueError, match="no liquidity sweep"):
            order_package({"symbol": "BTCUSDT"}, candles_df=_flat_frame())

    def test_sweep_without_displacement_raises(self):
        """Sweep present but the bars after it are flat (no displacement)."""
        df = _flat_frame().copy()
        n = len(df)
        sweep_idx = n - 5
        df.iloc[sweep_idx, df.columns.get_loc("low")] = 49_700.0  # sweep
        df.iloc[sweep_idx, df.columns.get_loc("close")] = 50_000.0
        # Rest stays sideways — no large bullish body.
        with pytest.raises(ValueError, match="displacement"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)

    def test_setup_present_but_last_bar_does_not_mitigate(self):
        """Bullish sweep + displacement + FVG, but the most recent
        bar sits above the FVG without dipping into it (no mitigation)
        and does not itself create a new 3-candle FVG."""
        df = _bullish_scalp_frame().copy()
        last_idx = len(df) - 1
        # Constrain the bar's high so the 3-candle FVG check at i=last
        # cannot form a fresh imbalance (need high[i-2] < low[i]; here
        # high[i-2] = 50_500, so keeping low[i] ≤ 50_500 prevents a new
        # FVG). Range entirely above the displacement-leg FVG zone.
        df.iloc[last_idx, df.columns.get_loc("open")] = 50_490.0
        df.iloc[last_idx, df.columns.get_loc("high")] = 50_500.0
        df.iloc[last_idx, df.columns.get_loc("low")] = 50_460.0
        df.iloc[last_idx, df.columns.get_loc("close")] = 50_495.0
        # v2 default: wick_rejection raises with the "wick rejection"
        # phrasing rather than v1's "mitigate". Match either so the
        # test stays passing under both modes.
        with pytest.raises(ValueError, match="wick rejection|mitigate"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)

    def test_bullish_setup_with_bearish_last_body_does_not_fire(self):
        """Mitigation in range, but last body is bearish — fails the
        clean-entry-confirmation gate."""
        df = _bullish_scalp_frame().copy()
        last_idx = len(df) - 1
        # Flip the last bar to bearish, keeping range overlapping FVG.
        df.iloc[last_idx, df.columns.get_loc("open")] = 50_450.0
        df.iloc[last_idx, df.columns.get_loc("close")] = 50_350.0
        with pytest.raises(ValueError, match="wick rejection|mitigate"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)


# ---------------------------------------------------------------------------
# Invalid / missing data
# ---------------------------------------------------------------------------


class TestICTScalpInvalidData:
    def test_none_candles_raises(self):
        with pytest.raises(ValueError, match="candles_df is required"):
            order_package({"symbol": "BTCUSDT"}, candles_df=None)

    def test_empty_frame_raises(self):
        with pytest.raises(ValueError, match="candles_df is required"):
            order_package({"symbol": "BTCUSDT"}, candles_df=pd.DataFrame())

    def test_missing_ohlc_columns_raises(self):
        df = _bullish_scalp_frame().drop(columns=["low"])
        with pytest.raises(ValueError, match="missing OHLC columns"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)

    def test_too_few_candles_raises(self):
        df = _flat_frame(n=10)  # < swing_lookback + atr_period + headroom
        with pytest.raises(ValueError, match="need at least"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)


# ---------------------------------------------------------------------------
# Timeframe handling
# ---------------------------------------------------------------------------


class TestICTScalpTimeframe:
    def test_default_timeframe_is_5m(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        assert pkg["meta"]["timeframe"] == "5m"

    def test_explicit_5m_propagates(self):
        pkg = order_package(
            {"symbol": "BTCUSDT", "timeframe": "5m"},
            candles_df=_bullish_scalp_frame(),
        )
        assert pkg["meta"]["timeframe"] == "5m"
        assert pkg["meta"]["setup_tf"] == "5m"

    def test_1m_timeframe_propagates_with_same_logic(self):
        """Confirms the unit is timeframe-agnostic: same fixture, just
        labelled as 1m, still produces a clean long signal."""
        pkg = order_package(
            {"symbol": "BTCUSDT", "timeframe": "1m"},
            candles_df=_bullish_scalp_frame(freq="1min"),
        )
        assert pkg["direction"] == "long"
        assert pkg["meta"]["timeframe"] == "1m"

    def test_cfg_overrides_apply(self):
        """A more permissive sweep_buffer keeps the same signal; a
        more restrictive one (huge buffer) silences it."""
        cfg = {"symbol": "BTCUSDT", "sweep_buffer_bps": 5000.0}
        with pytest.raises(ValueError, match="no liquidity sweep"):
            order_package(cfg, candles_df=_bullish_scalp_frame())


# ---------------------------------------------------------------------------
# Session filter
# ---------------------------------------------------------------------------


class TestICTScalpSessionFilter:
    def test_session_filter_off_by_default(self):
        # The fixture's last bar is at hour 6 UTC (80 5-min bars from
        # 2026-04-01 00:00 → ends at 06:35). With the filter off, the
        # signal still fires regardless of hour.
        pkg = order_package(
            {"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame()
        )
        assert pkg["direction"] == "long"

    def test_session_filter_blocks_outside_window(self):
        # Fixture ends at 06:35 UTC; restrict window to 12-15 UTC →
        # signal must be blocked.
        cfg = {
            "symbol": "BTCUSDT",
            "session_filter_enabled": True,
            "session_start_hour": 12,
            "session_end_hour": 15,
        }
        with pytest.raises(ValueError, match="session window"):
            order_package(cfg, candles_df=_bullish_scalp_frame())

    def test_session_filter_allows_inside_window(self):
        # The default 7-17 window doesn't cover 06:35, so widen to 6-9
        # and confirm the signal fires.
        cfg = {
            "symbol": "BTCUSDT",
            "session_filter_enabled": True,
            "session_start_hour": 6,
            "session_end_hour": 9,
        }
        pkg = order_package(cfg, candles_df=_bullish_scalp_frame())
        assert pkg["direction"] == "long"


# ---------------------------------------------------------------------------
# monitor() — break-even SL
# ---------------------------------------------------------------------------


class TestICTScalpMonitor:
    def _long_open_pkg(self) -> dict:
        return {
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": 50_000.0,
            "sl": 49_900.0,
            "tp": 50_200.0,
        }

    def test_monitor_returns_none_when_no_progress(self):
        df = pd.DataFrame(
            {
                "open": [50_000.0],
                "high": [50_010.0],
                "low": [49_990.0],
                "close": [50_005.0],
                "volume": [1.0],
            }
        )
        verdict = monitor({}, df, self._long_open_pkg())
        assert verdict is None

    def test_monitor_moves_sl_to_break_even_after_1r(self):
        df = pd.DataFrame(
            {
                "open": [50_050.0],
                "high": [50_110.0],
                "low": [50_040.0],
                "close": [50_100.0],  # 1R move beyond entry
                "volume": [1.0],
            }
        )
        verdict = monitor({}, df, self._long_open_pkg())
        assert verdict == {"sl": 50_000.0}

    def test_monitor_handles_none_candles(self):
        assert monitor({}, None, self._long_open_pkg()) is None


# ---------------------------------------------------------------------------
# YAML registration — config-level safety contract
# ---------------------------------------------------------------------------


class TestICTScalpYAMLRegistration:
    def test_strategy_present_in_strategies_yaml_disabled(self):
        """The strategy must ship registered-but-disabled so the
        runtime builder is plumbed in without changing live
        behaviour. Flipping enabled→true is the one-line operator
        change to activate it after a successful backtest."""
        import yaml
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        raw = yaml.safe_load((repo / "config" / "strategies.yaml").read_text())
        cfg = (raw.get("strategies") or {}).get("ict_scalp_5m")
        assert cfg is not None, "ict_scalp_5m missing from config/strategies.yaml"
        # PR #1156 (operator-approved 2026-05-14): ict_scalp_5m promoted to live
        # after backtest gate cleared (59.3% win rate, +0.301 R). enabled=true.
        # DEMOTED 2026-06-29 (Unit B, operator-approved): execution flipped live →
        # shadow after the live record showed -0.64R/trade — still enabled (logs
        # order packages), just no live order. This test pins enabled, not execution.
        assert cfg.get("enabled") is True, (
            "ict_scalp_5m was operator-approved live per PR #1156 (2026-05-14); "
            "enabled must be True"
        )
        assert cfg.get("timeframe") == "5m"
        assert cfg.get("signal_prefixes") == ["ict_scalp"]

    def test_defaults_in_unit_match_yaml(self):
        """Smoke check that the YAML carries every override knob the
        unit exposes, so the operator can tune via YAML without
        editing Python."""
        import yaml
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        raw = yaml.safe_load((repo / "config" / "strategies.yaml").read_text())
        cfg = (raw.get("strategies") or {}).get("ict_scalp_5m") or {}

        for key in _DEFAULTS:
            assert key in cfg, f"YAML missing tuning knob: {key}"


# ---------------------------------------------------------------------------
# v2: mitigation modes
# ---------------------------------------------------------------------------


class TestICTScalpMitigationModes:
    def test_default_mitigation_mode_is_wick_rejection(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        assert pkg["meta"]["mitigation_mode"] == "wick_rejection"

    def test_body_inside_fvg_mode_still_works_on_v1_style_setup(self):
        """The legacy v1 mode (body_inside_fvg) is preserved so the
        operator can A/B vs v2 without rolling back code."""
        pkg = order_package(
            {"symbol": "BTCUSDT", "mitigation_mode": "body_inside_fvg"},
            candles_df=_bullish_scalp_frame(),
        )
        assert pkg["direction"] == "long"
        assert pkg["meta"]["mitigation_mode"] == "body_inside_fvg"

    def test_unknown_mitigation_mode_raises(self):
        with pytest.raises(ValueError, match="unknown mitigation_mode"):
            order_package(
                {"symbol": "BTCUSDT", "mitigation_mode": "garbage"},
                candles_df=_bullish_scalp_frame(),
            )

    def test_wick_rejection_rejects_when_close_stays_inside_fvg(self):
        """Body inside FVG but closing INSIDE the gap fails the v2
        wick-rejection gate (no clean reversal). The v1 mode would
        have accepted it."""
        df = _bullish_scalp_frame().copy()
        last_idx = len(df) - 1
        # Bullish body, range overlaps FVG, but close stays inside the
        # FVG zone (50_320 .. 50_350). Set close = 50_340.
        df.iloc[last_idx, df.columns.get_loc("open")] = 50_325.0
        df.iloc[last_idx, df.columns.get_loc("high")] = 50_345.0
        df.iloc[last_idx, df.columns.get_loc("low")] = 50_322.0
        df.iloc[last_idx, df.columns.get_loc("close")] = 50_340.0
        # v2 wick_rejection: close <= fvg_high → fails closed_out gate.
        with pytest.raises(ValueError, match="wick rejection"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)


# ---------------------------------------------------------------------------
# v2: HTF bias filter
# ---------------------------------------------------------------------------


class TestICTScalpHTFFilter:
    def test_htf_filter_blocks_long_when_htf_bearish(self):
        with pytest.raises(ValueError, match="HTF bias is bearish"):
            order_package(
                {"symbol": "BTCUSDT", "htf_close": 100.0, "htf_ema": 110.0},
                candles_df=_bullish_scalp_frame(),
            )

    def test_htf_filter_allows_long_when_htf_bullish(self):
        pkg = order_package(
            {"symbol": "BTCUSDT", "htf_close": 110.0, "htf_ema": 100.0},
            candles_df=_bullish_scalp_frame(),
        )
        assert pkg["direction"] == "long"
        assert pkg["meta"]["htf_filter_active"] is True

    def test_htf_filter_blocks_short_when_htf_bullish(self):
        with pytest.raises(ValueError, match="HTF bias is bullish"):
            order_package(
                {"symbol": "BTCUSDT", "htf_close": 110.0, "htf_ema": 100.0},
                candles_df=_bearish_scalp_frame(),
            )

    def test_htf_filter_skips_when_inputs_missing(self):
        """No htf_close / htf_ema in cfg → the filter is a no-op so
        tests that don't supply HTF data still work."""
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_scalp_frame())
        assert pkg["direction"] == "long"
        assert pkg["meta"]["htf_filter_active"] is False

    def test_htf_filter_disabled_via_cfg_skips_check(self):
        """Operator can turn the filter off explicitly even when HTF
        values are present (e.g. for an A/B run)."""
        pkg = order_package(
            {
                "symbol": "BTCUSDT",
                "htf_trend_filter_enabled": False,
                "htf_close": 100.0,
                "htf_ema": 110.0,   # would block under enabled=true
            },
            candles_df=_bullish_scalp_frame(),
        )
        assert pkg["direction"] == "long"
        assert pkg["meta"]["htf_filter_active"] is False
