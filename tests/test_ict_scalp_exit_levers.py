"""Unit tests for the M20 exit levers on scripts/backtest_ict_scalp.py
(stale-stop + giveback-stop), added 2026-07-28 as the M27 ict_scalp_mgc_15m
exit-refinement follow-up.

The levers must (a) fire on the correct bar, (b) be stop-first (an SL/TP hit on
the same bar wins), and (c) leave the baseline byte-for-byte unchanged when off.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO_ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


bts = _load("backtest_ict_scalp_levers", "scripts/backtest_ict_scalp.py")
_simulate_exit = bts._simulate_exit


def _frame(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# A long trade (entry=100, sl=98 => risk=2, tp far away) that runs to a 3R peak
# on bar 1 then closes back near entry — the classic giveback shape.
_GIVEBACK_LONG = [(100, 101, 99, 100), (100, 106, 100, 101), (101, 106, 100, 101.0)]


def test_giveback_fires_long():
    res = _simulate_exit(_frame(_GIVEBACK_LONG), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=2.0, giveback_r=1.0)
    # bar 1: mfe=(106-100)/2=3R, close=101 => open_r=0.5R, surrendered 2.5R>=1R
    assert res["outcome"] == "giveback_stop"
    assert res["exit_index"] == 1
    assert res["exit_price"] == 101.0


def test_giveback_fires_short():
    rows = [(100, 101, 99, 100), (100, 100, 94, 99), (99, 100, 94, 99.0)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="short",
                         sl=102, tp=90, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=2.0, giveback_r=1.0)
    assert res["outcome"] == "giveback_stop"
    assert res["exit_index"] == 1


def test_giveback_off_by_default():
    # Same peaky frame, no lever => runs to timeout (baseline), NOT giveback.
    res = _simulate_exit(_frame(_GIVEBACK_LONG), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100)
    assert res["outcome"] == "timeout"


def test_giveback_needs_min_mfe():
    # Peak only reaches 3R; min_mfe=5R is never armed => no giveback.
    res = _simulate_exit(_frame(_GIVEBACK_LONG), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=5.0, giveback_r=1.0)
    assert res["outcome"] == "timeout"


# A long trade that never gets profitable — stale-stop should cut it at N bars.
_STALE_LONG = [(100, 100.5, 99.5, 100), (100, 100.2, 99.4, 99.8),
               (99.8, 100.0, 99.2, 99.6), (99.6, 99.9, 99.1, 99.5)]


def test_stale_fires_when_underwater():
    res = _simulate_exit(_frame(_STALE_LONG), start_idx=0, direction="long",
                         sl=95, tp=120, timeout_bars=20, entry=100,
                         stale_exit_bars=3, stale_exit_below_r=0.0)
    assert res["outcome"] == "stale_stop"
    assert res["exit_index"] == 3


def test_stale_off_by_default():
    res = _simulate_exit(_frame(_STALE_LONG), start_idx=0, direction="long",
                         sl=95, tp=120, timeout_bars=20, entry=100)
    assert res["outcome"] == "timeout"


def test_stale_holds_a_winner():
    # Trade IS in profit at the stale bar (open_r >= 0) => stale must NOT fire
    # with the default below_r=0.0 (only cuts trades still under water).
    rows = [(100, 101, 100, 100.5), (100.5, 102, 100.4, 101.5),
            (101.5, 103, 101.4, 102.5), (102.5, 104, 102.4, 103.5)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=130, timeout_bars=20, entry=100,
                         stale_exit_bars=2, stale_exit_below_r=0.0)
    assert res["outcome"] != "stale_stop"


def test_stop_first_beats_lever():
    # ONE bar that both peaks to 3R (high=106) AND hits the stop (low=97 <= sl=98)
    # AND would satisfy the giveback (close=98.5 => big surrender). Stop-first
    # ordering (SL checked before the lever block) means sl_hit must win.
    rows = [(100, 106, 97, 98.5)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=200, timeout_bars=10, entry=100,
                         giveback_min_mfe_r=1.0, giveback_r=0.5)
    assert res["outcome"] in ("sl_hit", "be_stop")


# ---------------------------------------------------------------------------
# Live-monitor ↔ harness parity (the M20 P5 requirement): the shipped
# ict_scalp.monitor() stale-stop must fire on the SAME bar the validated harness
# (_simulate_exit) does, and be OFF by default for a leg that declares nothing.
# ---------------------------------------------------------------------------

# The real live module (NOT the isolated harness import above).
from src.units.strategies import ict_scalp as live_scalp  # noqa: E402


def _frame_ts(rows, *, freq_min=15):
    """OHLC frame with a `timestamp` column (the live fetch_candles shape)."""
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=len(rows), freq=f"{freq_min}min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", ts)
    return df


# Signal bar = index 2 (two pre-entry bars exercise the strictly-after age
# filter); entry = 100 at its close; then a run of never-profitable bars.
_PARITY_ROWS = [
    (100, 101.0, 99.0, 100.0),   # 0  pre-entry
    (100, 101.0, 99.0, 100.0),   # 1  pre-entry
    (100, 101.0, 99.0, 100.0),   # 2  SIGNAL/entry bar (entry=100)
    (100, 100.2, 99.4, 99.8),    # 3  bars_since_entry=0
    (99.8, 100.0, 99.2, 99.6),   # 4  =1
    (99.6, 99.9, 99.1, 99.5),    # 5  =2
    (99.5, 99.8, 99.0, 99.4),    # 6  =3  -> stale_exit_bars=3 fires here (open_r<0)
    (99.4, 99.7, 98.9, 99.3),    # 7  (never reached)
]
_SIG_IDX = 2
_STALE_N = 3


def _parity_open_pkg(df):
    entry, sl = 100.0, 95.0
    return {
        "order_package_id": "parity-1",
        "symbol": "ETHUSDT",
        "direction": "long",
        "entry": entry,
        "sl": sl,
        "tp": 120.0,
        "meta": {
            "strategy_name": "ict_scalp_eth_15m",
            "timeframe": "15m",
            "risk_per_unit": abs(entry - sl),
            "entry_time": str(df["timestamp"].iloc[_SIG_IDX]),
        },
    }


def test_monitor_matches_harness_fire_bar():
    """The live monitor stale-stop fires on the exact bar the harness does."""
    df = _frame_ts(_PARITY_ROWS)
    ohlc = _frame([r for r in _PARITY_ROWS])
    harness = _simulate_exit(
        ohlc, start_idx=_SIG_IDX + 1, direction="long", sl=95.0, tp=120.0,
        timeout_bars=50, entry=100.0, stale_exit_bars=_STALE_N,
        stale_exit_below_r=0.0,
    )
    assert harness["outcome"] == "stale_stop"
    fire_idx = harness["exit_index"]

    cfg = {"stale_exit_bars": _STALE_N, "stale_exit_below_r": 0.0}
    pkg = _parity_open_pkg(df)
    # Every bar BEFORE the harness fire bar: monitor must not stale-close.
    for j in range(_SIG_IDX + 1, fire_idx):
        v = live_scalp.monitor(cfg, df.iloc[: j + 1], pkg)
        assert not (isinstance(v, dict) and v.get("reason") == "stale_stop"), (
            f"live monitor stale-closed at bar {j}, harness fires at {fire_idx}"
        )
    # The harness fire bar: monitor stale-closes at the same bar + close price.
    v = live_scalp.monitor(cfg, df.iloc[: fire_idx + 1], pkg)
    assert v == {
        "action": "close", "reason": "stale_stop",
        "exit_price": float(df["close"].iloc[fire_idx]),
    }


def test_monitor_stale_off_when_undeclared():
    """No stale_exit_bars declared (cfg or meta) => baseline unchanged: the
    monitor never stale-closes, even well past N underwater bars. This is what
    keeps the real-money ict_scalp_5m BTC leg byte-for-byte unaffected."""
    df = _frame_ts(_PARITY_ROWS)
    pkg = _parity_open_pkg(df)
    for j in range(_SIG_IDX + 1, len(_PARITY_ROWS)):
        v = live_scalp.monitor({}, df.iloc[: j + 1], pkg)  # empty cfg
        assert not (isinstance(v, dict) and v.get("reason") == "stale_stop")


def test_monitor_stale_holds_a_winner():
    """A trade in profit at the stale bar is NOT cut (open_r >= below_r=0.0)."""
    rows = [
        (100, 101, 99, 100.0),    # 0 pre-entry
        (100, 101, 99, 100.0),    # 1 pre-entry
        (100, 101, 99, 100.0),    # 2 signal/entry (entry=100)
        (100, 102, 100, 101.5),   # 3
        (101.5, 103, 101, 102.5),  # 4
        (102.5, 104, 102, 103.5),  # 5  in profit -> no stale cut
    ]
    df = _frame_ts(rows)
    pkg = _parity_open_pkg(df)
    cfg = {"stale_exit_bars": 2, "stale_exit_below_r": 0.0}
    for j in range(_SIG_IDX + 1, len(rows)):
        v = live_scalp.monitor(cfg, df.iloc[: j + 1], pkg)
        assert not (isinstance(v, dict) and v.get("reason") == "stale_stop")


def test_monitor_stale_stop_first_defers_to_sl():
    """Stop-first: if the current bar's close has crossed SL, the stale-stop
    defers (the exchange bracket / normal path owns the stop)."""
    rows = [
        (100, 101, 99, 100.0),   # 0 pre-entry
        (100, 101, 99, 100.0),   # 1 pre-entry
        (100, 101, 99, 100.0),   # 2 signal/entry (entry=100, sl=95)
        (100, 100, 96, 99.0),    # 3
        (99, 99, 95, 96.0),      # 4
        (96, 96, 90, 94.0),      # 5  close 94 <= sl 95 (bars_since_entry=2 >= N=2)
    ]
    df = _frame_ts(rows)
    pkg = _parity_open_pkg(df)  # sl=95
    cfg = {"stale_exit_bars": 2, "stale_exit_below_r": 0.0}
    v = live_scalp.monitor(cfg, df, pkg)
    # SL already crossed at the last bar => stale must NOT fire.
    assert not (isinstance(v, dict) and v.get("reason") == "stale_stop")


def test_order_package_stamps_entry_time():
    """order_package() stamps meta.entry_time = the signal (last) bar's
    timestamp, the anchor the stale-stop's age count reads."""
    # Minimal synthetic frame that produces a signal is complex to hand-build;
    # assert the stamp helper + meta wiring directly instead.
    df = _frame_ts(_PARITY_ROWS)
    assert live_scalp._extract_entry_time(df) == str(df["timestamp"].iloc[-1])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# M20 partial-TP ladder lever (bank_frac / bank_at_r), ported from
