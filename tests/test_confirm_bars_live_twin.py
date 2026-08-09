"""M21 E-2 batch 2 — live confirmation-bar entry twin (trend_donchian unit).

Contract (mirrors the harness lever, tests/test_confirm_bars_lever.py):
  * Undeclared (``confirm_bars`` absent/0) ⇒ ``order_package`` behaviour
    byte-unchanged (breakout on the latest bar ⇒ package).
  * Declared ``confirm_bars: 1`` ⇒ a latest-bar breakout with no held
    prior-bar breakout is NON-actionable; a breakout one bar back whose
    close held beyond the signal bar's channel edge IS actionable, with
    entry at the LATEST close and the confidence taken from the SIGNAL
    bar's depth.
  * A close back inside the signal bar's channel edge cancels.
  * Harness parity: on the same synthetic tape, the live twin fires on
    exactly the bar the harness enters.

MIGRATED 2026-08-09 (``BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-
TEST-COUPLING``) from the retired ``scripts/research/backtest_trend.py`` onto
the live-faithful ``scripts/backtest_trend.py``. **Disposition: (b) repoint —
and the repoint STRENGTHENS the test.** ``test_harness_parity_entry_bar`` is a
live-vs-harness parity assertion, so it is only meaningful against the harness
the live unit actually mirrors: ``trend_donchian.order_package`` freezes the
entry bar's ATR into ``meta["atr"]`` and ``monitor()`` trails off that frozen
value, which is ``scripts/backtest_trend.py``'s semantics and NOT the research
copy's rolling-ATR trail. Pointing it at the research engine made it assert
parity against an engine the live unit does not match. The assertions
themselves are entry-side (entry bar + entry price) and both engines agreed on
this tape, so no expected number moved — what changed is that the test now
means what its name says.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.units.strategies import trend_donchian as td

from tests.trend_harness_engine import trend_trades  # noqa: E402


def _df(closes):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame([{"timestamp": (start + timedelta(hours=i)).isoformat(),
                          "open": c, "high": c + 0.5, "low": c - 0.5,
                          "close": c, "volume": 1.0}
                         for i, c in enumerate(closes)])


CFG = {"symbol": "BTCUSDT", "donchian": 10, "atr_period": 5,
       "atr_stop_mult": 2.0, "trail_mult": 3.0, "min_confidence": 0.0,
       "timeframe": "1h"}


def test_undeclared_unchanged():
    # Latest bar is a fresh breakout: undeclared behaviour emits a package.
    df = _df([100.0] * 20 + [102.0])
    pkg = td.order_package(dict(CFG), df)
    assert pkg["direction"] == "long"
    assert "confirm_bars" not in pkg["meta"]


def test_declared_fresh_breakout_not_actionable():
    df = _df([100.0] * 20 + [102.0])
    with pytest.raises(ValueError, match="no breakout 1 bar"):
        td.order_package({**CFG, "confirm_bars": 1}, df)


def test_declared_held_breakout_fires_at_latest_close():
    df = _df([100.0] * 20 + [102.0, 103.0])
    pkg = td.order_package({**CFG, "confirm_bars": 1}, df)
    assert pkg["direction"] == "long"
    assert pkg["entry"] == 103.0          # latest close, not the signal bar
    assert pkg["meta"]["confirm_bars"] == 1
    # confidence from the SIGNAL bar's depth, not the latest bar's
    base = td.order_package(dict(CFG), _df([100.0] * 20 + [102.0]))
    assert pkg["confidence"] == base["confidence"]


def test_declared_close_back_inside_cancels():
    df = _df([100.0] * 20 + [102.0, 100.0])
    with pytest.raises(ValueError, match="confirmation failed"):
        td.order_package({**CFG, "confirm_bars": 1}, df)


def test_harness_parity_entry_bar():
    # The harness (confirm_bars=1) enters at bar 21's close on this tape;
    # the live twin must be actionable exactly when evaluated at bar 21
    # and non-actionable at bar 20.
    closes = [100.0] * 20 + [101.5, 102.5, 103.5, 104.5] + [97.0] * 10
    full = _df(closes)
    trades = trend_trades(full, confirm_bars=1)
    t = next(tr for tr in trades if tr.direction == "long")
    harness_entry_ts = pd.Timestamp(t.entry_time)

    with pytest.raises(ValueError):
        td.order_package({**CFG, "confirm_bars": 1}, full.iloc[:21])  # bar 20
    pkg = td.order_package({**CFG, "confirm_bars": 1}, full.iloc[:22])  # bar 21
    assert pd.Timestamp(pkg["meta"]["entry_time"]) == harness_entry_ts
    assert pkg["entry"] == t.entry
