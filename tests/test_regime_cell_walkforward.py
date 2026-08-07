"""rec #5 · walk-forward gate — tests for the regime-cell walk-forward tooling.

Offline + deterministic: no harness run, no network, no DB.
  1. ``regime_tag_emitted.annotate_trades_with_regime`` labels each trade by the
     ADX regime at its entry bar (a synthetic adx Series is passed in directly, so
     the ADX math itself is not under test here — the entry-bar lookup + labelling
     is).
  2. ``regime_cell_walkforward.cell_verdict`` reduces a FIXED-fold-panel walk-forward
     to the per-side OOS-stability verdict — the short-drag gate a Tier-3 OFF-cell
     draft must pass. The verdict is fold-count invariant
     (BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP): fed a per-fold-count panel of
     hand-built walk-forward dicts, so the verdict math is exact.
  3. The two compose through ``direction_walkforward.analyze`` on a synthetic
     regime-filtered trades file — the real driver path minus the fetch/harness.
  4. The 2-D (trend, vol) cell axis (BL-20260730-WALKFORWARD-NO-VOL-AXIS) refuses
     to grade without vol labels rather than silently fall back to the 1-D
     population.

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


def _wf(short_rs, long_rs, pooled_short, pooled_long):
    """Build a single-fold-count walk-forward dict from per-fold short/long net-R."""
    by_fold = [
        {"long_n": 5, "long_r": lr, "short_n": 5, "short_r": sr}
        for sr, lr in zip(short_rs, long_rs)
    ]
    return {
        "folds": len(short_rs), "total_trades": 10 * len(short_rs),
        "by_fold": by_fold, "pooled": {"short_r": pooled_short, "long_r": pooled_long},
    }


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
    # short < 0 in every fold under EVERY panel fold count AND pooled short < 0
    #   -> the OFF-cell gate PASSES, and is not fold-sensitive.
    panel = {
        3: _wf([-3.0, -4.0, -2.0], [2.0, 1.0, 3.0], -9.0, 6.0),
        4: _wf([-3.0, -4.0, -2.0, -1.0], [2.0, 1.0, 3.0, 0.5], -10.0, 6.5),
        5: _wf([-3.0, -4.0, -2.0, -1.0, -2.0], [2.0, 1.0, 3.0, 0.5, 1.0], -12.0, 7.5),
    }
    cv = rcwf.cell_verdict(panel, "trending")
    assert cv["short_stable_drag"] is True
    assert cv["short_fold_sensitive"] is False
    assert cv["long_stable_drag"] is False  # long is positive throughout
    assert cv["fold_panel"] == [3, 4, 5]


def test_cell_verdict_short_positive_does_not_pass():
    # short > 0 in a majority of folds  ->  NOT a durable drag (regime-of-sample)
    panel = {
        3: _wf([-2.6, 5.5, 10.8], [7.9, -5.4, -5.7], 13.7, -3.2),
        4: _wf([-2.6, 5.5, 10.8, 4.0], [7.9, -5.4, -5.7, 1.0], 17.7, -2.2),
        5: _wf([-2.6, 5.5, 10.8, 4.0, 3.0], [7.9, -5.4, -5.7, 1.0, 0.5], 20.7, -1.7),
    }
    cv = rcwf.cell_verdict(panel, "trending")
    assert cv["short_stable_drag"] is False


def test_cell_verdict_fold_count_invariant_regression():
    """BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP: the old strict-majority-of-folds test
    (`neg > folds/2`) FLIPPED the verdict on the fold count at identical pooled
    net-R — a 4-fold 2/4 read FALSE while a 3-fold 2/3 read TRUE. Now the verdict is
    computed over the FIXED panel and disagreement across fold counts CANNOT produce
    a PASS; it is instead flagged as fold-sensitive."""
    # pooled short < 0 in all, but the per-fold-count majority disagrees:
    #   k=3: 2/3 negative -> majority True ; k=4: 2/4 -> majority False ; k=5: 3/5 -> True
    panel = {
        3: _wf([-3.0, -3.0, 2.5], [1.0, 1.0, 1.0], -3.5, 3.0),
        4: _wf([-3.0, -3.0, 2.5, 2.5], [1.0, 1.0, 1.0, 1.0], -1.0, 4.0),
        5: _wf([-3.0, -3.0, -3.0, 2.5, 2.5], [1.0, 1.0, 1.0, 1.0, 1.0], -4.0, 5.0),
    }
    cv = rcwf.cell_verdict(panel, "trending")
    # The flip scenario must NOT pass, and must be flagged as fold-sensitive.
    assert cv["short_stable_drag"] is False
    assert cv["short_fold_sensitive"] is True
    # The verdict is a pure function of the fixed panel — reordering the caller's
    # display folds cannot change it (there is no --folds input to cell_verdict).
    assert cv == rcwf.cell_verdict(dict(sorted(panel.items(), reverse=True)), "trending")


def test_compose_through_direction_walkforward(tmp_path):
    # synthetic trending-only trades file: short negative in every fold, pooled < 0
    p = tmp_path / "trending.jsonl"
    rows = []
    base = pd.Timestamp("2025-01-01", tz="UTC")
    for i in range(30):
        rows.append({"entry_time": (base + pd.Timedelta(hours=i)).isoformat(),
                     "direction": "short", "net_r": -1.0, "regime": "trending"})
        rows.append({"entry_time": (base + pd.Timedelta(hours=i)).isoformat(),
                     "direction": "long", "net_r": 0.5, "regime": "trending"})
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    panel = {k: dwf.analyze([str(p)], k, "synthetic:trending") for k in rcwf.FOLD_PANEL}
    cv = rcwf.cell_verdict(panel, "trending")
    assert cv["regime_trades"] == 60
    assert cv["short_stable_drag"] is True
    assert cv["long_stable_drag"] is False


def test_run_cell_2d_cell_requires_vol_labels(monkeypatch):
    """A 2-D (trend, vol) cell must refuse to grade without vol labels rather than
    silently fall back to the 1-D trend population (BL-20260730-WALKFORWARD-NO-VOL-AXIS)."""
    monkeypatch.setattr(rcwf.rdm, "load_roster",
                        lambda: {"x_pullback_1h": {"symbols": ["BTCUSDT"], "timeframe": "1h"}})
    out = rcwf.run_cell("x_pullback_1h", "trending", 4, "/tmp/rcwf_test", 730,
                        vol="volatile", vol_labels=None)
    assert "error" in out and "vol-labels" in out["error"]
    assert out.get("cell") == "trending/volatile"


def test_main_vol_without_labels_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv",
                        ["prog", "--strategy", "x", "--regime", "trending", "--vol", "volatile"])
    rc = rcwf.main()
    assert rc == 2
    assert "requires --vol-labels" in capsys.readouterr().err


# --- MIN_DIRECTION_TRADES: the sample floor on the Tier-3 evidence gate ------
# Measured 2026-08-07: `*_stable_drag` had no sample floor, so the PASS/FAIL at
# the n where the six authored `trend_vol` cells live (9, 9, 30, 43) was driven
# by how many folds the trades happened to spread across rather than by the
# strength of the evidence. See regime_cell_walkforward.MIN_DIRECTION_TRADES.


def _wf_n(long_rs, short_rs, long_ns, short_ns, pooled_long, pooled_short):
    """Walk-forward dict with EXPLICIT per-fold trade counts (the floor's input)."""
    by_fold = [
        {"long_n": ln, "long_r": lr, "short_n": sn, "short_r": sr}
        for lr, sr, ln, sn in zip(long_rs, short_rs, long_ns, short_ns)
    ]
    return {"by_fold": by_fold, "total_trades": sum(long_ns) + sum(short_ns),
            "pooled": {"long_r": pooled_long, "short_r": pooled_short,
                       "long_n": sum(long_ns), "short_n": sum(short_ns)}}


def test_three_losing_trades_no_longer_pass_the_gate():
    """THE REGRESSION. Three losing long trades, one per fold, satisfied
    'pooled < 0 AND strict majority-negative under every panel member' and
    reported `long_stable_drag=True, long_fold_sensitive=False` — a Tier-3 PASS
    over three trades, indistinguishable in the output from one over 300."""
    panel = {
        3: _wf_n([-0.5, -0.5, -0.5], [0, 0, 0], [1, 1, 1], [0, 0, 0], -1.5, 0.0),
        4: _wf_n([-0.5, -0.5, -0.5, 0], [0] * 4, [1, 1, 1, 0], [0] * 4, -1.5, 0.0),
        5: _wf_n([-0.5, -0.5, 0, -0.5, 0], [0] * 5, [1, 1, 0, 1, 0], [0] * 5, -1.5, 0.0),
    }
    cv = rcwf.cell_verdict(panel, "trending", "calm")
    assert cv["long_trades"] == 3
    # The raw majority arithmetic is unchanged — it still reads "majority negative".
    assert all(v["long_majority_negative"] for v in cv["per_fold_count"].values())
    # ...but the verdict no longer launders that into a PASS.
    assert cv["long_stable_drag"] is False
    assert cv["long_insufficient_n"] is True
    assert cv["long_verdict"] == "insufficient_n"
    assert cv["min_direction_trades"] == rcwf.MIN_DIRECTION_TRADES


def test_insufficient_n_is_distinguishable_from_a_measured_negative():
    """`*_stable_drag is False` must never again conflate 'measured, no drag'
    with 'not enough data to say'. Only `*_verdict` separates them."""
    thin = {k: _wf_n([-0.5] * k, [0] * k, [1] * k, [0] * k, -0.5 * k, 0.0)
            for k in (3, 4, 5)}
    # 15+ long trades, genuinely NOT a drag (pooled positive).
    thick = {k: _wf_n([2.0] * k, [0] * k, [5] * k, [0] * k, 2.0 * k, 0.0)
             for k in (3, 4, 5)}
    thin_cv, thick_cv = rcwf.cell_verdict(thin, "trending"), rcwf.cell_verdict(thick, "trending")
    assert thin_cv["long_stable_drag"] is thick_cv["long_stable_drag"] is False
    assert thin_cv["long_verdict"] == "insufficient_n"
    assert thick_cv["long_verdict"] == "no_stable_drag"
    assert thick_cv["long_insufficient_n"] is False


def test_zero_trades_still_reports_no_trades_not_insufficient_n():
    """A direction the cell does not trade at all keeps its own distinct state."""
    panel = {k: _wf_n([0] * k, [-3.0] * k, [0] * k, [5] * k, 0.0, -3.0 * k)
             for k in (3, 4, 5)}
    cv = rcwf.cell_verdict(panel, "trending")
    assert cv["long_trades"] == 0 and cv["long_verdict"] == "no_trades"
    assert cv["short_verdict"] == "stable_drag"  # 15 shorts, all folds negative


def test_populated_folds_are_reported():
    """The majority denominator is the fold COUNT; empty folds dilute it. That is
    the intended conservative reading, but it is only legible if the gap shows."""
    panel = {k: _wf_n([-40.0, -40.0] + [0] * (k - 2), [0] * k,
                      [1, 1] + [0] * (k - 2), [0] * k, -80.0, 0.0)
             for k in (3, 4, 5)}
    cv = rcwf.cell_verdict(panel, "trending")
    assert cv["per_fold_count"][5]["long_populated_folds"] == 2
    assert cv["per_fold_count"][5]["of_folds"] == 5
    # -80R over 2 trades: fails on BOTH the dilution and the sample floor.
    assert cv["long_verdict"] == "insufficient_n"


def test_min_direction_trades_matches_evidence_policy():
    """Pin the floor to the policy that AUTHORED the cells.

    `scripts/ml/walkforward_cell_selection.py::MIN_TRADES` is the meaningful-sample
    threshold the OFF-cells were authored under. Grading a cell on a smaller sample
    than authoring it required is not a re-audit. That module calls parse_args() at
    import time, so the literal is read out of the source with `ast` rather than
    imported — a drift pin, not a runtime dependency.
    """
    import ast

    src = os.path.join(os.path.dirname(_HERE), "scripts", "ml",
                       "walkforward_cell_selection.py")
    tree = ast.parse(open(src, encoding="utf-8").read())
    found = [n.value.value for n in ast.walk(tree)
             if isinstance(n, ast.Assign)
             and any(getattr(t, "id", None) == "MIN_TRADES" for t in n.targets)
             and isinstance(n.value, ast.Constant)]
    assert found, "MIN_TRADES literal not found in walkforward_cell_selection.py"
    assert rcwf.MIN_DIRECTION_TRADES == found[0], (
        f"the walk-forward gate's floor ({rcwf.MIN_DIRECTION_TRADES}) drifted from "
        f"the evidence policy that authored the cells ({found[0]})")