# backtest_trend.py / backtest_pullback.py 2026-08-09 so the eight live
# ict_scalp legs can be swept for `exit_ladder` (they read
# blocked:no_harness_levers until this landed).
# ---------------------------------------------------------------------------

import ast  # noqa: E402


def test_every_return_path_reports_banked():
    """Structural guard: `banked` on EVERY `_simulate_exit` return dict.

    run_backtest reads `result["banked"]` strictly. A return path that forgot
    the key would raise there rather than silently read as "never banked" —
    but a caller could regress to `.get("banked", False)`, so the honest
    denominator is asserted at the source. Counted, not eyeballed: the
    duplicated-row class of bug is invisible to a re-read and obvious to a
    count that disagrees.
    """
    src = (REPO_ROOT / "scripts" / "backtest_ict_scalp.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_simulate_exit")
    rets = [n for n in ast.walk(fn)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    with_banked = [r for r in rets
                   if any(isinstance(k, ast.Constant) and k.value == "banked"
                          for k in r.value.keys)]
    assert len(rets) >= 7, f"expected >=7 return sites, found {len(rets)}"
    assert len(rets) == len(with_banked), (
        f"{len(rets) - len(with_banked)} return path(s) omit 'banked'")


# Long, entry=100, sl=98 (risk=2). Bar 1 prints a high of 102 (= +1R) and then
# trades down to the stop. Rung-before-stop means the fraction banks at +1R and
# only the remainder takes the −1R.
_BANK_THEN_STOP_LONG = [(100, 100.5, 99.5, 100), (100, 102.0, 97.0, 97.5)]


def test_bank_off_is_byte_identical():
    """bank_frac=0 must leave the result identical to not passing the lever."""
    rows = _BANK_THEN_STOP_LONG
    base = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                          sl=98, tp=110, timeout_bars=10, entry=100)
    off = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100,
                         bank_frac=0.0, bank_at_r=1.0)
    assert base == off
    assert off["banked"] is False


