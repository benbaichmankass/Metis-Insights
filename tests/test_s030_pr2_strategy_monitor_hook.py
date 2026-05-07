"""S-030 PR2 regression tests — strategy ``monitor()`` hook contract.

Per CLAUDE.md § Architecture rules § 2 + architecture-audit-2026-05-02
P1-4: every strategy unit exposes a ``monitor(cfg, candles_df, open_pkg)``
function that returns ``None`` (no change), or a dict describing the
update (``{"sl": float}``, ``{"tp": float}``, or
``{"action": "close", "reason": str}``).

PR2 ships the contract + a v1 break-even-SL-after-1R rule shared via
``_base.monitor_breakeven_sl``. PR3 (next) builds the heartbeat-driven
loop that consumes the contract, calls ``Database.update_order_package``
on changes, and routes close/modify decisions to the account unit.
"""
from __future__ import annotations

import pandas as pd

from src.units.strategies._base import monitor_breakeven_sl


def _candles(*closes):
    """Build a minimal OHLCV DataFrame with the given close-price tail."""
    return pd.DataFrame({
        "open": closes,
        "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes],
        "close": list(closes),
        "volume": [100.0] * len(closes),
    })


# ---------------------------------------------------------------------------
# _base.monitor_breakeven_sl — the shared rule
# ---------------------------------------------------------------------------


class TestMonitorBreakevenSL:
    """Long: entry 100, sl 98, tp 104. 1R = $2."""

    LONG_PKG = {
        "entry": 100.0, "sl": 98.0, "tp": 104.0, "direction": "long",
        "symbol": "BTCUSDT",
    }
    SHORT_PKG = {
        "entry": 100.0, "sl": 102.0, "tp": 96.0, "direction": "short",
        "symbol": "BTCUSDT",
    }

    def test_long_below_1r_returns_none(self):
        """Price between entry and entry+1R: no change."""
        df = _candles(100.5, 101.0, 101.5)  # < 102
        assert monitor_breakeven_sl(self.LONG_PKG, df) is None

    def test_long_at_1r_moves_sl_to_breakeven(self):
        """Price exactly at entry+1R: move SL to entry."""
        df = _candles(101.0, 101.5, 102.0)
        result = monitor_breakeven_sl(self.LONG_PKG, df)
        assert result == {"sl": 100.0}

    def test_long_beyond_1r_moves_sl_to_breakeven(self):
        """Price well past entry+1R: move SL to entry."""
        df = _candles(101.0, 102.0, 103.5)
        result = monitor_breakeven_sl(self.LONG_PKG, df)
        assert result == {"sl": 100.0}

    def test_long_already_at_breakeven_returns_none(self):
        """SL already at entry: monitor must not re-write the same value."""
        pkg = {**self.LONG_PKG, "sl": 100.0}
        df = _candles(103.0, 103.5)
        assert monitor_breakeven_sl(pkg, df) is None

    def test_long_below_entry_returns_none(self):
        """Drawdown — price below entry: original SL still holds."""
        df = _candles(99.0, 98.5)
        assert monitor_breakeven_sl(self.LONG_PKG, df) is None

    def test_short_below_1r_returns_none(self):
        df = _candles(99.5, 99.0, 98.5)  # > 98
        assert monitor_breakeven_sl(self.SHORT_PKG, df) is None

    def test_short_at_1r_moves_sl_to_breakeven(self):
        df = _candles(99.0, 98.5, 98.0)
        result = monitor_breakeven_sl(self.SHORT_PKG, df)
        assert result == {"sl": 100.0}

    def test_short_already_at_breakeven_returns_none(self):
        pkg = {**self.SHORT_PKG, "sl": 100.0}
        df = _candles(97.0, 96.5)
        assert monitor_breakeven_sl(pkg, df) is None

    def test_custom_threshold_2r(self):
        """one_r_threshold=2.0 means SL moves only after 2R."""
        df = _candles(101.0, 102.5)  # 2.5 above entry but < 2R (=$4)
        assert monitor_breakeven_sl(self.LONG_PKG, df, one_r_threshold=2.0) is None
        df2 = _candles(101.0, 104.0)  # = 2R
        assert monitor_breakeven_sl(self.LONG_PKG, df2, one_r_threshold=2.0) == {"sl": 100.0}


class TestMonitorBreakevenSLDefensive:
    """The monitor must never raise — bad inputs return None."""

    def test_empty_dataframe_returns_none(self):
        df = pd.DataFrame()
        pkg = {"entry": 100.0, "sl": 98.0, "tp": 104.0, "direction": "long"}
        assert monitor_breakeven_sl(pkg, df) is None

    def test_none_dataframe_returns_none(self):
        pkg = {"entry": 100.0, "sl": 98.0, "tp": 104.0, "direction": "long"}
        assert monitor_breakeven_sl(pkg, None) is None

    def test_missing_close_column_returns_none(self):
        df = pd.DataFrame({"open": [100.0]})  # no close
        pkg = {"entry": 100.0, "sl": 98.0, "tp": 104.0, "direction": "long"}
        assert monitor_breakeven_sl(pkg, df) is None

    def test_missing_pkg_keys_returns_none(self):
        df = _candles(102.0)
        # Missing direction
        assert monitor_breakeven_sl({"entry": 100.0, "sl": 98.0}, df) is None

    def test_zero_risk_distance_returns_none(self):
        """entry == sl is invalid (no risk); monitor must not divide-by-zero."""
        pkg = {"entry": 100.0, "sl": 100.0, "tp": 104.0, "direction": "long"}
        df = _candles(150.0)
        assert monitor_breakeven_sl(pkg, df) is None

    def test_unknown_direction_returns_none(self):
        pkg = {"entry": 100.0, "sl": 98.0, "tp": 104.0, "direction": "neutral"}
        df = _candles(102.0)
        assert monitor_breakeven_sl(pkg, df) is None


# ---------------------------------------------------------------------------
# Strategy-level monitor() — must exist + delegate to the shared helper
# ---------------------------------------------------------------------------


class TestVwapMonitor:
    """S-047 T4 (2026-05-07): VWAP's monitor() no longer follows the
    shared break-even-after-1R rule — it implements four close paths
    (TP / SL / VWAP-cross / time-decay) plus no-action. The exhaustive
    contract is pinned in
    ``tests/units/strategies/test_vwap_monitor_close.py``; this class
    keeps a single signature smoke test so the S-030 PR2 hook
    contract (a ``monitor()`` function exists) is still asserted here.
    turtle_soup still delegates to ``monitor_breakeven_sl`` — see
    ``TestTurtleSoupMonitor`` below.
    """

    def test_signature_matches_contract(self):
        from src.units.strategies import vwap
        assert callable(vwap.monitor)


class TestTurtleSoupMonitor:
    def test_signature_matches_contract(self):
        from src.units.strategies import turtle_soup
        assert callable(turtle_soup.monitor)

    def test_no_change_returns_none(self):
        from src.units.strategies import turtle_soup
        pkg = {"entry": 100.0, "sl": 98.0, "tp": 104.0, "direction": "long"}
        df = _candles(100.5, 101.0)
        assert turtle_soup.monitor({}, df, pkg) is None

    def test_short_one_r_reached_returns_breakeven(self):
        from src.units.strategies import turtle_soup
        pkg = {"entry": 100.0, "sl": 102.0, "tp": 96.0, "direction": "short"}
        df = _candles(99.0, 98.0)
        assert turtle_soup.monitor({}, df, pkg) == {"sl": 100.0}
