"""Offline tests for the trainer-side exit-head replay leg.

Two things are pinned here:

1. **`would_exit_for` is THE decision predicate**, shared by the live monitor
   (`src/runtime/exit_head_shadow.py::maybe_score_exit_head`) and the offline
   replay. The extraction was behaviour-preserving; these cases pin the
   truth table so a future edit cannot silently move live and replay apart.
   That drift is the exact defect class
   `docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md` §5f
   documents for the two `backtest_trend.py` copies.

2. **The replay re-resolves a trade at the FIRST firing bar**, and a head that
   never fires leaves the trade byte-identical. LightGBM is not required: the
   scorer is injected, which is why `replay_trade` takes `predict`.
"""
import importlib.util
import os
import sys

import pytest

pd = pytest.importorskip("pandas")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.runtime.exit_head_shadow import shape_params, would_exit_for  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


replay = _load("_exit_head_replay", "scripts/ml/exit_head_replay.py")
harness = _load("_bt_trend_for_replay", "scripts/backtest_trend.py")


# --- 1. the shared decision predicate ------------------------------------- #

def test_shape_params_defaults_match_the_live_fallbacks():
    assert shape_params({}) == (0.10, 0.5, "below_half_r")
    assert shape_params({"tau": 0.3, "below_r": 1.0, "policy": "peak_winner"}) == (
        0.3, 1.0, "peak_winner")


@pytest.mark.parametrize("shape,score,open_r,expected", [
    # below_half_r (the LIVE head): fires on a LOW score AND a losing open_r.
    ({"policy": "below_half_r", "tau": 0.1, "below_r": 0.5}, 0.05, 0.2, True),
    ({"policy": "below_half_r", "tau": 0.1, "below_r": 0.5}, 0.05, 0.9, False),
    ({"policy": "below_half_r", "tau": 0.1, "below_r": 0.5}, 0.50, 0.2, False),
    # peak_is_in: fires on a HIGH score, no open_r condition.
    ({"policy": "peak_is_in", "tau": 0.6, "below_r": 0.5}, 0.9, -3.0, True),
    ({"policy": "peak_is_in", "tau": 0.6, "below_r": 0.5}, 0.4, 5.0, False),
    # peak_winner: HIGH score AND already a winner by below_r.
    ({"policy": "peak_winner", "tau": 0.6, "below_r": 1.0}, 0.9, 1.5, True),
    ({"policy": "peak_winner", "tau": 0.6, "below_r": 1.0}, 0.9, 0.5, False),
    # An empty shape must resolve to the live defaults, not to "always exit".
    ({}, 0.05, 0.2, True),
    ({}, 0.05, 0.8, False),
])
def test_would_exit_truth_table(shape, score, open_r, expected):
    assert would_exit_for(shape, score, open_r) is expected


# --- 2. the replay ---------------------------------------------------------- #

def _candles():
    df = pd.read_csv(os.path.join(_REPO, "data/backtest_candles.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return (df.set_index("timestamp").resample("5min", label="right", closed="right")
            .agg(agg).dropna().reset_index())


def _trades(df):
    out = []
    harness.run_backtest(df.copy(), donchian=20, atr_period=14, atr_stop_mult=2.5,
                         trail_mult=3.0, timeout_bars=200, cooldown_bars=1,
                         timeframe="5min", symbol="BTCUSDT", trades_out=out)
    return out


def test_trades_out_is_purely_additive():
    """The summary must be identical whether or not trades_out is passed."""
    df = _candles()
    without = harness.run_backtest(
        df.copy(), donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
        timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")
    sink = []
    with_out = harness.run_backtest(
        df.copy(), donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
        timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT",
        trades_out=sink)
    assert without == with_out
    assert len(sink) == without["total_trades"] > 0


def test_head_that_never_fires_leaves_the_trade_untouched():
    df, art = _candles(), {"features": [], "shape": {"policy": "below_half_r",
                                                     "tau": 0.1, "below_r": 0.5}}
    trades = _trades(df)
    assert trades, "fixture must produce trades or the test proves nothing"
    for t in trades:
        rec = replay.replay_trade(df, t, art, lambda _v: 0.99, "close")
        assert rec["exit_head_fired"] is False
        assert rec["replayed_r"] == t.r_multiple
        assert rec["replayed_outcome"] == t.outcome


def test_head_that_always_fires_re_resolves_at_the_first_in_trade_bar():
    df = _candles()
    art = {"features": [], "shape": {"policy": "peak_is_in", "tau": 0.0,
                                     "below_r": 0.0}}
    trades = _trades(df)
    fired = 0
    for t in trades:
        rec = replay.replay_trade(df, t, art, lambda _v: 1.0, "close")
        if not rec["exit_head_fired"]:
            continue          # a trade with no scoreable in-trade bar
        fired += 1
        # first firing bar, and the R is the mark-to-market at THAT close
        assert rec["exit_bar_index"] <= t.exit_index
        close = float(df["close"].iloc[rec["exit_bar_index"]])
        expect = ((close - t.entry) if t.direction == "long"
                  else (t.entry - close)) / t.risk
        assert rec["replayed_r"] == pytest.approx(round(expect, 4))
        assert rec["replayed_outcome"] == "exit_head"
    assert fired > 0, "an always-fire head must re-resolve at least one trade"


def test_non_close_action_is_not_applied():
    df, art = _candles(), {"features": [], "shape": {"policy": "peak_is_in",
                                                     "tau": 0.0, "below_r": 0.0}}
    t = _trades(df)[0]
    rec = replay.replay_trade(df, t, art, lambda _v: 1.0, "annotate")
    assert rec["exit_head_fired"] is False
    assert rec["replayed_r"] == t.r_multiple
    assert "not an apply action" in rec["note"]


def test_missing_artifact_dir_raises_rather_than_returning_empty():
    """'No head' must never be indistinguishable from 'the head said hold'."""
    with pytest.raises(replay.ReplayUnavailable) as exc:
        replay.load_heads("/nonexistent/exit_head", "1h", "BTCUSDT")
    assert "trainer" in str(exc.value)
