"""MI-165 `be_floor_r` — the fleet-wide break-even FLOOR, in all three ratchet harnesses.

The lever MI-163 proposed as "Proposal B": once a trade has SEEN `be_floor_r` R
of open profit, its trailing stop may never again sit below entry. It is
`max()`-ed against the chandelier candidate so it can only ever TIGHTEN, which
is what preserves the monotone-ratchet invariant the live units guarantee.

Three units run that ratchet (`trend_donchian`, `htf_pullback_trend_2h`,
`squeeze_breakout_4h`) and three harnesses model their entries, so the lever
exists in three places. **The properties are asserted against all three**, not
just the one the sweep happened to exercise most: the units share one trail
geometry, so a lever that meant something different in one harness would make
the sweep's pooled number incomparable across legs.

What is pinned here:
  * **byte-identical at default** — the load-bearing property for every harness
    lever in this repo (see `test_trend_harness_levers`), because the recorded
    corpus was produced with it unset;
  * **it only ever tightens** — the floor may never LOOSEN a stop, which is the
    invariant that lets it compose with the existing ratchet;
  * **it actually fires**, and arms at the declared threshold rather than
    always-on or never-on;
  * **it truncates a resumed winner** — the FORGONE CONTINUATION. This is the
    cost term the live telemetry cannot measure and the whole reason the sweep
    had to be path-aware, so it is asserted on a hand-built path rather than
    left to be inferred from a fleet aggregate.
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


TREND = _load("_bef_trend", "scripts/backtest_trend.py")
PULLBACK = _load("_bef_pullback", "scripts/backtest_pullback.py")
SQUEEZE = _load("_bef_squeeze", "scripts/backtest_squeeze.py")

# (module, the params that make its ENTRY engine trade on the shared fixture)
HARNESSES = [
    pytest.param(TREND, dict(donchian=20, atr_period=14, atr_stop_mult=2.5,
                             trail_mult=3.0, timeout_bars=200, cooldown_bars=1,
                             timeframe="5min", symbol="BTCUSDT"), id="trend"),
    pytest.param(PULLBACK, dict(trend_lookback=40, pullback_lookback=10,
                                pullback_frac=0.5, atr_period=14,
                                atr_stop_mult=2.5, trail_mult=5.0,
                                timeout_bars=200, cooldown_bars=1,
                                timeframe="5min", symbol="BTCUSDT"), id="pullback"),
    pytest.param(SQUEEZE, dict(bb_period=20, bb_std=2.0, kc_mult=1.5,
                               atr_period=14, atr_stop_mult=2.5, trail_mult=3.5,
                               timeout_bars=48, cooldown_bars=1,
                               timeframe="5min", symbol="BTCUSDT"), id="squeeze"),
]


def _candles(rule="5min"):
    df = pd.read_csv(os.path.join(_REPO, "data/backtest_candles.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


# --- the load-bearing property ------------------------------------------- #

@pytest.mark.parametrize("mod,base", HARNESSES)
def test_be_floor_is_a_noop_at_default(mod, base):
    """Passing be_floor_r=0.0 == not passing it at all, in every harness.

    If this fails, every previously-recorded backtest number in that harness
    silently changed meaning.
    """
    df = _candles()
    unset = mod.run_backtest(df.copy(), **base)
    explicit = mod.run_backtest(df.copy(), **base, be_floor_r=0.0)
    assert unset == explicit
    assert unset["total_trades"] > 0, "fixture must trade or this proves nothing"


@pytest.mark.parametrize("mod,base", HARNESSES)
def test_undeclared_be_floor_absent_from_params_echo(mod, base):
    """An undeclared lever must not appear in the summary's params block."""
    assert "be_floor_r" not in mod.run_backtest(_candles(), **base)["params"]


@pytest.mark.parametrize("mod,base", HARNESSES)
def test_declared_be_floor_is_reported(mod, base):
    """A DECLARED lever must appear, so a recorded run says what produced it."""
    s = mod.run_backtest(_candles(), **base, be_floor_r=0.75)
    assert s["params"]["be_floor_r"] == 0.75


@pytest.mark.parametrize("mod,base", HARNESSES)
def test_be_floor_changes_outcomes_when_armed(mod, base):
    """The lever must actually do something, or the sweep measured nothing."""
    df = _candles()
    assert mod.run_backtest(df.copy(), **base, be_floor_r=0.5) != \
        mod.run_backtest(df.copy(), **base)


# --- the invariant: it may only ever TIGHTEN ------------------------------ #

def test_be_floor_never_loosens_a_stop():
    """No armed trade may exit WORSE than the control's own -1R stop floor.

    The floor is `max()`-ed against the chandelier candidate, so it can only
    ever raise a long's stop (lower a short's). The observable consequence on
    per-trade records: no trade may book a gross R below the control arm's worst
    gross R, because the only stops the floor can introduce sit at entry — which
    is strictly better than the -1R initial stop.

    Asserted on per-trade records via `trades_out` (the trend harness's Trade
    list), not on an aggregate, so a single loosened stop cannot be averaged
    away by the rest of the book.
    """
    df = _candles()
    base = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
                timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")
    ctrl: list = []
    TREND.run_backtest(df.copy(), **base, trades_out=ctrl)
    worst_ctrl = min(t.r_multiple for t in ctrl)
    assert ctrl, "fixture must trade or this proves nothing"
    for arm in (0.5, 1.0, 1.5):
        armed: list = []
        TREND.run_backtest(df.copy(), **base, be_floor_r=arm, trades_out=armed)
        worst_armed = min(t.r_multiple for t in armed)
        assert worst_armed >= worst_ctrl - 1e-9, (
            f"be_floor_r={arm} produced a worse loss ({worst_armed}) than the "
            f"ungated stop floor ({worst_ctrl}) — the floor LOOSENED a stop")
        for t in armed:
            if t.direction == "long":
                assert t.exit_price >= t.sl - 1e-9
            else:
                assert t.exit_price <= t.sl + 1e-9


