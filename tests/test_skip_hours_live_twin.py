"""M21 E-2 round 3 — live time-of-day entry twin (donchian + pullback units).

Contract (mirrors the harness lever, tests/test_skip_hours_lever.py):
  * Undeclared (``skip_hours`` absent/"") ⇒ ``order_package`` behaviour
    byte-unchanged, and no ``skip_hours`` in meta.
  * Declared ⇒ a trigger bar whose UTC hour is in the set is
    NON-actionable (standard ValueError path); a trigger on any other
    hour is unaffected and stamps ``meta["skip_hours"]`` for audit.
  * Fail-permissive: a malformed CSV disables the gate (never strands
    the strategy); an unparseable timestamp never skips.
  * donchian + confirm_bars: the gate reads the TRIGGER (signal) bar,
    not the later confirming/entry bar — same as the harness.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.units.strategies import htf_pullback_trend_2h as hp
from src.units.strategies import trend_donchian as td


def _df(closes, start_hour=0):
    start = datetime(2026, 1, 1, start_hour, tzinfo=timezone.utc)
    return pd.DataFrame([{"timestamp": (start + timedelta(hours=i)).isoformat(),
                          "open": c, "high": c + 0.5, "low": c - 0.5,
                          "close": c, "volume": 1.0}
                         for i, c in enumerate(closes)])


# ---------------------------------------------------------------- donchian --

TD_CFG = {"symbol": "BTCUSDT", "donchian": 10, "atr_period": 5,
          "atr_stop_mult": 2.0, "trail_mult": 3.0, "min_confidence": 0.0,
          "timeframe": "1h"}


def _breakout_at_hour(hour):
    # 20 flat bars then a breakout on the latest bar; start_hour chosen so the
    # latest (trigger) bar lands on the requested UTC hour.
    return _df([100.0] * 20 + [102.0], start_hour=(hour - 20) % 24)


def test_td_undeclared_unchanged():
    pkg = td.order_package(dict(TD_CFG), _breakout_at_hour(0))
    assert pkg["direction"] == "long"
    assert "skip_hours" not in pkg["meta"]


def test_td_trigger_hour_in_set_non_actionable():
    df = _breakout_at_hour(0)
    with pytest.raises(ValueError, match="time-of-day gate"):
        td.order_package({**TD_CFG, "skip_hours": "0"}, df)


def test_td_other_hour_unchanged_and_stamped():
    df = _breakout_at_hour(7)
    base = td.order_package(dict(TD_CFG), df)
    gated = td.order_package({**TD_CFG, "skip_hours": "0"}, df)
    assert gated["direction"] == base["direction"]
    assert gated["entry"] == base["entry"]
    assert gated["meta"]["skip_hours"] == "0"


def test_td_malformed_csv_disables_gate():
    df = _breakout_at_hour(0)
    pkg = td.order_package({**TD_CFG, "skip_hours": "banana"}, df)
    assert pkg["direction"] == "long"          # gate off, never strands


def test_td_confirm_bars_gate_reads_trigger_bar():
    # Trigger (breakout) at hour 20, confirming close at hour 21: entry
    # fires on the hour-21 bar, but the GATE reads the hour-20 trigger.
    df = _df([100.0] * 20 + [102.0, 103.0])    # trigger idx 20 = hour 20
    with pytest.raises(ValueError, match="time-of-day gate"):
        td.order_package({**TD_CFG, "confirm_bars": 1, "skip_hours": "20"}, df)
    pkg = td.order_package({**TD_CFG, "confirm_bars": 1, "skip_hours": "21"}, df)
    assert pkg["direction"] == "long"          # entry-bar hour is NOT gated


# ---------------------------------------------------------------- pullback --

HP_CFG = {"symbol": "SPY", "trend_lookback": 10, "pullback_lookback": 5,
          "pullback_frac": 0.5, "atr_period": 5, "atr_stop_mult": 2.0,
          "trail_mult": 2.0, "min_confidence": 0.0, "timeframe": "1h"}

# Same geometry as tests/test_skip_hours_lever.py::_pb_tape — ramp, 2-bar
# pullback into the lower half of the 5-bar range, up-close trigger on the
# latest bar.
_HP_CLOSES = [100 + k * 1.4 for k in range(20)] + [127.0, 124.5, 123.2, 123.8]


def _hp_tape_at_hour(hour):
    return _df(_HP_CLOSES, start_hour=(hour - 23) % 24)  # trigger idx 23


def test_hp_undeclared_unchanged():
    pkg = hp.order_package(dict(HP_CFG), _hp_tape_at_hour(19))
    assert pkg["direction"] == "long"
    assert "skip_hours" not in pkg["meta"]


def test_hp_trigger_hour_in_set_non_actionable():
    with pytest.raises(ValueError, match="time-of-day gate"):
        hp.order_package({**HP_CFG, "skip_hours": "19,20"}, _hp_tape_at_hour(19))
    with pytest.raises(ValueError, match="time-of-day gate"):
        hp.order_package({**HP_CFG, "skip_hours": "19,20"}, _hp_tape_at_hour(20))


def test_hp_other_hour_unchanged_and_stamped():
    df = _hp_tape_at_hour(14)
    base = hp.order_package(dict(HP_CFG), df)
    gated = hp.order_package({**HP_CFG, "skip_hours": "19,20"}, df)
    assert gated["direction"] == base["direction"]
    assert gated["entry"] == base["entry"]
    assert gated["meta"]["skip_hours"] == "19,20"


def test_hp_malformed_csv_disables_gate():
    pkg = hp.order_package({**HP_CFG, "skip_hours": "nope"}, _hp_tape_at_hour(19))
    assert pkg["direction"] == "long"