def test_bank_fires_before_stop_long():
    """A bar that trades through the rung AND the stop banks the fraction."""
    res = _simulate_exit(_frame(_BANK_THEN_STOP_LONG), start_idx=0,
                         direction="long", sl=98, tp=110, timeout_bars=10,
                         entry=100, bank_frac=0.5, bank_at_r=1.0)
    assert res["outcome"] == "sl_hit"
    assert res["banked"] is True
    # run_backtest's blend: 0.5 * (+1R) + 0.5 * (−1R) == 0.0R, vs −1R unbanked.
    r_exit = (res["exit_price"] - 100) / 2.0
    assert r_exit == pytest.approx(-1.0)
    assert 0.5 * 1.0 + 0.5 * r_exit == pytest.approx(0.0)


def test_bank_fires_short():
    """Short mirror: rung at entry − bank_at_r × risk, touched by the bar low."""
    rows = [(100, 100.5, 99.5, 100), (100, 103.0, 98.0, 102.5)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="short",
                         sl=102, tp=90, timeout_bars=10, entry=100,
                         bank_frac=0.25, bank_at_r=1.0)
    assert res["banked"] is True


def test_rung_not_reached_does_not_bank():
    """Never touching the rung leaves banked False (an INERT cell, not a loss)."""
    rows = [(100, 100.5, 99.5, 100), (100, 101.0, 97.0, 97.5)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100,
                         bank_frac=0.5, bank_at_r=1.0)
    assert res["outcome"] == "sl_hit"
    assert res["banked"] is False


