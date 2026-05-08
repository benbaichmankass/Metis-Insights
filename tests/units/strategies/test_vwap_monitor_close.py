"""S-047 T4 — VWAP ``monitor()`` close-path tests.

Per `docs/sprint-plans/S-047-bybit2-spot-margin.md` § 6, T4 ships a
rewrite of `vwap.monitor()` that replaces the v1 break-even-only stub
with four close paths plus the no-action path. This file pins the
five-path contract.

Architecture note: the strategy unit's `monitor()` is a pure verdict
producer (CLAUDE.md § Architecture rules § 2). It returns
``{"action": "close", "reason": str, "exit_price": float}`` for the
close paths and ``None`` for no-action. The runtime layer
(``src/runtime/order_monitor.py::_apply_update``) is what translates
the verdict into a reduce-only ``close_open_position`` call against
the linked trade row's ``account_id`` + ``position_size`` —
package-level data the strategy never sees. Tests therefore exercise
the strategy contract directly with synthetic candles; no live
exchange contact is required and no DB or exchange client is mocked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from src.units.strategies import vwap


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _candles_with_close(closes, *, volume: float = 100.0) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame whose closing prices are *closes*.

    The high / low envelope hugs each close (±0.1 %) so the typical
    price is dominated by the close, which keeps the live VWAP close
    to the mean of *closes* — easy to reason about in tests.
    """
    return pd.DataFrame({
        "open": list(closes),
        "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes],
        "close": list(closes),
        "volume": [volume] * len(closes),
    })


def _pkg(
    *,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    created_at: Optional[str] = None,
    symbol: str = "BTCUSDT",
) -> dict:
    """Build an order_packages-shape dict with the keys monitor() reads."""
    if created_at is None:
        # Default: just-opened, well within any sensible time-decay window.
        created_at = datetime.now(timezone.utc).isoformat()
    return {
        "order_package_id": "pkg-test",
        "strategy_name": "vwap",
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "created_at": created_at,
        "status": "open",
    }


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# 1. TP-cross close
# ---------------------------------------------------------------------------


