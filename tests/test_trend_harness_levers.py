"""The M20/M21 levers ported into the LIVE-FAITHFUL trend harness.

`BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE` step (a): the 15
research-only lever flags moved into `scripts/backtest_trend.py`, whose engine
(frozen entry-bar ATR, SL-first nested loop, post-exit cooldown, no flip exit) is
the one `trend_donchian.monitor()` actually matches — see design-doc §5f.

The load-bearing property is **byte-identical-at-default**: porting a lever must
not move a single number on any existing run, because every prior trend result
(the debt matrix, the calibration corpus, `backtest_trades.db`) was produced with
these levers unset. `test_every_lever_is_a_noop_at_default` is the guard; the
rest pin that each lever, once declared, actually does the thing it claims.
"""
import importlib.util
import os
import sys

import pytest

pd = pytest.importorskip("pandas")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load("_bt_trend_levers", "scripts/backtest_trend.py")


def _candles(rule="5min"):
    df = pd.read_csv(os.path.join(_REPO, "data/backtest_candles.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


BASE = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
            timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")


def _run(df, **kw):
    return bt.run_backtest(df.copy(), **{**BASE, **kw})


# --- the load-bearing property ------------------------------------------- #

ALL_LEVERS_AT_DEFAULT = dict(
    bank_frac=0.0, bank_at_r=1.0,
    giveback_min_mfe_r=0.0, giveback_r=1.0,
    trail_decay_arm_r=0.0, trail_decay_stall_bars=0, trail_decay_tight_mult=0.0,
    confirm_bars=0, skip_hours="",
    vol_skip_above_pctl=0.0, vol_skip_below_pctl=0.0, vol_pctl_window=200,
    trail_vol_above_pctl=0.0, trail_vol_below_pctl=0.0, trail_vol_tight_mult=0.0,
)


def test_every_lever_is_a_noop_at_default():
    """Passing all 15 lever params at their defaults == not passing them at all.

    If this ever fails, every previously-recorded trend backtest number silently
    changed meaning — which is exactly what the convergence was required not to do.
    """
    df = _candles()
    unset = _run(df)
    explicit = _run(df, **ALL_LEVERS_AT_DEFAULT)
    assert unset == explicit
    assert unset["total_trades"] > 0, "fixture must trade or this proves nothing"


@pytest.mark.parametrize("lever", sorted(ALL_LEVERS_AT_DEFAULT))
def test_each_lever_individually_is_a_noop_at_its_default(lever):
    """Per-lever, so a regression names the offending flag rather than 'something'."""
    df = _candles()
    assert _run(df) == _run(df, **{lever: ALL_LEVERS_AT_DEFAULT[lever]})


def test_params_echo_omits_undeclared_levers():
    """An undeclared lever must not appear in the summary's params block."""
    params = _run(_candles())["params"]
    for key in ("bank_frac", "giveback_min_mfe_r", "trail_decay_tight_mult",
                "confirm_bars", "skip_hours", "vol_skip_above_pctl",
                "trail_vol_tight_mult", "vol_pctl_window"):
        assert key not in params


# --- each lever actually does something when declared --------------------- #

def test_trail_decay_tightens_and_is_reported():
    df = _candles()
    base, decayed = _run(df), _run(df, trail_decay_arm_r=0.5,
                                   trail_decay_tight_mult=1.0)
    assert decayed != base, "a tightened trail must change outcomes"
    assert decayed["params"]["trail_decay_tight_mult"] == 1.0


def test_trail_decay_needs_tight_mult_to_arm():
    """arm_r alone is inert — tight_mult is the lever's on-switch."""
    df = _candles()
    assert _run(df, trail_decay_arm_r=0.5) == _run(df)


def test_giveback_stop_fires_and_labels_its_exit():
    df = _candles()
    out = _run(df, giveback_min_mfe_r=0.3, giveback_r=0.1)
    assert out["by_outcome"].get("giveback_stop", 0) > 0
    assert out["params"]["giveback_min_mfe_r"] == 0.3


def test_bank_lever_lifts_r_on_trades_that_reach_the_rung():
    """Banking half at +1R cannot leave the book unchanged when rungs are hit."""
    df = _candles()
    base, banked = _run(df), _run(df, bank_frac=0.5, bank_at_r=0.5)
    assert banked["net_total_r"] != base["net_total_r"]
    assert banked["total_trades"] == base["total_trades"], (
        "banking is an exit-SIZE lever; it must not change which trades exist")


def test_confirm_bars_reduces_entries_and_never_increases_them():
    df = _candles()
    base, confirmed = _run(df), _run(df, confirm_bars=2)
    assert confirmed["total_trades"] <= base["total_trades"]
    assert confirmed["params"]["confirm_bars"] == 2


def test_skip_hours_removes_only_signal_bars_in_the_named_hours():
    df = _candles()
    base = _run(df)
    all_hours = ",".join(str(h) for h in range(24))
    assert _run(df, skip_hours=all_hours)["total_trades"] == 0
    assert _run(df, skip_hours="")["total_trades"] == base["total_trades"]


def test_vol_skip_gates_entries_at_the_extremes():
    df = _candles()
    base = _run(df)
    # A percentile floor of 1.0 skips everything the window can grade.
    tight = _run(df, vol_skip_below_pctl=1.0, vol_pctl_window=20)
    assert tight["total_trades"] < base["total_trades"]


def test_vol_trail_needs_both_a_bound_and_a_tight_mult():
    """Either half alone is inert — mirrors the research harness's gate."""
    df = _candles()
    assert _run(df, trail_vol_tight_mult=1.0) == _run(df)
    assert _run(df, trail_vol_above_pctl=0.5) == _run(df)
    assert _run(df, trail_vol_above_pctl=0.5, trail_vol_tight_mult=1.0,
                vol_pctl_window=20) != _run(df)


def test_tightest_lever_wins_when_decay_and_vol_trail_both_fire():
    """Composition is by MINIMUM, not by sum — pinned so a future edit can't
    silently make two tighteners additive."""
    assert bt._effective_trail_mult(
        3.0, peak_r=9.9, bars_since_peak=0, decay_on=True, decay_arm_r=1.0,
        decay_stall_bars=0, decay_tight_mult=2.0, vol_on=False, atr_pctl=None,
        j=0, vol_above_pctl=0.0, vol_below_pctl=0.0, vol_tight_mult=0.0) == 2.0
    series = pd.Series([0.9])
    assert bt._effective_trail_mult(
        3.0, peak_r=9.9, bars_since_peak=0, decay_on=True, decay_arm_r=1.0,
        decay_stall_bars=0, decay_tight_mult=2.0, vol_on=True, atr_pctl=series,
        j=0, vol_above_pctl=0.5, vol_below_pctl=0.0, vol_tight_mult=1.5) == 1.5


def test_effective_trail_mult_is_inert_with_both_levers_off():
    assert bt._effective_trail_mult(
        3.0, peak_r=99.0, bars_since_peak=99, decay_on=False, decay_arm_r=0.0,
        decay_stall_bars=0, decay_tight_mult=0.0, vol_on=False, atr_pctl=None,
        j=0, vol_above_pctl=0.0, vol_below_pctl=0.0, vol_tight_mult=0.0) == 3.0


def test_undefined_percentile_never_fires_the_vol_trail():
    """Warm-up NaN must be fail-permissive, not a fired tightener."""
    assert bt._effective_trail_mult(
        3.0, peak_r=0.0, bars_since_peak=0, decay_on=False, decay_arm_r=0.0,
        decay_stall_bars=0, decay_tight_mult=0.0, vol_on=True,
        atr_pctl=pd.Series([float("nan")]), j=0, vol_above_pctl=0.5,
        vol_below_pctl=0.0, vol_tight_mult=1.0) == 3.0


# --- confirm_bars must not corrupt the trade record ----------------------- #

def test_confirm_bars_anchors_the_trade_to_the_ENTRY_bar_not_the_signal_bar():
    """entry_index/entry_time/exit_index must all reference the confirming bar.

    Getting this wrong would silently shift every in-trade window the exit-head
    replay walks (`scripts/ml/exit_head_replay.py` slices entry_index+1..exit_index).
    """
    df = _candles()
    out = []
    bt.run_backtest(df.copy(), **BASE, confirm_bars=2, trades_out=out)
    assert out, "fixture must produce confirmed trades"
    for t in out:
        assert t.exit_index > t.entry_index
        assert t.entry_time == df["timestamp"].iloc[t.entry_index]
        assert t.entry == pytest.approx(float(df["close"].iloc[t.entry_index]))