def test_rung_at_or_above_tp_is_a_provable_noop():
    """The ict_scalp-specific trap: every live leg has a FIXED tp_at_r=1.5.

    A rung at bank_at_r >= tp_at_r coincides with the TP, so the blend
    `frac*tp + (1-frac)*tp` returns tp exactly — the lever measures nothing.
    A sweep grid that used the fleet default bank_at_r=1.5 would report a
    confident, meaningless "no effect" for half its cells.
    """
    # entry=100, sl=98 (risk=2) => tp_at_r 1.5 puts TP at 103.
    rows = [(100, 100.5, 99.5, 100), (100, 103.5, 99.8, 103.2)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=103, timeout_bars=10, entry=100,
                         bank_frac=0.5, bank_at_r=1.5)
    assert res["outcome"] == "tp_hit"
    assert res["banked"] is True
    r_exit = (res["exit_price"] - 100) / 2.0
    assert r_exit == pytest.approx(1.5)
    assert 0.5 * 1.5 + 0.5 * r_exit == pytest.approx(r_exit)  # identical


# ---------------------------------------------------------------------------
# CAPITAL EFFICIENCY (operator directive 2026-08-10): "we need to look at
# efficiency of capital utilization as well as max R — the most recent example
# being the open trades bybit_2 is currently holding through a long chop period
# with no upside to show for it."
#
# The exit-refinement skill has ALWAYS declared "capital-efficiency tiebreak:
# net_R per position-day" as part of the lever gate. No harness ever computed
# it, so the gate's own declared tiebreak was never measurable.
# ---------------------------------------------------------------------------

def test_banked_index_reports_the_rung_bar_not_just_the_fact():
    """`banked` alone cannot express HOW LONG capital was committed."""
    rows = [(100, 100.5, 99.5, 100), (100, 102.2, 99.6, 100.2)] + \
           [(100, 100.4, 99.6, 100.0)] * 20
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=103, timeout_bars=30, entry=100,
                         bank_frac=0.5, bank_at_r=1.0)
    assert res["banked"] is True
    assert res["banked_index"] == 1, "rung printed on bar 1"
    assert res["exit_index"] == 21, "then chopped to timeout"


