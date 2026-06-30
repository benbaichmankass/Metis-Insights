"""Tests for the M18 c_ml-conditioned scorer-probe features.

Covers the additions in `scripts/research/allocator_candidate_dataset.py` +
`allocator_ranker_eval.py`: the canonical regime label, the leakage-safe
per-(owner,regime) historical-expectancy feature, and the ranker-eval wiring
(cell-feat imputation + regime one-hot). The single most important invariant —
**the per-cell expectancy read at a candidate's OPEN reflects only PRIOR closes,
never the candidate's own or any future outcome** — is asserted directly.
"""
from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

cand_ds = pytest.importorskip("scripts.research.allocator_candidate_dataset")
ranker = pytest.importorskip("scripts.research.allocator_ranker_eval")


def _fake_state(n: int = 260, *, strong_up: bool = True, high_vol: bool = True):
    """A minimal _SymState-shaped namespace: high/low/close np arrays + ts."""
    rng = np.linspace(0.0, 1.0, n)
    base = 100.0 * (1.0 + (0.20 if strong_up else 0.0) * rng)  # +20% over the window
    amp = 0.02 if high_vol else 0.001
    close = base * (1.0 + amp * np.sin(np.arange(n)))
    high = close * (1.0 + amp)
    low = close * (1.0 - amp)
    ts = pd.Series(pd.date_range("2024-01-01", periods=n, freq="5min"))
    return types.SimpleNamespace(close=close, high=high, low=low, ts=ts)


def _fake_cand(owner="trend_donchian", side="long", entry=110.0, sl=109.0, tp=112.0):
    return types.SimpleNamespace(
        symbol="BTC", owner=owner, side=side, confidence=0.6, ev_r=0.1,
        entry=entry, sl=sl, tp=tp, meta={})


def test_regime_label_is_canonical_and_populated():
    st = _fake_state(strong_up=True, high_vol=True)
    reg = cand_ds._regime_at(st, 200, window=48)
    # classify_regime: +20% over the full series -> the trailing 48-bar window is
    # a clear up-move with non-trivial range; never the degenerate "unknown".
    assert reg["regime_trend"] != "unknown"
    assert reg["regime_vol"] != "unknown"


def test_regime_label_unknown_on_short_window():
    st = _fake_state()
    assert cand_ds._regime_at(st, 3, window=48) == {
        "regime_trend": "unknown", "regime_vol": "unknown"}


def test_cell_expectancy_is_leakage_free():
    """The read at OPEN must see only PRIOR closes — never this candidate's own
    outcome. Mirrors collect_dataset's contract: read in _features, fold at close."""
    st = _fake_state()
    cand = _fake_cand()
    cell_stats: dict = {}

    # First candidate in its cell: no prior history.
    f1 = cand_ds._features(st, 200, cand, 48, cell_stats)
    assert f1["cell_hist_n"] == 0
    assert f1["cell_hist_mean_r"] is None
    assert f1["cell_hist_winrate"] is None

    # Simulate collect_dataset folding f1's realized close (net_r=+2, a win) AFTER
    # the row was emitted — exactly the close-time update.
    key = cand_ds._cell_key(f1["owner"], f1)
    cell_stats[key] = {"n": 1.0, "sum_r": 2.0, "wins": 1.0}

    # Next candidate in the SAME cell now sees exactly that one prior close.
    f2 = cand_ds._features(st, 200, cand, 48, cell_stats)
    assert f2["cell_hist_n"] == 1
    assert f2["cell_hist_mean_r"] == pytest.approx(2.0)
    assert f2["cell_hist_winrate"] == pytest.approx(1.0)


def test_cell_key_keys_on_owner_and_regime():
    reg = {"regime_trend": "strong-up", "regime_vol": "high"}
    assert cand_ds._cell_key("trend_donchian", reg) == "trend_donchian|strong-up|high"
    # Different regime -> different cell (the whole point of regime-conditioning).
    reg2 = {"regime_trend": "sideways", "regime_vol": "low"}
    assert cand_ds._cell_key("trend_donchian", reg2) != cand_ds._cell_key("trend_donchian", reg)


def test_features_deterministic():
    st = _fake_state()
    cand = _fake_cand()
    a = cand_ds._features(st, 200, cand, 48, {})
    b = cand_ds._features(st, 200, cand, 48, {})
    assert a == b


def test_ranker_imputes_missing_cell_feats_not_drop():
    """A row missing cell-expectancy history must NOT be dropped (it imputes 0.0)
    — otherwise every warmup candidate vanishes from the eval."""
    row = {"confidence": "0.6", "ev_r": "0.1", "rr": "2", "stop_dist_pct": "0.01",
           "tp_dist_pct": "0.02", "ret_1h": "0.001", "ret_4h": "0.002",
           "ret_12h": "0.003", "vol_1h": "0.004", "mom_align_1h": "1",
           "hour_utc": "13", "dow": "2",
           "cell_hist_mean_r": "", "cell_hist_winrate": "", "cell_hist_n": "0",
           "regime_trend": "strong-up", "regime_vol": "high"}
    x = ranker._row_features(row, ranker._MARKET_FEATS, [], False,
                             cell_feats=ranker._CELL_FEATS)
    assert x is not None  # not dropped
    # last 3 entries are the cell feats; missing mean_r/winrate imputed to 0.0
    assert x[-3:] == [0.0, 0.0, 0.0]


def test_ranker_regime_one_hot():
    row = {"confidence": "0.6", "ev_r": "0.1", "rr": "2", "stop_dist_pct": "0.01",
           "tp_dist_pct": "0.02", "ret_1h": "0.001", "ret_4h": "0.002",
           "ret_12h": "0.003", "vol_1h": "0.004", "mom_align_1h": "1",
           "hour_utc": "13", "dow": "2", "regime_trend": "strong-up", "regime_vol": "high"}
    regime_vals = {"regime_trend": ["sideways", "strong-up"], "regime_vol": ["high", "low"]}
    x = ranker._row_features(row, ranker._MARKET_FEATS, [], False, regime_vals=regime_vals)
    # 4 one-hot slots appended; exactly the matching trend + vol fire.
    assert x[-4:] == [0.0, 1.0, 1.0, 0.0]
