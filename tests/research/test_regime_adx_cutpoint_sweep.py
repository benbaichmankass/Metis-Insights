"""RESEARCH-PROGRAM R2 · ADX cut-point sweep — offline logic tests.

No network, no harness, no DB. Exercises the sweep's re-bucketing + verdict path
directly on synthetic trades with hand-set entry-bar ADX, so the fetch/harness
(the only network-touching half) is out of scope here.

  1. ``_regime_at`` buckets one ADX reading under an arbitrary (chop_max, trend_min)
     pair — the parametrization that is the whole point of the sweep — matching
     ``regime_matrix._regime`` at the live 20/25.
  2. ``_entry_adx`` attaches the entry-bar ADX (nearest bar at/just-before entry).
  3. ``_grade_cell`` re-buckets + folds + grades a cell, and the SAME trade set
     grades differently once the cut-points move it out of the target regime — the
     sensitivity R2 exists to surface.
  4. ``_summarize`` reports a verdict as robust only when it holds across the whole
     grid.

scripts/research is not a package, so the module loads via importlib.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESEARCH = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "scripts", "research")


def _load(name: str):
    if _RESEARCH not in sys.path:
        sys.path.insert(0, _RESEARCH)
    spec = importlib.util.spec_from_file_location(name, os.path.join(_RESEARCH, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sweep = _load("regime_adx_cutpoint_sweep")


def test_regime_at_matches_live_and_reparametrizes():
    # At the live cut-points, _regime_at == regime_matrix._regime.
    assert sweep._regime_at(18.0, 20.0, 25.0) == "chop"
    assert sweep._regime_at(22.0, 20.0, 25.0) == "transitional"
    assert sweep._regime_at(30.0, 20.0, 25.0) == "trending"
    assert sweep._regime_at(float("nan"), 20.0, 25.0) == "unknown"
    # The same ADX re-buckets when the cut-points move — a 22 that is
    # "transitional" at 20/25 is "trending" once trend_min drops to 21.
    assert sweep._regime_at(22.0, 20.0, 21.0) == "trending"
    assert sweep._regime_at(22.0, 23.0, 25.0) == "chop"


def test_entry_adx_labels_nearest_prior_bar():
    ts = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    df = pd.DataFrame({"timestamp": ts})
    adx = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    trades = [
        {"entry_time": "2024-01-01T02:30:00Z", "direction": "long", "net_r": 1.0},
        {"entry_time": "not-a-date", "direction": "short", "net_r": -1.0},
    ]
    out = sweep._entry_adx(trades, adx, df)
    # unparseable entry dropped; the 02:30 trade takes the 02:00 bar's ADX (30.0).
    assert len(out) == 1
    assert out[0]["_adx"] == 30.0


def _adx_trades(entries):
    # entries: list of (adx, direction, net_r, day) -> adx_trades rows
    rows = []
    for i, (a, d, r, day) in enumerate(entries):
        rows.append({"_adx": a, "direction": d, "net_r": r,
                     "entry_time": f"2024-{day:02d}-01T00:00:00Z"})
    return rows


def test_grade_cell_flips_when_cutpoints_move_trades_out(tmp_path):
    # 12 short trades, all negative, ADX ~30 (trending), spread across 12 months so
    # every fold count in the panel gets trades -> short_stable_drag at 20/25.
    entries = [(30.0, "short", -1.0, m) for m in range(1, 13)]
    adx_trades = _adx_trades(entries)

    live = sweep._grade_cell(adx_trades, "trending", 20.0, 25.0, str(tmp_path))
    assert live["regime_trades"] == 12
    assert live["short_stable_drag"] is True

    # Raise trend_min above the trades' ADX -> they are "transitional", so the
    # trending cell empties: the verdict is cut-point-dependent, R2's whole point.
    moved = sweep._grade_cell(adx_trades, "trending", 20.0, 35.0, str(tmp_path))
    assert moved["regime_trades"] == 0
    assert "short_stable_drag" not in moved


def test_summarize_flags_robust_vs_fragile():
    robust = [
        {"short_stable_drag": True, "long_stable_drag": False, "is_live_cutpoint": True},
        {"short_stable_drag": True, "long_stable_drag": False, "is_live_cutpoint": False},
    ]
    s = sweep._summarize(robust, "trending")
    assert s["gradeable_cut_points"] == 2
    assert s["short_verdict_robust"] is True
    assert s["live_cutpoint_short_stable_drag"] is True

    fragile = [
        {"short_stable_drag": True, "long_stable_drag": False, "is_live_cutpoint": True},
        {"short_stable_drag": False, "long_stable_drag": False, "is_live_cutpoint": False},
    ]
    s2 = sweep._summarize(fragile, "trending")
    assert s2["short_verdict_robust"] is False
