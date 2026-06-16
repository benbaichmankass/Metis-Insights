"""Tests for scripts/ml/sweep_conviction_weights — the v1 weight sweep harness."""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "sweep_conviction_weights",
    Path(__file__).resolve().parents[2] / "scripts" / "ml" / "sweep_conviction_weights.py",
)
sw = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = sw  # dataclass needs the module registered
_SPEC.loader.exec_module(sw)  # type: ignore[union-attr]


def _row(inputs, won, order, source="live"):
    return sw.Row(inputs=inputs, won=won, source=source, order=order)


def test_input_presence_counts():
    rows = [
        _row({"c_strat": 0.5}, True, 0),
        _row({"c_strat": 0.5, "c_wr": 0.7}, False, 1),
        _row({"c_strat": 0.5, "c_setup": 0.6, "c_reg": 0.4}, True, 2),
    ]
    p = sw.input_presence(rows)
    assert p["__total__"] == 3
    assert p["c_strat"] == 3
    assert p["c_wr"] == 1
    assert p["__multi__"] == 2  # rows 1 and 2 carry >=2 inputs


def test_score_weights_perfect_separation():
    # c_wr perfectly separates win/loss; weighting it should give AUC 1.0
    rows = [
        _row({"c_strat": 0.5, "c_wr": 0.9}, True, 0),
        _row({"c_strat": 0.5, "c_wr": 0.8}, True, 1),
        _row({"c_strat": 0.5, "c_wr": 0.2}, False, 2),
        _row({"c_strat": 0.5, "c_wr": 0.1}, False, 3),
    ]
    s = sw.score_weights(rows, {"c_strat": 0.0, "c_setup": 0.0, "c_wr": 1.0, "c_reg": 0.0})
    assert s["auc"] == 1.0


def test_thin_multi_input_keeps_defaults():
    # plenty of c_strat-only rows but no multi-input → not identifiable → keep defaults
    rng = random.Random(0)
    rows = [
        _row({"c_strat": rng.random()}, rng.random() > 0.5, i, source="backtest")
        for i in range(400)
    ]
    rep = sw.run_sweep(rows, n_folds=3)
    assert rep["identifiable"] is False
    assert rep["recommendation"] == "keep_hand_set_defaults"
    assert rep["input_presence"]["__multi__"] == 0


def test_run_sweep_smoke_multi_input():
    rng = random.Random(1)
    rows = []
    for i in range(600):
        cstrat = rng.random()
        cwr = rng.random()
        # outcome driven mostly by c_wr (so a c_wr-heavy weighting should win)
        won = (0.3 * cstrat + 0.7 * cwr) > 0.5
        rows.append(_row({"c_strat": cstrat, "c_wr": cwr}, won, i))
    rep = sw.run_sweep(rows, n_folds=4)
    assert rep["n_rows"] == 600
    assert rep["input_presence"]["__multi__"] == 600
    assert rep["best_candidate"] is not None
    # report is well-formed regardless of the adopt/keep decision
    assert rep["recommendation"] in {"adopt_swept_weights", "keep_hand_set_defaults"}
