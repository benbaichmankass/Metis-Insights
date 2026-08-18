"""M31 P5 `rr_from_here` FLOOR lever — the PULLBACK harness.

`tests/test_rr_floor_lever.py` pins the same lever in `scripts/backtest_trend.py`.
This file exists because the port is where the two harnesses are free to
disagree, and because the pullback family is the one that needs the lever most:
measured 2026-08-18 over the live book, `htf_pullback_trend_2h` carries 11 of
the 22 open trades that have NO decision-driven exit path at all
(`scripts/ops/exit_path_coverage.py`), and its unit module implements exactly
one of the four M20 close mechanisms. A floor on `rr_from_here` is the only
decision-driven close its legs could realistically get — so it had better be
measurable here, not just in the harness for the family that already has three.

The three properties, same as the trend file:

1. **One definition** — the harness IMPORTS `r_distances` from the live
   telemetry module. Object identity, not agreement on sampled cases.
2. **Measurable vs inert are different states** — with no capped TP there is no
   `r_to_target`, so a zero delta is not a measurement.
3. **The lever is not dead** — a floor above the observed distribution must
   actually fire, or every "no change" assertion below proves nothing.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

pd = pytest.importorskip("pandas")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.runtime.position_telemetry import r_distances  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load("_bt_pullback_rr_floor", "scripts/backtest_pullback.py")

BASE = dict(trend_lookback=40, pullback_lookback=10, pullback_frac=0.5,
            atr_period=14, atr_stop_mult=2.5, trail_mult=5.0,
            timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")

#: Production's Bybit TP-distance clamp; the only setting under which the lever
#: is measurable at all.
LIVE_TP_CAP = 0.099


def _candles(rule="5min"):
    df = pd.read_csv(os.path.join(_REPO, "data/backtest_candles.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


def _run(df, **kw):
    return bt.run_backtest(df.copy(), **{**BASE, **kw})


# --- 1. ONE DEFINITION ---------------------------------------------------- #

def test_pullback_harness_shares_the_live_definition():
    """Object IDENTITY. Two implementations agreeing on sampled cases is exactly
    how a drifted definition survives review."""
    assert bt.r_distances is r_distances


def test_both_harnesses_use_the_same_object():
    """The port must not have introduced a second rr definition alongside the
    trend harness's. This is the assertion the port itself could break."""
    trend = _load("_bt_trend_rr_floor_xcheck", "scripts/backtest_trend.py")
    assert bt.r_distances is trend.r_distances


# --- 2. MEASURABLE vs INERT ARE DIFFERENT STATES -------------------------- #

def test_state_is_off_when_no_floor_requested():
    assert _run(_candles(), tp_cap_pct=LIVE_TP_CAP)["rr_floor_state"] == "off"


def test_state_is_unmeasurable_without_a_capped_tp():
    """The state that stops an inert run reading as a measured no-op."""
    s = _run(_candles(), rr_floor=1.0)          # note: no tp_cap_pct
    assert s["rr_floor_state"] == "unmeasurable_no_tp_cap"
    assert s["rr_floor_exits"] == 0
    assert s["rr_min_n"] == 0


def test_state_is_measurable_with_a_capped_tp():
    s = _run(_candles(), tp_cap_pct=LIVE_TP_CAP, rr_floor=1.0)
    assert s["rr_floor_state"] == "measurable"


def test_cli_refuses_the_unmeasurable_combination():
    rc = bt.main(["backtest_pullback.py",
                  "--data", os.path.join(_REPO, "data/backtest_candles.csv"),
                  "--rr-floor", "1.0"])          # no --tp-cap-pct
    assert rc == 2


def test_cli_accepts_the_measurable_combination():
    """Positive control for the refusal above — it must not refuse everything."""
    rc = bt.main(["backtest_pullback.py",
                  "--data", os.path.join(_REPO, "data/backtest_candles.csv"),
                  "--tp-cap-pct", str(LIVE_TP_CAP), "--rr-floor", "1.0"])
    assert rc == 0


def test_rr_min_is_none_not_zero_when_unmeasured():
    """"We did not look" and "the ratio reached zero" are opposite statements."""
    s = _run(_candles())                        # no TP cap -> nothing measurable
    assert s["rr_min_n"] == 0
    for k in ("rr_min_p10", "rr_min_median", "rr_min_p90"):
        assert s[k] is None


# --- 3. THE LEVER IS NOT DEAD --------------------------------------------- #

def test_a_floor_above_the_observed_distribution_fires():
    """POSITIVE CONTROL. Without this every 'no change' below proves nothing.

    The floor comes from the run's OWN measured rr_min_p90, so the test cannot
    rot into asserting a stale constant.
    """
    df = _candles()
    base = _run(df, tp_cap_pct=LIVE_TP_CAP)
    assert base["rr_min_n"] > 0, "fixture must measure rr or this proves nothing"
    high = float(base["rr_min_p90"]) * 2.0
    fired = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=high)
    assert fired["rr_floor_exits"] > 0
    assert fired["by_outcome"].get("rr_floor_exit", 0) == fired["rr_floor_exits"]


def test_more_fires_at_a_higher_floor():
    """Monotonicity — a lever that fires but ignores its own threshold would
    pass the control above."""
    df = _candles()
    p90 = float(_run(df, tp_cap_pct=LIVE_TP_CAP)["rr_min_p90"])
    low = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=p90 * 0.5)["rr_floor_exits"]
    high = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=p90 * 2.0)["rr_floor_exits"]
    assert high > low


def test_floor_off_is_byte_identical():
    """rr_floor=0 must not perturb the run at all — the lever is opt-in."""
    df = _candles()
    off = _run(df, tp_cap_pct=LIVE_TP_CAP)
    zero = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=0.0)
    for k in ("total_trades", "net_total_r", "max_drawdown_r", "by_outcome"):
        assert off[k] == zero[k]


def test_rr_floor_exits_are_counted_in_by_outcome():
    """The exit reason must reach the outcome histogram, not just the counter —
    a lever whose exits are invisible in by_outcome cannot be attributed."""
    df = _candles()
    p90 = float(_run(df, tp_cap_pct=LIVE_TP_CAP)["rr_min_p90"])
    s = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=p90 * 2.0)
    assert s["by_outcome"].get("rr_floor_exit", 0) > 0
    assert sum(s["by_outcome"].values()) == s["total_trades"]
