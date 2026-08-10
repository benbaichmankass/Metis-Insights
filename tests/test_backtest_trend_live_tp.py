"""Live-parity take-profit in the trend harness
(BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP).

Production places `tp = min(entry*(1+0.099), entry + tp_r*risk)` — the 50R
"sentinel" clamped to 9.9% because Bybit rejects a TP beyond ~10%. At
`atr_stop_mult` 2.5 with 2-3% ATR that lands at **1.3-2.0R**: an ordinary,
frequently-touched target. `scripts/backtest_trend.py` had NO take-profit exit
path at all — its only outcomes were trail_stop / stale_stop / giveback_stop /
timeout — so every trail-family exit verdict in M20 was measured on a book that
cannot take profit, against a live book that does.

Three properties, in order of how badly getting them wrong would mislead:

  1. **Default off is byte-identical.** 266 coverage cells were graded with no
     TP. If enabling the flag were the default, the whole fleet's history would
     silently re-base and no A/B would be possible.
  2. **SL-first survives.** The harness's conservative intrabar convention is
     that a bar trading through both levels takes the STOP. A TP checked before
     the stop would manufacture winners out of losers — the single most
     flattering bug available here.
  3. **The clamp is what binds.** `min(cap, tp_r*risk)` — with tp_r at 50R the
     percentage cap is the operative one, which is the entire point.

Requires pandas (the harness does); CI provides it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The behavioural tests need pandas (the harnesses do). The STRUCTURAL ones --
# signature defaults and SL-first ordering -- read source and must NOT be
# skipped with them: they check the property whose failure is most expensive
# (a TP checked before the stop turns losers into winners), so deferring them
# to CI would leave the riskiest claim unverified locally.
try:
    import pandas as pd  # noqa: F401
    HAVE_PANDAS = True
except ImportError:
    HAVE_PANDAS = False

needs_pandas = pytest.mark.skipif(not HAVE_PANDAS, reason="harness requires pandas; CI provides it")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load("backtest_trend") if HAVE_PANDAS else None
bp = _load("backtest_pullback") if HAVE_PANDAS else None
HARNESSES = ("backtest_trend", "backtest_pullback", "backtest_squeeze")


def _frame(bars):
    """bars: list of (o, h, l, c). 1h spacing, volume constant."""
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(bars), freq="1h", tz="UTC"),
        "open": [b[0] for b in bars], "high": [b[1] for b in bars],
        "low": [b[2] for b in bars], "close": [b[3] for b in bars],
        "volume": [1000.0] * len(bars),
    })


def _outcomes(res):
    return (res.get("by_outcome") or {})


# run_backtest has six REQUIRED keyword-only strategy params. Omitting them
# raised TypeError before any assertion ran — the first version of these tests
# could never have passed, and calling that "CI-verified" was wrong.
TREND_CFG = dict(donchian=20, atr_period=14, atr_stop_mult=2.0, trail_mult=4.0,
                 timeout_bars=200, cooldown_bars=0,
                 timeframe="1h", symbol="BTCUSDT")


def test_tp_price_is_the_clamp_not_the_50R_sentinel():
    """min(entry*1.099, entry + 50*risk): with any sane risk the CAP binds.
    If tp_r bound instead, the TP would sit ~50R away and never fire — which is
    exactly the behaviour this change exists to stop modelling."""
    entry, risk = 100.0, 1.0          # 1% risk -> 50R sentinel = 150.0
    capped = min(entry * 1.099, entry + 50.0 * risk)
    assert capped == pytest.approx(109.9)      # the cap, not 150.0
    # ...and in R that is a perfectly ordinary target:
    assert (capped - entry) / risk == pytest.approx(9.9)


@needs_pandas
def test_default_off_emits_no_take_profit_outcome():
    """The 266 already-graded cells must stay reproducible."""
    bars = [(100, 101, 99, 100)] * 30 + [(100, 140, 99, 139)] * 10 + [(139, 140, 100, 101)] * 30
    res = bt.run_backtest(_frame(bars), **TREND_CFG)
    assert "take_profit" not in _outcomes(res), \
        "a TP outcome appeared with the lever off — prior verdicts are no longer reproducible"


@needs_pandas
def test_stop_wins_when_one_bar_trades_through_both():
    """SL-first is the harness's conservative convention. A bar spanning both
    levels must take the STOP; checking TP first would mint fake winners."""
    # Long setup, then a single bar whose range covers both the trail and a
    # +10% target. Whatever the entry, that bar must not resolve as take_profit.
    bars = ([(100, 101, 99, 100)] * 30
            + [(100, 103, 99.5, 102)] * 3          # breakout / entry region
            + [(102, 130, 80, 85)]                  # spans TP (~112) AND the stop
            + [(85, 86, 84, 85)] * 10)
    res = bt.run_backtest(_frame(bars), **TREND_CFG, tp_cap_pct=0.099)
    out = _outcomes(res)
    if res.get("total_trades"):
        assert out.get("take_profit", 0) == 0, (
            "a bar trading through both levels resolved as take_profit — "
            f"SL-first was broken. outcomes={out}")


@needs_pandas
def test_enabling_the_cap_changes_the_book():
    """The whole premise: with a reachable TP the book must differ from the
    no-TP baseline. An inert flag would make the capped-vs-uncapped A/B
    meaningless.

    The bar sequence is load-bearing and was found by RUNNING, not reasoning.
    Two earlier attempts failed for reasons worth recording: the first peaked at
    112 against a TP of ~112.1 (a hair short), and the second put the run-up
    BEFORE the donchian breakout confirmed, so entry landed on the last rising
    bar with mfe_r 0.0 and stopped out immediately. The climb has to happen
    AFTER entry.
    """
    bars = [(100, 100.6, 99.4, 100)] * 30 + [(100, 102, 99.8, 101.8)]
    px = 101.8
    for _ in range(14):                       # sustained climb past +9.9%
        nxt = px * 1.025
        bars.append((px, nxt, px * 0.998, nxt))
        px = nxt
    bars += [(px, px, px * 0.80, px * 0.82)] * 3      # then hand it back
    bars += [(px * 0.82, px * 0.82, px * 0.80, px * 0.82)] * 8

    base = bt.run_backtest(_frame(bars), **TREND_CFG)
    capped = bt.run_backtest(_frame(bars), **TREND_CFG, tp_cap_pct=0.099)

    # The uncapped book cannot take profit at all...
    assert "take_profit" not in _outcomes(base)
    # ...the capped one does, and the result differs.
    assert "take_profit" in _outcomes(capped), (
        f"the live TP never fired: {_outcomes(capped)}")
    assert capped.get("net_total_r") != base.get("net_total_r"), (
        "enabling the live TP changed nothing — the flag is inert")
    # And the measurement lands in the range quoted to the operator, MEASURED
    # rather than assumed from an ATR% guess.
    assert capped.get("tp_r_effective_median") is not None
    assert base.get("tp_r_effective_median") is None, (
        "reach must be None when the lever is off — unmeasured and "
        "zero-distance are opposite statements")


# ------------------------------------------------ the port must not diverge
# Both read SOURCE, so they run everywhere -- including a sandbox without
# pandas, where the behavioural tests above cannot.
@pytest.mark.parametrize("name", HARNESSES)
def test_both_harnesses_expose_the_same_lever(name):
    """One live TP geometry, two harnesses. If the port drifts, a cross-family
    capped-vs-uncapped A/B silently compares two different exits -- the same
    class of defect the trend-engine convergence guard exists for."""
    import ast
    tree = ast.parse((REPO / "scripts" / f"{name}.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_backtest")
    kwonly = {a.arg: d for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults)}
    plain = {a.arg for a in fn.args.args}
    assert "tp_cap_pct" in kwonly or "tp_cap_pct" in plain, f"{name} lacks the lever"
    assert "tp_r" in kwonly or "tp_r" in plain, f"{name} lacks tp_r"
    d = kwonly.get("tp_cap_pct")
    assert d is not None and getattr(d, "value", None) == 0.0, (
        f"{name} does not default the live TP OFF -- enabling it by default "
        "silently re-bases all 266 previously-graded coverage cells")


@pytest.mark.parametrize("name", HARNESSES)
def test_tp_is_checked_after_the_stop_in_source(name):
    """Structural check on the ORDER of the two intrabar branches. A behavioural
    test can pass on data that never spans both levels; this cannot."""
    src = (REPO / "scripts" / f"{name}.py").read_text()
    for branch in ("bl <= trail", "bh >= trail"):
        i_stop = src.find(branch)
        assert i_stop != -1, f"{name}: stop branch {branch!r} not found"
        window = src[i_stop:i_stop + 700]
        i_tp = window.find("take_profit")
        assert i_tp != -1, f"{name}: no take_profit exit near the {branch!r} stop"
        i_break = window.find("break")
        assert i_break < i_tp, (
            f"{name}: TP is checked BEFORE the stop breaks on {branch!r} -- "
            "SL-first is broken and losers become winners")