class TestTpCrossClose:
    """Long: entry 100, sl 99.5, tp 102. Short mirrors it."""

    def test_long_close_above_tp_returns_close_verdict(self):
        # 100, 101, 102.5 — last close exceeds tp (102).
        df = _candles_with_close([100.0, 101.0, 102.5])
        pkg = _pkg(direction="long", entry=100.0, sl=99.5, tp=102.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["action"] == "close"
        assert verdict["reason"] == "tp_cross"
        assert verdict["exit_price"] == 102.5

    def test_long_close_exactly_at_tp_returns_close(self):
        df = _candles_with_close([100.0, 101.0, 102.0])
        pkg = _pkg(direction="long", entry=100.0, sl=99.5, tp=102.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "tp_cross"

    def test_short_close_below_tp_returns_close_verdict(self):
        # tp = 98 for a short opened at 100. Last close below tp.
        df = _candles_with_close([100.0, 99.0, 97.5])
        pkg = _pkg(direction="short", entry=100.0, sl=100.5, tp=98.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "tp_cross"
        assert verdict["exit_price"] == 97.5


# ---------------------------------------------------------------------------
# 2. SL-cross close
# ---------------------------------------------------------------------------


class TestSlCrossClose:
    def test_long_close_at_sl_returns_close_verdict(self):
        # Long entry 100, sl 99. Last close = sl.
        df = _candles_with_close([100.0, 99.5, 99.0])
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=102.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "sl_cross"
        assert verdict["exit_price"] == 99.0

    def test_long_close_below_sl_returns_close_verdict(self):
        df = _candles_with_close([100.0, 99.5, 98.5])
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=102.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "sl_cross"

    def test_short_close_above_sl_returns_close_verdict(self):
        # Short entry 100, sl 101. Last close above sl.
        df = _candles_with_close([100.0, 100.5, 101.5])
        pkg = _pkg(direction="short", entry=100.0, sl=101.0, tp=98.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "sl_cross"


# ---------------------------------------------------------------------------
# 3. VWAP-cross close
# ---------------------------------------------------------------------------


class TestVwapCrossClose:
    """The structural mean-reversion invariant.

    The trade was opened because price had deviated from VWAP. Once
    price crosses back through the live VWAP line, the original thesis
    has played out — close.

    Tests construct candle frames whose live VWAP differs from the
    package's *tp* (a TP-cross would otherwise short-circuit ahead of
    VWAP-cross and the verdict would carry the wrong reason). All
    candles use the same volume so live VWAP is the volume-weighted
    mean of the typical prices — each candle's typical price ≈ close.
    """

    def test_long_close_crosses_back_above_live_vwap(self):
        # Frame: 95, 96, 99.2. Live vwap ~96.7. Long entry was below
        # vwap (deviation > 0). Last close 99.2 > live vwap → cross.
        # tp = 105.0 — not yet reached, so TP-cross does not fire.
        df = _candles_with_close([95.0, 96.0, 99.2])
        pkg = _pkg(direction="long", entry=95.0, sl=93.5, tp=105.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["action"] == "close"
        assert verdict["reason"] == "vwap_cross"
        assert verdict["exit_price"] == 99.2

    def test_short_close_crosses_back_below_live_vwap(self):
        # Frame: 105, 104, 100.8. Live vwap ~103.3. Short entry was
        # above vwap. Last close 100.8 < live vwap → cross.
        df = _candles_with_close([105.0, 104.0, 100.8])
        pkg = _pkg(direction="short", entry=105.0, sl=106.5, tp=95.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "vwap_cross"

    def test_long_below_live_vwap_does_not_close(self):
        # Frame: 95, 96, 96.5. Live vwap ~95.83. Last close 96.5 >
        # live vwap → would normally cross, but pick a frame where
        # close is still below the live vwap to assert the no-action
        # branch. Use 90, 100, 91 — live vwap ~93.7, close 91 below → no
        # cross. tp 105, sl 88 — neither hit either. (Last close 91 is
        # +0.5R from entry; staying below +1R keeps the SL-to-BE
        # fallback dormant so this test exercises the pure no-action
        # path on the VWAP-cross branch.)
        df = _candles_with_close([90.0, 100.0, 91.0])
        pkg = _pkg(direction="long", entry=90.0, sl=88.0, tp=105.0)
        assert vwap.monitor({}, df, pkg) is None


# ---------------------------------------------------------------------------
# 4. Time-decay close
# ---------------------------------------------------------------------------


class TestTimeDecayClose:
    """Closes when the position is older than the hold window even if
    TP / SL / VWAP-cross have not fired yet."""

    # Frame design: closes [90, 100, 91] gives live vwap ≈ 93.7. For
    # a long entry, last close 91 is *below* live vwap → no
    # VWAP-cross (price has not yet reverted). tp 105 / sl 88 keep
    # TP and SL out of the way too. Last close 91 is +0.5R from
    # entry; under +1R it also stays out of the SL-to-break-even
    # fallback so these tests exercise pure time-decay and no-action
    # branches without crossing into BE territory. This is the
    # realistic time-decay scenario: price is still in the deviation
    # band when the hold window expires.
    LONG_NO_TRIGGER_CLOSES = [90.0, 100.0, 91.0]
    LONG_PKG_KW = {"direction": "long", "entry": 90.0, "sl": 88.0, "tp": 105.0}
    SHORT_NO_TRIGGER_CLOSES = [110.0, 100.0, 108.0]
    SHORT_PKG_KW = {"direction": "short", "entry": 110.0, "sl": 112.0, "tp": 95.0}

    def test_long_open_past_hold_window_closes(self):
        df = _candles_with_close(self.LONG_NO_TRIGGER_CLOSES)
        pkg = _pkg(**self.LONG_PKG_KW, created_at=_iso_minutes_ago(90))
        verdict = vwap.monitor({"monitor_hold_window_minutes": 60}, df, pkg)
        assert verdict is not None
        assert verdict["action"] == "close"
        assert verdict["reason"] == "time_decay"
        assert verdict["exit_price"] == self.LONG_NO_TRIGGER_CLOSES[-1]

    def test_short_open_past_default_hold_window_closes(self):
        df = _candles_with_close(self.SHORT_NO_TRIGGER_CLOSES)
        pkg = _pkg(
            **self.SHORT_PKG_KW,
            created_at=_iso_minutes_ago(vwap.MONITOR_HOLD_WINDOW_MINUTES + 5),
        )
        # No cfg → falls back to module default (240 min).
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "time_decay"

    def test_within_hold_window_does_not_close_on_time_decay(self):
        df = _candles_with_close(self.LONG_NO_TRIGGER_CLOSES)
        pkg = _pkg(**self.LONG_PKG_KW, created_at=_iso_minutes_ago(30))
        # cfg window 60 min, age 30 min — below the threshold.
        assert vwap.monitor({"monitor_hold_window_minutes": 60}, df, pkg) is None

    def test_zero_or_negative_hold_window_disables_time_decay(self):
        df = _candles_with_close(self.LONG_NO_TRIGGER_CLOSES)
        pkg = _pkg(**self.LONG_PKG_KW, created_at=_iso_minutes_ago(10000))
        assert vwap.monitor({"monitor_hold_window_minutes": 0}, df, pkg) is None
        assert vwap.monitor({"monitor_hold_window_minutes": -1}, df, pkg) is None

    def test_priority_tp_cross_wins_over_time_decay(self):
        # Old package + price has already crossed TP. TP-cross is
        # listed first in priority order; reason must be tp_cross.
        df = _candles_with_close([100.0, 101.0, 105.5])
        pkg = _pkg(
            direction="long", entry=100.0, sl=99.0, tp=105.0,
            created_at=_iso_minutes_ago(99999),
        )
        verdict = vwap.monitor({"monitor_hold_window_minutes": 60}, df, pkg)
        assert verdict is not None
        assert verdict["reason"] == "tp_cross"

    def test_malformed_created_at_skips_time_decay(self):
        # Bad created_at → time-decay branch silently skipped (no crash).
        df = _candles_with_close(self.LONG_NO_TRIGGER_CLOSES)
        pkg = _pkg(**self.LONG_PKG_KW)
        pkg["created_at"] = "not-a-timestamp"
        assert vwap.monitor({"monitor_hold_window_minutes": 1}, df, pkg) is None


# ---------------------------------------------------------------------------
# 5. No-action path
# ---------------------------------------------------------------------------


class TestNoActionPath:
    """When none of the four close paths fire, monitor() returns None."""

    def test_long_within_band_returns_none(self):
        # Frame: 95, 95, 95. Live vwap ≈ 95. Long entry 95, sl 90,
        # tp 105. Last close 95 — at vwap (no strict cross), under tp,
        # above sl. Just-opened. → None.
        df = _candles_with_close([95.0, 95.0, 95.0])
        # Build pkg WITHOUT triggering vwap-cross at the boundary:
        # close 95 == vwap 95 means current_price >= vwap_live for a
        # long → vwap-cross WOULD fire. To exercise the strict
        # no-action path, drop close just below live vwap. Last close
        # 91 is +0.5R from entry, which keeps the SL-to-BE fallback
        # dormant too (BE only fires at +1R or beyond).
        df = _candles_with_close([90.0, 100.0, 91.0])
        pkg = _pkg(direction="long", entry=90.0, sl=88.0, tp=110.0)
        assert vwap.monitor({}, df, pkg) is None

    def test_short_within_band_returns_none(self):
        # Mirror of the long case. Live vwap ~94, close 96 above vwap
        # → still good for a short (price hasn't reverted yet).
        # tp 80, sl 110.
        df = _candles_with_close([100.0, 90.0, 96.0])
        pkg = _pkg(direction="short", entry=100.0, sl=110.0, tp=80.0)
        assert vwap.monitor({}, df, pkg) is None


# ---------------------------------------------------------------------------
# Defensive — bad inputs return None, never raise
# ---------------------------------------------------------------------------


class TestMonitorDefensive:
    def test_empty_dataframe_returns_none(self):
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=102.0)
        assert vwap.monitor({}, pd.DataFrame(), pkg) is None

    def test_none_dataframe_returns_none(self):
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=102.0)
        assert vwap.monitor({}, None, pkg) is None

    def test_missing_close_column_returns_none(self):
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=102.0)
        df = pd.DataFrame({"open": [100.0]})
        assert vwap.monitor({}, df, pkg) is None

    def test_missing_pkg_keys_returns_none(self):
        df = _candles_with_close([100.0, 101.0])
        # No tp / sl / direction.
        assert vwap.monitor({}, df, {"entry": 100.0}) is None

    def test_unknown_direction_returns_none(self):
        df = _candles_with_close([100.0, 101.0])
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=102.0)
        pkg["direction"] = "neutral"
        assert vwap.monitor({}, df, pkg) is None

    def test_zero_volume_frame_skips_vwap_cross_branch(self):
        # Zero volume → compute_vwap raises; monitor swallows and
        # falls through. With closes that don't hit TP / SL and a
        # fresh package, the result should be None (not a vwap_cross
        # close). Last close 100.8 is +0.8R from entry — under the
        # +1R SL-to-BE threshold so the fallback stays dormant too.
        df = _candles_with_close([100.0, 100.5, 100.8], volume=0.0)
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=105.0)
        assert vwap.monitor({}, df, pkg) is None

    def test_cfg_none_falls_back_to_default(self):
        # cfg=None → must not crash on .get(); time-decay default
        # MONITOR_HOLD_WINDOW_MINUTES applies. Fresh package + frame
        # in deviation territory (last close +0.5R from entry, under
        # the +1R SL-to-BE trigger) → None.
        df = _candles_with_close([90.0, 100.0, 91.0])
        pkg = _pkg(direction="long", entry=90.0, sl=88.0, tp=105.0)
        assert vwap.monitor(None, df, pkg) is None

    def test_invalid_hold_window_falls_back_to_default(self):
        # Garbage cfg value → fall back to module default rather than
        # crash. Default is 240 min; package is 30 min old → still
        # within the window → no time-decay close. Last close 91 is
        # +0.5R, below the SL-to-BE trigger.
        df = _candles_with_close([90.0, 100.0, 91.0])
        pkg = _pkg(
            direction="long", entry=90.0, sl=88.0, tp=105.0,
            created_at=_iso_minutes_ago(30),
        )
        assert vwap.monitor({"monitor_hold_window_minutes": "abc"}, df, pkg) is None


# ---------------------------------------------------------------------------
# 6. SL-to-break-even fallback (strategy position awareness)
# ---------------------------------------------------------------------------


class TestSlToBreakeven:
    """Defence-in-depth fallback: when price has moved >= 1R in the
    trade's favour but none of the four close paths fired, slide SL to
    entry to lock in partial profit. Last in the priority chain so any
    close verdict on the same tick wins.

    Frames are constructed so the live VWAP sits *beyond* the current
    price (long: VWAP above price; short: VWAP below price), keeping
    the VWAP-cross branch dormant. TP / SL distances are picked so
    neither close fires either, which leaves SL-to-BE as the only
    plausible verdict.
    """

    def test_long_at_one_r_returns_breakeven_sl(self):
        # entry 100, sl 99, tp 110. 1R = 1. Last close 101 (= entry +1R).
        # Frame [105, 105, 101] gives live vwap ~103.7 — well above 101,
        # so VWAP-cross does not fire. TP at 110 is out of reach; SL at
        # 99 is unchallenged. Only BE remains.
        df = _candles_with_close([105.0, 105.0, 101.0])
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict == {"sl": 100.0}

    def test_long_beyond_one_r_returns_breakeven_sl(self):
        # entry 100, sl 99, tp 110. Price 102 (= +2R). VWAP frame keeps
        # vwap above current price so vwap-cross stays dormant.
        df = _candles_with_close([108.0, 108.0, 102.0])
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict == {"sl": 100.0}

    def test_short_at_one_r_returns_breakeven_sl(self):
        # entry 100, sl 101, tp 90. 1R = 1. Last close 99 (= entry -1R).
        # Frame [95, 95, 99] gives live vwap ~96.3 — below 99, so the
        # short VWAP-cross (price <= vwap) does NOT fire. TP at 90 is
        # out of reach; SL at 101 is unchallenged. Only BE remains.
        df = _candles_with_close([95.0, 95.0, 99.0])
        pkg = _pkg(direction="short", entry=100.0, sl=101.0, tp=90.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict == {"sl": 100.0}

    def test_long_below_one_r_no_breakeven(self):
        # Price 100.5 = +0.5R. Below the BE threshold → None.
        # Same frame shape as the positive case so VWAP-cross stays
        # dormant.
        df = _candles_with_close([105.0, 105.0, 100.5])
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=110.0)
        assert vwap.monitor({}, df, pkg) is None

    def test_long_already_at_breakeven_idempotent(self):
        # SL already at entry (=BE). The shared helper must not re-emit
        # the same value tick after tick — returns None for "no change".
        df = _candles_with_close([105.0, 105.0, 102.0])
        pkg = _pkg(direction="long", entry=100.0, sl=100.0, tp=110.0)
        assert vwap.monitor({}, df, pkg) is None

    def test_be_does_not_override_tp_cross(self):
        # Price has both reached TP AND moved >= 1R. TP-cross is
        # listed first in priority order; reason must be tp_cross,
        # the verdict must be a close (not a sl-move).
        df = _candles_with_close([100.0, 101.0, 105.5])
        pkg = _pkg(direction="long", entry=100.0, sl=99.0, tp=105.0)
        verdict = vwap.monitor({}, df, pkg)
        assert verdict is not None
        assert verdict.get("action") == "close"
        assert verdict.get("reason") == "tp_cross"

    def test_be_does_not_override_time_decay(self):
        # Aged past hold window AND >= 1R reached. Time-decay must win
        # — the position closes rather than locking BE for one more
        # tick.
        df = _candles_with_close([105.0, 105.0, 101.0])
        pkg = _pkg(
            direction="long", entry=100.0, sl=99.0, tp=110.0,
            created_at=_iso_minutes_ago(99999),
        )
        verdict = vwap.monitor({"monitor_hold_window_minutes": 60}, df, pkg)
        assert verdict is not None
        assert verdict.get("reason") == "time_decay"


# ---------------------------------------------------------------------------
# Hard guardrail: turtle_soup must keep its v1 break-even behaviour.
# ---------------------------------------------------------------------------


class TestTurtleSoupUnaffected:
    """Sprint guardrail (`docs/sprint-plans/S-047-bybit2-spot-margin.md`
    § 7): T4 rewrites VWAP only. turtle_soup keeps the
    break-even-after-1R contract from S-030 PR2.
    """

    def test_turtle_soup_still_uses_breakeven_sl(self):
        from src.units.strategies import turtle_soup
        # Long: entry 100, sl 98, tp 104. Price at 102 (=1R) — turtle
        # soup must still emit {"sl": 100.0}.
        df = _candles_with_close([100.5, 101.0, 102.0])
        pkg = {
            "entry": 100.0, "sl": 98.0, "tp": 104.0,
            "direction": "long", "symbol": "BTCUSDT",
        }
        assert turtle_soup.monitor({}, df, pkg) == {"sl": 100.0}

    def test_turtle_soup_no_close_verdict_at_breakeven_trigger(self):
        from src.units.strategies import turtle_soup
        # Same scenario; the verdict is a sl-move, not a close.
        df = _candles_with_close([100.5, 101.0, 102.0])
        pkg = {
            "entry": 100.0, "sl": 98.0, "tp": 104.0,
            "direction": "long", "symbol": "BTCUSDT",
        }
        verdict = turtle_soup.monitor({}, df, pkg)
        assert verdict is not None
        assert "action" not in verdict
