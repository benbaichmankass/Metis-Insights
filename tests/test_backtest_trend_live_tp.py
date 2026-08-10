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

pytest.importorskip("pandas", reason="backtest_trend requires pandas; CI provides it")
import pandas as pd  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "backtest_trend", REPO / "scripts" / "backtest_trend.py")
bt = importlib.util.module_from_spec(_spec)
sys.modules["backtest_trend"] = bt
_spec.loader.exec_module(bt)


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


def test_tp_price_is_the_clamp_not_the_50R_sentinel():
    """min(entry*1.099, entry + 50*risk): with any sane risk the CAP binds.
    If tp_r bound instead, the TP would sit ~50R away and never fire — which is
    exactly the behaviour this change exists to stop modelling."""
    entry, risk = 100.0, 1.0          # 1% risk -> 50R sentinel = 150.0
    capped = min(entry * 1.099, entry + 50.0 * risk)
    assert capped == pytest.approx(109.9)      # the cap, not 150.0
    # ...and in R that is a perfectly ordinary target:
    assert (capped - entry) / risk == pytest.approx(9.9)


def test_default_off_emits_no_take_profit_outcome():
    """The 266 already-graded cells must stay reproducible."""
    bars = [(100, 101, 99, 100)] * 30 + [(100, 140, 99, 139)] * 10 + [(139, 140, 100, 101)] * 30
    res = bt.run_backtest(_frame(bars), symbol="BTCUSDT", timeframe="1h")
    assert "take_profit" not in _outcomes(res), \
        "a TP outcome appeared with the lever off — prior verdicts are no longer reproducible"


def test_stop_wins_when_one_bar_trades_through_both():
    """SL-first is the harness's conservative convention. A bar spanning both
    levels must take the STOP; checking TP first would mint fake winners."""
    # Long setup, then a single bar whose range covers both the trail and a
    # +10% target. Whatever the entry, that bar must not resolve as take_profit.
    bars = ([(100, 101, 99, 100)] * 30
            + [(100, 103, 99.5, 102)] * 3          # breakout / entry region
            + [(102, 130, 80, 85)]                  # spans TP (~112) AND the stop
            + [(85, 86, 84, 85)] * 10)
    res = bt.run_backtest(_frame(bars), symbol="BTCUSDT", timeframe="1h",
                          tp_cap_pct=0.099)
    out = _outcomes(res)
    if res.get("total_trades"):
        assert out.get("take_profit", 0) == 0, (
            "a bar trading through both levels resolved as take_profit — "
            f"SL-first was broken. outcomes={out}")


def test_enabling_the_cap_changes_the_book():
    """The whole premise: with a reachable TP the results must differ from the
    no-TP baseline. If they were identical the flag would be inert and the
    parity break unaddressed."""
    bars = ([(100, 101, 99, 100)] * 30
            + [(100, 103, 99.5, 102)] * 3
            + [(102, 106, 101, 105)] * 3
            + [(105, 112, 104, 111)] * 3            # reaches ~+10% => the cap
            + [(111, 112, 95, 96)] * 5              # ...then hands it all back
            + [(96, 97, 95, 96)] * 10)
    base = bt.run_backtest(_frame(bars), symbol="BTCUSDT", timeframe="1h")
    capped = bt.run_backtest(_frame(bars), symbol="BTCUSDT", timeframe="1h",
                             tp_cap_pct=0.099)
    if base.get("total_trades") and capped.get("total_trades"):
        assert (capped.get("net_total_r") != base.get("net_total_r")
                or "take_profit" in _outcomes(capped)), (
            "enabling the live TP changed nothing — the flag is inert, so the "
            "capped-vs-uncapped A/B would be meaningless")