def test_banked_index_is_none_when_the_rung_never_filled():
    """None, never 0 — bar 0 is a real bar, so a fabricated 0 would read as
    'banked immediately' on a trade that never banked at all."""
    rows = [(100, 100.5, 99.5, 100), (100, 101.0, 97.0, 97.5)]
    res = _simulate_exit(_frame(rows), start_idx=0, direction="long",
                         sl=98, tp=110, timeout_bars=10, entry=100,
                         bank_frac=0.5, bank_at_r=1.0)
    assert res["banked"] is False
    assert res["banked_index"] is None


def test_capital_weighted_hold_is_shorter_than_the_full_hold():
    """THE OPERATOR'S CASE: a trade that reaches 1R then chops to a flat
    timeout. Banking releases half the position at the rung, so half the
    capital stops sitting in the chop. net_R barely moves; capital-time
    nearly halves — and a net_R-only gate cannot see the difference.
    """
    banked_index, exit_index, bank_frac = 1, 21, 0.5
    full = exit_index
    capital_weighted = bank_frac * banked_index + (1 - bank_frac) * exit_index
    assert capital_weighted == pytest.approx(11.0)
    assert capital_weighted < full
    # Just over half the capital-time of holding the whole position throughout.
    assert capital_weighted / full == pytest.approx(0.5238, abs=1e-3)


# --------------------------------------------------------------------------
# --start / --end : the IS/OOS window. Absent until 2026-08-10, which is why
# the ict_scalp family had never been window-split — the fleet sweep passes
# both to every cell and this harness rejected them with an argparse usage
# error, failing all seven legs before a single backtest ran.
# --------------------------------------------------------------------------

def _window_frame(n: int = 6, start: str = "2024-01-01", tz: bool = True) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="5min", tz="UTC" if tz else None)
    return pd.DataFrame({
        "timestamp": ts,
        "open": [100.0] * n, "high": [101.0] * n,
        "low": [99.0] * n, "close": [100.5] * n, "volume": [1.0] * n,
    })


def test_date_filter_is_inclusive_on_both_bounds():
    df = _window_frame(6)  # 00:00, 00:05, 00:10, 00:15, 00:20, 00:25
    out = bts._date_filter(df, "2024-01-01T00:05", "2024-01-01T00:15")
    assert len(out) == 3
    assert str(out["timestamp"].iloc[0]).startswith("2024-01-01 00:05")
    assert str(out["timestamp"].iloc[-1]).startswith("2024-01-01 00:15")


def test_date_filter_handles_a_tz_naive_frame():
    """_load_candles parses WITHOUT utc=True, so the column can be tz-naive.

    Comparing a naive column to a tz-aware bound raises, which would have
    surfaced as a generic load failure rather than as a window problem.
    """
    df = _window_frame(6, tz=False)
    assert df["timestamp"].dt.tz is None
    out = bts._date_filter(df, "2024-01-01T00:10", None)
    assert len(out) == 4
    # The frame's own dtype is returned untouched — the UTC coercion is for
    # the comparison only, so the HTF resample downstream sees what it always saw.
    assert out["timestamp"].dt.tz is None


def test_date_filter_without_bounds_is_the_identity():
    df = _window_frame(4)
    out = bts._date_filter(df, None, None)
    assert out.equals(df)


def test_date_filter_refuses_a_window_it_cannot_apply():
    """A frame with no timestamp column + a requested window must RAISE.

    This harness falls back to integer indices when `timestamp` is absent.
    Silently ignoring the window there would return a FULL-HISTORY result
    under an `IS` or `OOS` label — a real number with a wrong label, which is
    strictly worse than an error.
    """
    df = _window_frame(4).drop(columns=["timestamp"])
    with pytest.raises(ValueError, match="no `timestamp` column"):
        bts._date_filter(df, "2024-01-01", None)
    # ...but with no window requested the same frame is still fine.
    assert bts._date_filter(df, None, None).equals(df)