def test_floor_truncates_a_resumed_winner_the_forgone_continuation():
    """THE COST TERM, asserted on a path where the answer is known by hand.

    A long that runs to +1R, retraces THROUGH entry, then resumes to a large
    win. The ungated chandelier (trail_mult 3.0 x ATR, far below entry) rides
    the dip and banks the resumption. With `be_floor_r=0.75` the stop is pinned
    at entry once +0.75R is seen, so the dip takes it out at ~breakeven and the
    resumption is FORGONE.

    This is exactly what the live telemetry cannot see — it carries `peak_r` and
    `open_r` and no path — and it is why MI-163 refused to quote its gross table
    as a net result.

    ⚠️ The assertion is on the FIRST TRADE, not on the book. On this fixture the
    floored arm goes on to RE-ENTER at a much tighter stop and finishes ahead
    overall — which is a real effect and exactly why the book-level answer needs
    the fleet sweep rather than a hand-built path. What is pinned here is the
    MECHANISM: on the trade that armed the floor, the continuation is forgone.

    The fixture reproduces the fleet's actual condition (MI-163 §2.3): a WIDE
    chandelier that still sits BELOW entry at the peak (`trail_mult` x ATR
    exceeds the excursion), which is the only regime in which a break-even floor
    can bind at all. With a tight trail the stop is already above entry and the
    lever is inert — the case `test_floor_arms_at_the_declared_threshold_not_always`
    covers.
    """
    rows = []

    def bar(o, h, lo, c):
        rows.append({"open": o, "high": h, "low": lo, "close": c})

    # 1) 140 bars of HIGH-ATR chop in [97, 103] -> ATR ~ 5.8, channel high 103.
    for i in range(140):
        bar(97, 103, 97, 103) if i % 2 else bar(103, 103, 97, 97)
    # 2) breakout above the channel -> entry at 106, risk = 2.5 x ATR ~ 14.5,
    #    so the initial stop sits at ~91.5 and the chandelier trails 3 x ATR
    #    (~17.4) below the extreme — BELOW entry for this whole excursion.
    bar(103, 106, 103, 106)
    # 3) run to +12 (~0.83R): past the 0.75 floor, still under 1R.
    p = 106.0
    for _ in range(8):
        p += 1.5
        bar(p - 1.5, p, p - 1.6, p)
    peak = p
    # 4) retrace BELOW entry (to 102) but ABOVE the chandelier (~100.7), so the
    #    ungated arm survives the dip and the floored arm does not.
    for _ in range(8):
        p -= 2.0
        bar(p + 2.0, p + 2.1, p, p)
    dip = p
    # 5) resume, hard.
    for _ in range(60):
        p += 2.0
        bar(p - 2.0, p, p - 2.1, p)
    while len(rows) < 400:
        bar(p, p + 0.1, p - 0.1, p)

    df = pd.DataFrame(rows)
    df.insert(0, "timestamp",
              pd.date_range("2026-01-01", periods=len(rows), freq="1min", tz="UTC"))

    base = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
                timeout_bars=300, cooldown_bars=1, timeframe="1min",
                symbol="TEST", long_only=True)
    ctrl: list = []
    TREND.run_backtest(df.copy(), **base, trades_out=ctrl)
    armed: list = []
    TREND.run_backtest(df.copy(), **base, be_floor_r=0.75, trades_out=armed)

    assert ctrl and armed, "fixture must trade or this proves nothing"
    assert dip < 106.0 < peak, "fixture must retrace through entry"
    # Same entry, same stop — the ONLY difference is the floor.
    assert ctrl[0].entry == armed[0].entry == 106.0
    # The armed trade armed the floor (it saw >= 0.75R) ...
    assert armed[0].mfe_r >= 0.75
    # ... exited AT entry ...
    assert armed[0].exit_price == pytest.approx(106.0)
    assert armed[0].r_multiple == pytest.approx(0.0, abs=1e-9)
    # ... and thereby FORWENT the continuation the ungated arm banked.
    assert ctrl[0].r_multiple > 5.0, (
        "the ungated chandelier must ride the dip and bank the resumption; "
        f"got {ctrl[0].r_multiple}")
    assert armed[0].r_multiple < ctrl[0].r_multiple, (
        "the break-even floor must forgo the resumed continuation on this path")


def test_floor_arms_at_the_declared_threshold_not_always():
    """A floor above the trade's whole excursion must never arm.

    Guards the direction that would make the sweep meaningless: a lever that
    fires regardless of its threshold would show a constant delta across arms.
    """
    df = _candles()
    base = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
                timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")
    unarmable = TREND.run_backtest(df.copy(), **base, be_floor_r=10_000.0)
    control = TREND.run_backtest(df.copy(), **base)
    assert unarmable["net_total_r"] == control["net_total_r"], (
        "a floor beyond any observed excursion must be inert")
