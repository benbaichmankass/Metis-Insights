"""MOMENTUM-TRAIL-LEVER semantics on synthetic series.

`momentum_trail` (--trail-adx-*) and `momentum_stale` (--momentum-stale-*) read
Wilder ADX, not price path. The default-off contract (byte-identical to the
pre-lever harnesses) is pinned by `test_momentum_stop_golden.py`; this file pins
what the levers DO when armed, on tapes and ADX series built by hand.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.research import trail_levers as tl  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(_REPO / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TREND = _load("_mom_lv_trend", "scripts/backtest_trend.py")
PULLBACK = _load("_mom_lv_pullback", "scripts/backtest_pullback.py")
GOLD = _load("_mom_lv_golden", "tests/test_momentum_stop_golden.py")


def S(*v):
    return pd.Series(list(v), dtype=float)


def mult(adx, j, base=6.0, **kw):
    a = dict(adx_below=0.0, adx_falling_bars=0, adx_tight_mult=2.0)
    a.update(kw)
    return tl.effective_trail_mult(
        base, 0.0, 0, False, 0.0, 0, 0.0, False, None, j, 0.0, 0.0, 0.0,
        True, adx, a["adx_below"], a["adx_falling_bars"], a["adx_tight_mult"])


# --- the shared rule --------------------------------------------------------
def test_defaults_leave_the_mult_untouched():
    # adx_on False (the default) ignores every momentum argument.
    assert tl.effective_trail_mult(
        6.0, 0.0, 0, False, 0.0, 0, 0.0, False, None, 0, 0.0, 0.0, 0.0) == 6.0
    assert tl.effective_trail_mult(
        6.0, 0.0, 0, False, 0.0, 0, 0.0, False, None, 0, 0.0, 0.0, 0.0,
        False, S(1.0), 100.0, 3, 2.0) == 6.0


def test_level_only_fires_below_threshold():
    adx = S(30, 25, 19, 40)
    assert [mult(adx, j, adx_below=20) for j in range(4)] == [6.0, 6.0, 2.0, 6.0]


def test_threshold_zero_and_no_falling_is_never_armed():
    assert not tl.momentum_trail_armed(0.0, 0, 2.0)
    assert not tl.momentum_trail_armed(20.0, 0, 0.0)          # no tight mult
    assert tl.momentum_trail_armed(20.0, 0, 2.0)
    assert tl.momentum_trail_armed(0.0, 3, 2.0)               # falling-only
    assert mult(S(1.0, 1.0), 1) == 6.0                         # nothing declared


def test_falling_requires_strictly_lower_than_k_bars_ago():
    rising = S(10, 12, 14, 16, 18, 20)
    falling = S(30, 28, 26, 24, 22, 20)
    flat = S(20, 20, 20, 20, 20, 20)
    assert all(mult(rising, j, adx_falling_bars=3) == 6.0 for j in range(6))
    assert all(mult(flat, j, adx_falling_bars=3) == 6.0 for j in range(6))
    # fires on every bar with k bars of history, inert during warm-up
    assert [mult(falling, j, adx_falling_bars=3) for j in range(6)] == \
           [6.0, 6.0, 6.0, 2.0, 2.0, 2.0]


def test_level_and_falling_are_anded():
    adx = S(40, 35, 30, 25, 21, 15)
    # ADX<20 only at j=5, falling-3 true from j=3: AND fires only at j=5
    got = [mult(adx, j, adx_below=20, adx_falling_bars=3) for j in range(6)]
    assert got == [6.0, 6.0, 6.0, 6.0, 6.0, 2.0]


def test_nan_adx_is_inert():
    adx = S(float("nan"), float("nan"), 10.0)
    assert mult(adx, 0, adx_below=20) == 6.0
    assert mult(adx, 2, adx_below=20, adx_falling_bars=2) == 6.0   # ref is NaN


def test_never_loosens_a_tighter_base():
    assert mult(S(5.0), 0, base=1.5, adx_below=20, adx_tight_mult=2.0) == 1.5


def test_composes_with_vol_trail_by_minimum():
    atr_pctl = pd.Series([0.95])
    f = lambda t_adx, t_vol: tl.effective_trail_mult(  # noqa: E731
        6.0, 0.0, 0, False, 0.0, 0, 0.0, True, atr_pctl, 0, 0.9, 0.0, t_vol,
        True, S(5.0), 20.0, 0, t_adx)
    assert f(2.0, 3.0) == 2.0
    assert f(3.0, 2.0) == 2.0


def test_stale_fires_only_after_n_bars_with_decayed_adx():
    adx = S(30, 28, 26, 24, 22, 40)
    fire = lambda j, entry=0, n=3, a=25.0: tl.momentum_stale_fires(adx, j, entry, n, a)  # noqa: E731
    assert not fire(2)            # fewer than N bars in
    assert fire(3)                # 3 bars in, 24 < 30 and 24 < 25
    assert not fire(3, a=20.0)    # not under the bound
    assert not fire(5)            # ADX rose back above entry
    assert tl.momentum_stale_armed(8, 20.0)
    assert not tl.momentum_stale_armed(8, 0.0)
    assert not tl.momentum_stale_armed(0, 20.0)


def test_stale_ignores_open_r_by_construction():
    import inspect
    assert "open_r" not in inspect.signature(tl.momentum_stale_fires).parameters


def test_flag_refusals():
    ok = tl.momentum_flag_errors(3.0, 20.0, 0, 1.5, 8, 20.0)
    assert ok == []
    assert tl.momentum_flag_errors(3.0, 0.0, 0, 1.5, 0, 0.0)          # tight, no cond
    assert tl.momentum_flag_errors(3.0, 20.0, 0, 0.0, 0, 0.0)         # cond, no tight
    assert tl.momentum_flag_errors(3.0, 20.0, 0, 3.0, 0, 0.0)         # tight >= trail
    assert tl.momentum_flag_errors(3.0, 0.0, 0, 0.0, 8, 0.0)          # half stale
    assert tl.momentum_flag_errors(3.0, 0.0, 0, 0.0, 0, 20.0)


# --- the harnesses ----------------------------------------------------------
def _run(kind, tmp_path, extra=None, name="x"):
    case = "trend_trail_only" if kind == "trend" else "pullback_trail_only"
    return GOLD.run_case(case, tmp_path, extra=extra)


@pytest.mark.parametrize("kind", ["trend", "pullback"])
def test_always_on_tighten_equals_a_constant_tighter_trail(kind, tmp_path):
    """A=100 (every bar fires, ADX<100 always), T<trail_mult ⇒ identical to a run
    with --trail-mult T, exits AND entries (memo § 4.1 golden test 3)."""
    base_tm = 3.0 if kind == "trend" else 5.0
    T = 1.5
    _, rows_a = _run_dir(kind, tmp_path / "a", dict(trail_adx_below=100.0,
                                                    trail_adx_tight_mult=T))
    const, rows_c = _run_dir(kind, tmp_path / "c", dict(trail_mult=T))
    assert rows_c, "constant-trail run must trade (non-vacuous)"
    strip = lambda rows: [{k: v for k, v in r.items()  # noqa: E731
                           if k not in ("trail_adx_fired_bars", "trail_adx_tightened",
                                        "adx_entry_bar", "adx_at_exit")} for r in rows]
    assert strip(rows_a) == rows_c
    # and it is a real change from the unarmed run
    _, rows_0 = _run_dir(kind, tmp_path / "0", {})
    assert rows_0 != rows_c
    assert base_tm > T


def _run_dir(kind, d, extra):
    d.mkdir(parents=True, exist_ok=True)
    return _run(kind, d, extra)


@pytest.mark.parametrize("kind", ["trend", "pullback"])
def test_armed_with_no_tightening_changes_nothing(kind, tmp_path):
    """A=100 with T == trail_mult fires every bar but tightens nothing (§ 4.1 test 2)."""
    tm = 3.0 if kind == "trend" else 5.0
    _, rows_0 = _run_dir(kind, tmp_path / "0", {})
    _, rows_a = _run_dir(kind, tmp_path / "a", dict(
        trail_adx_below=100.0, trail_adx_tight_mult=tm))
    drop = ("trail_adx_fired_bars", "trail_adx_tightened", "adx_entry_bar", "adx_at_exit")
    assert rows_0
    assert [{k: v for k, v in r.items() if k not in drop} for r in rows_a] == rows_0


@pytest.mark.parametrize("kind", ["trend", "pullback"])
def test_entries_are_untouched_by_exit_levers(kind, tmp_path):
    _, rows_0 = _run_dir(kind, tmp_path / "0", {})
    for extra in (dict(trail_adx_below=25.0, trail_adx_tight_mult=1.5),
                  dict(momentum_stale_bars=4, momentum_stale_adx_below=60.0)):
        _, rows = _run_dir(kind, tmp_path / str(len(extra)) / str(sorted(extra)), extra)
        assert rows
        first = {r["entry_time"] for r in rows_0}
        # exits move the cooldown, so later entries may shift; the FIRST trade's
        # entry is decided before any lever can act and must not move.
        assert rows[0]["entry_time"] == rows_0[0]["entry_time"]
        assert rows[0]["entry"] == rows_0[0]["entry"] and first


@pytest.mark.parametrize("kind", ["trend", "pullback"])
def test_momentum_stale_fires_and_is_attributed(kind, tmp_path):
    _, rows_0 = _run_dir(kind, tmp_path / "0", {})
    summ, rows = _run_dir(kind, tmp_path / "s", dict(
        momentum_stale_bars=2, momentum_stale_adx_below=100.0))
    stale = [r for r in rows if r["exit_reason"] == "momentum_stale"]
    assert stale, "A=100/N=2 must close some trades on a real tape"
    assert all(r["adx_at_exit"] is not None and r["adx_entry_bar"] is not None
               and r["adx_at_exit"] < r["adx_entry_bar"] for r in stale)
    assert summ["params"]["momentum_stale_bars"] == 2
    # the default-run rows never carry the attribution keys
    assert all("adx_at_exit" not in r for r in rows_0)


@pytest.mark.parametrize("kind", ["trend", "pullback"])
def test_momentum_stale_threshold_zero_is_off(kind, tmp_path):
    _, rows_0 = _run_dir(kind, tmp_path / "0", {})
    _, rows = _run_dir(kind, tmp_path / "z", dict(
        momentum_stale_bars=2, momentum_stale_adx_below=0.0))
    assert rows == rows_0


def test_stale_stops_at_the_first_eligible_bar_on_a_falling_adx(monkeypatch):
    """Memo § 4.2 test 2/3 on a hand tape: strictly-falling ADX ⇒ exit at the
    first bar >= N after entry; strictly-rising ⇒ never."""
    n = 40
    ts = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    # flat, then a breakout and a slow grind up that never hits the stop
    close = [100.0] * 25 + [104 + 0.2 * i for i in range(n - 25)]
    df = pd.DataFrame({"timestamp": ts, "open": close,
                       "high": [c + 0.3 for c in close],
                       "low": [c - 0.3 for c in close], "close": close})
    kw = dict(donchian=10, atr_period=5, atr_stop_mult=2.0, trail_mult=6.0,
              timeout_bars=30, cooldown_bars=0, timeframe="1h", symbol="BTCUSDT",
              long_only=True)

    def run(adx_fn):
        monkeypatch.setattr(TREND, "_adx", lambda d, p: adx_fn(len(d)))
        out = []
        TREND.run_backtest(df.copy(), trades_out=out,
                           momentum_stale_bars=3, momentum_stale_adx_below=100.0,
                           **kw)
        return out

    falling = run(lambda m: pd.Series([50.0 - 0.5 * i for i in range(m)]))
    rising = run(lambda m: pd.Series([10.0 + 0.5 * i for i in range(m)]))
    assert falling and falling[0].outcome == "momentum_stale"
    assert falling[0].exit_index - falling[0].entry_index == 3
    assert not any(t.outcome == "momentum_stale" for t in rising)


def test_cells_for_emits_the_grid_and_skips_an_unsafe_tight_mult():
    sys.path.insert(0, str(_REPO / "scripts" / "research"))
    import m20_fleet_exit_sweep as SW
    cells = SW.cells_for({"trail_mult": 3.0}, "donchian")
    mt = [c for c in cells if c[1] == "momentum_trail"]
    ms = [c for c in cells if c[1] == "momentum_stale"]
    assert sorted(t for t, _, _ in mt) == ["adx20_k0_t1.5", "adx20_k3_t1.5",
                                           "adx25_k0_t1.5", "adx25_k3_t1.5"]
    assert sorted(t for t, _, _ in ms) == ["ms12_a20", "ms12_a25", "ms8_a20", "ms8_a25"]
    k3 = dict((t, a) for t, _, a in mt)["adx20_k3_t1.5"]
    assert "--trail-adx-falling-bars" in k3 and "3" in k3
    assert "--trail-adx-falling-bars" not in dict((t, a) for t, _, a in mt)["adx20_k0_t1.5"]
    # not offered on a family whose harness has no ADX
    assert not [c for c in SW.cells_for({"trail_mult": 3.0}, "squeeze")
                if c[1].startswith("momentum")]
    # T would not sit below the leg's own trail ⇒ withheld, with the reason
    skipped: list = []
    cells = SW.cells_for({"trail_mult": 1.5}, "pullback", skipped)
    assert not [c for c in cells if c[1] == "momentum_trail"]
    assert any(s["lever"] == "momentum_trail" for s in skipped)
    # the lever-off arm stays `shipped_*` only (its pinned invariant)
    off = SW.cells_for({"trail_mult": 3.0, "stale_exit_bars": 8,
                        "stale_exit_below_r": 0.0}, "donchian",
                       without_declared_levers=frozenset({"stale_stop"}))
    assert off and all(t.startswith("shipped_") for t, _, _ in off)


def test_cli_refuses_half_declared_levers(capsys):
    for mod in (TREND, PULLBACK):
        rc = mod.main(["prog", "--symbol", "BTCUSDT", "--data", "data/backtest_candles.csv", "--trail-adx-tight-mult", "1.5"])
        assert rc == 2
        assert "momentum_trail is inert" in capsys.readouterr().err