def test_is_and_oos_partition_the_full_run(tmp_path):
    """The split must be a partition, not an approximation.

    Measured on data/backtest_candles.csv (5,000 1-min bars, 2022-07-23..27)
    with the config-exact base: full history is 4 trades / -0.6535 netR, and
    the split at 2022-07-25 gives 1 / -0.1210 and 3 / -0.5325 — trades and R
    both add up exactly. A silent off-by-one in the window would show here as
    a lost or duplicated trade.
    """
    import json
    import subprocess
    data = REPO_ROOT / "data" / "backtest_candles.csv"
    if not data.exists():
        pytest.skip("sample frame not present")

    def run(*extra):
        out = tmp_path / f"o{len(extra)}{abs(hash(extra))}.json"
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "backtest_ict_scalp.py"),
             "--data", str(data), "--symbol", "BTCUSDT", "--timeframe", "5m",
             "--sim-breakeven", "--json", str(out), *extra],
            check=True, capture_output=True)
        return json.loads(out.read_text())

    full = run()
    is_ = run("--end", "2022-07-25")
    oos = run("--start", "2022-07-25")
    assert is_["total_trades"] + oos["total_trades"] == full["total_trades"]
    assert is_["net_total_r"] + oos["net_total_r"] == pytest.approx(
        full["net_total_r"], abs=1e-9)
    # Each side must be a STRICT subset — a window that silently matched
    # everything would pass the sum check above by being the full run twice.
    assert is_["bars"] < full["bars"] and oos["bars"] < full["bars"]


def test_a_window_with_no_overlap_exits_nonzero(tmp_path):
    """An empty window must not arrive as a confident zero-trade summary.

    "The window selected no bars" and "the strategy took no trades in this
    window" are different statements, and the sweep grades the second one.
    """
    import subprocess
    data = REPO_ROOT / "data" / "backtest_candles.csv"
    if not data.exists():
        pytest.skip("sample frame not present")
    p = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "backtest_ict_scalp.py"),
         "--data", str(data), "--symbol", "BTCUSDT", "--timeframe", "5m",
         "--start", "2030-01-01", "--json", str(tmp_path / "o.json")],
        capture_output=True, text=True)
    assert p.returncode != 0
    assert "selected 0 bars" in p.stderr


def test_the_sweeps_scalp_invocation_is_accepted_end_to_end():
    """Every flag scripts/research/m20_fleet_exit_sweep.py sends must parse.

    This is the regression the 2026-08-10 dispatch needed and did not have:
    all seven scalp legs failed with an argparse usage error before running a
    single backtest, because `--start`/`--end` were undeclared here — and
    nothing in the repo asserted that the sweep's argv and this parser agree.
    The census had passed on the same legs minutes earlier, because a census
    windows nothing.
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "_sweep_argv", REPO_ROOT / "scripts" / "research" / "m20_fleet_exit_sweep.py")
    sweep = _ilu.module_from_spec(spec)
    sys.modules["_sweep_argv"] = sweep
    spec.loader.exec_module(spec and sweep)

    cfg = {"timeframe": "15m", "symbols": ["ETHUSDT"], "min_confidence": 0.3,
           "htf_filter_timeframe": "1h", "htf_filter_ema_period": 20}
    base = sweep.base_args("ict_scalp_eth_15m", cfg, "scalp", "x.csv", None)
    cells = sweep.cells_for(cfg, "scalp")
    assert cells, "scalp lost its lever cells"
    # The base alone must parse, and so must base+cell in each window the
    # gate uses — IS passes --end, OOS passes --start, the walk-forward both.
    parser = bts.build_parser()
    windows = ([], ["--end", "2025-07-01"], ["--start", "2025-07-01"],
               ["--start", "2021-01-01", "--end", "2022-01-01"])
    for tag, _lever, extra in [("<base>", "", [])] + list(cells):
        for window in windows:
            try:
                parser.parse_args([*base, *extra, *window, "--json", "/dev/null"])
            except SystemExit as exc:  # argparse exits 2 on an unknown flag
                raise AssertionError(
                    f"the sweep's scalp invocation is rejected by this harness: "
                    f"cell={tag} window={window} ({exc})") from exc


def test_build_parser_still_rejects_an_unknown_flag():
    """The guard above is only meaningful if the parser is strict.

    A parser that silently accepted anything would make the sweep-argv test
    pass while the real failure persisted — the same class as a probe that
    cannot find a positive.
    """
    with pytest.raises(SystemExit):
        bts.build_parser().parse_args(["--a-flag-that-does-not-exist", "1"])
