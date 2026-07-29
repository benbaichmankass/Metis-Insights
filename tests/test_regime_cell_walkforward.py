"""rec #5 · walk-forward gate — tests for the regime-cell walk-forward tooling.

Offline + deterministic: no harness run, no network, no DB.
  1. ``regime_tag_emitted.annotate_trades_with_regime`` labels each trade by the
     ADX regime at its entry bar (a synthetic adx Series is passed in directly, so
     the ADX math itself is not under test here — the entry-bar lookup + labelling
     is).
  2. ``regime_cell_walkforward.cell_verdict`` reduces a walk-forward result to the
     per-side OOS-stability verdict — the short-drag gate a Tier-3 OFF-cell draft
     must pass. Fed a hand-built walk-forward dict, so the verdict math is exact.
  3. The two compose through ``direction_walkforward.analyze`` on a synthetic
     regime-filtered trades file — the real driver path minus the fetch/harness.

scripts/research is not a package, so modules load via importlib.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESEARCH = os.path.join(os.path.dirname(_HERE), "scripts", "research")


def _load(name: str):
    # scripts/research modules import each other by bare name off sys.path
    if _RESEARCH not in sys.path:
        sys.path.insert(0, _RESEARCH)
    spec = importlib.util.spec_from_file_location(name, os.path.join(_RESEARCH, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rte = _load("regime_tag_emitted")
dwf = _load("direction_walkforward")
rcwf = _load("regime_cell_walkforward")


def _df(n: int):
    ts = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.0, "low": 1.0,
                         "close": 1.0, "volume": 1.0})


def test_annotate_labels_by_entry_bar_regime():
    # bars 0-1 chop (adx 10), bars 2-4 trending (adx 30)  [thresholds: <20 chop, >=25 trending]
    df = _df(5)
    adx = pd.Series([10.0, 10.0, 30.0, 30.0, 30.0])
    trades = [
        {"entry_time": df["timestamp"].iloc[0].isoformat(), "direction": "long", "net_r": 1.0},
        {"entry_time": df["timestamp"].iloc[2].isoformat(), "direction": "short", "net_r": -1.0},
        {"entry_time": df["timestamp"].iloc[4].isoformat(), "direction": "short", "net_r": -2.0},
    ]
    tagged = rte.annotate_trades_with_regime(trades, adx, df)
    assert [t["regime"] for t in tagged] == ["chop", "trending", "trending"]
    # unparseable entry_time is dropped (no bar to label against)
    dropped = rte.annotate_trades_with_regime(
        [{"entry_time": "not-a-date", "direction": "long", "net_r": 1.0}], adx, df)
    assert dropped == []


def test_only_regime_filter():
    df = _df(4)
    adx = pd.Series([10.0, 10.0, 30.0, 30.0])
    trades = [
        {"entry_time": df["timestamp"].iloc[0].isoformat(), "direction": "long", "net_r": 1.0},
        {"entry_time": df["timestamp"].iloc[3].isoformat(), "direction": "short", "net_r": -1.0},
    ]
    tagged = rte.annotate_trades_with_regime(trades, adx, df)
    trending = [t for t in tagged if t["regime"] == "trending"]
    assert len(trending) == 1 and trending[0]["direction"] == "short"


def test_cell_verdict_short_stable_drag_true():
    # short < 0 in every fold AND pooled short < 0  ->  the OFF-cell gate PASSES
    wf = {
        "folds": 3, "total_trades": 60,
        "by_fold": [
            {"long_n": 5, "long_r": 2.0, "short_n": 5, "short_r": -3.0},
            {"long_n": 5, "long_r": 1.0, "short_n": 5, "short_r": -4.0},
            {"long_n": 5, "long_r": 3.0, "short_n": 5, "short_r": -2.0},
        ],
        "pooled": {"long_r": 6.0, "short_r": -9.0},
    }
    cv = rcwf.cell_verdict(wf, "trending")
    assert cv["short_folds_negative"] == 3
    assert cv["short_stable_drag"] is True
    assert cv["long_stable_drag"] is False  # long is positive throughout


def test_cell_verdict_short_positive_does_not_pass():
    # short > 0 in a majority of folds  ->  NOT a durable drag (regime-of-sample)
    wf = {
        "folds": 3, "total_trades": 74,
        "by_fold": [
            {"long_n": 12, "long_r": 7.9, "short_n": 13, "short_r": -2.6},
            {"long_n": 12, "long_r": -5.4, "short_n": 13, "short_r": 5.5},
            {"long_n": 12, "long_r": -5.7, "short_n": 12, "short_r": 10.8},
        ],
        "pooled": {"long_r": -3.2, "short_r": 13.7},
    }
    cv = rcwf.cell_verdict(wf, "trending")
    assert cv["short_folds_negative"] == 1
    assert cv["short_stable_drag"] is False


def test_compose_through_direction_walkforward(tmp_path):
    # synthetic trending-only trades file: short negative in 3/3 folds, pooled < 0
    p = tmp_path / "trending.jsonl"
    rows = []
    base = pd.Timestamp("2025-01-01", tz="UTC")
    for i in range(30):
        rows.append({"entry_time": (base + pd.Timedelta(hours=i)).isoformat(),
                     "direction": "short", "net_r": -1.0, "regime": "trending"})
        rows.append({"entry_time": (base + pd.Timedelta(hours=i)).isoformat(),
                     "direction": "long", "net_r": 0.5, "regime": "trending"})
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    wf = dwf.analyze([str(p)], 3, "synthetic:trending")
    cv = rcwf.cell_verdict(wf, "trending")
    assert cv["regime_trades"] == 60
    assert cv["short_stable_drag"] is True
    assert cv["long_stable_drag"] is False
