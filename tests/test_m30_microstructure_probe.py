"""M30 S0 — tests for the microstructure feasibility probe (no network)."""

from __future__ import annotations

import math
import os
import sys

_MICRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "micro")
sys.path.insert(0, _MICRO)

import microstructure_probe as mp  # noqa: E402


# ---- pure OHLCV parser -----------------------------------------------------

def test_parse_kline_ohlcv_shapes_and_sorts():
    payload = {"result": {"list": [
        # Bybit returns newest-first; parser sorts ascending by t
        ["1700003600000", "101", "102", "100", "101.5", "12", "1200"],
        ["1700000000000", "100", "101", "99", "100.5", "10", "1000"],
    ]}}
    bars = mp.parse_kline_ohlcv(payload)
    assert [b["t"] for b in bars] == [1700000000000, 1700003600000]  # ascending
    assert bars[0] == {"t": 1700000000000, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 10.0}


def test_parse_kline_ohlcv_skips_malformed_rows():
    payload = {"result": {"list": [
        ["1700000000000", "100", "101", "99", "100.5", "10", "1000"],
        ["bad", "x", "y", "z", "w", "v", "t"],          # non-numeric → skipped
        ["1700003600000", "101", "102", "100"],          # too short → skipped
    ]}}
    bars = mp.parse_kline_ohlcv(payload)
    assert len(bars) == 1 and bars[0]["c"] == 100.5


# ---- pure feature functions ------------------------------------------------

def test_log_returns_and_guards():
    r = mp.log_returns([100.0, 110.0, 121.0])
    assert math.isclose(r[0], math.log(1.1), rel_tol=1e-9)
    assert math.isclose(r[1], math.log(1.1), rel_tol=1e-9)
    assert mp.log_returns([100.0, 0.0, 50.0]) == [0.0, 0.0]   # non-positive close → 0.0, stays aligned


def test_realized_vol_and_term_structure():
    rets = [0.01, -0.01, 0.02, -0.02, 0.03, -0.03]
    assert mp.realized_vol([], 5) is None                     # < 2 points
    rv = mp.realized_vol(rets, 6)
    assert rv is not None and rv > 0
    # a recently-expanding tail makes short RV > long RV → ratio > 1
    rets_expanding = [0.001, 0.001, 0.001, 0.05, -0.05, 0.05]
    assert mp.rv_term_structure(rets_expanding, 3, 6) > 1.0


def test_ret_autocorr_detects_alternation():
    # a strictly alternating series has strong NEGATIVE lag-1 autocorr
    alt = [0.02, -0.02] * 10
    ac = mp.ret_autocorr_lag1(alt, 20)
    assert ac is not None and ac < -0.8
    assert mp.ret_autocorr_lag1([0.0, 0.0, 0.0], 3) is None    # zero variance → None


def test_range_position_and_volume_z():
    assert mp.range_position({"h": 10.0, "l": 8.0, "c": 9.5}) == 0.75
    assert mp.range_position({"h": 5.0, "l": 5.0, "c": 5.0}) is None   # zero range
    # a volume spike on the last bar is a strong positive z
    z = mp.volume_zscore([10, 10, 10, 10, 100], 5)
    assert z is not None and z > 1.5


def test_forward_return_bounds():
    closes = [100.0, 110.0, 121.0]
    assert math.isclose(mp.forward_return(closes, 0, 2), math.log(1.21), rel_tol=1e-9)
    assert mp.forward_return(closes, 2, 1) is None            # out of range
    assert mp.forward_return([100.0, 0.0], 0, 1) is None      # non-positive fwd close


def test_pearson_ic_recovers_a_planted_positive_relationship():
    # feature x, target y = 0.5x + small noise → strong positive IC, significant t
    xs = [i * 0.1 - 1.0 for i in range(40)]
    pairs = [(x, 0.5 * x + (0.001 if i % 2 else -0.001)) for i, x in enumerate(xs)]
    ic = mp.pearson_ic(pairs)
    assert ic["n"] == 40 and ic["ic"] > 0.9 and ic["ic_t"] > 2.0


def test_pearson_ic_thin_or_constant_is_none():
    assert mp.pearson_ic([(1, 2), (3, 4)]) is None            # < 5 pairs
    assert mp.pearson_ic([(1, 5)] * 8) is None                # zero variance both sides


# ---- panel + S0 report -----------------------------------------------------

def _synthetic_bars(n=260, seed_scale=0.01):
    """A deterministic OHLCV series with a MOMENTUM structure: each bar's return
    carries a fraction of the previous return (positive lag-1 autocorr) so the panel
    is non-degenerate and a first-look IC exists. No RNG (Date/random are unavailable)."""
    bars = []
    c = 100.0
    prev_ret = 0.0
    t0 = 1_700_000_000_000
    for i in range(n):
        # deterministic pseudo-oscillation + momentum carry
        base = seed_scale * math.sin(i * 0.7)
        ret = 0.5 * prev_ret + base
        prev_ret = ret
        new_c = c * math.exp(ret)
        hi = max(c, new_c) * (1 + 0.002)
        lo = min(c, new_c) * (1 - 0.002)
        vol = 1000.0 * (1 + 0.5 * math.sin(i * 0.3))
        bars.append({"t": t0 + i * 3_600_000, "o": c, "h": hi, "l": lo, "c": new_c, "v": vol})
        c = new_c
    return bars


def test_build_feature_panel_is_pit_and_populated():
    bars = _synthetic_bars(60)
    panel = mp.build_feature_panel(bars)
    assert len(panel) == 60
    # bar 0 can carry range_position (bar-local) but not the trailing-window features
    assert panel[0]["realized_vol"] is None and panel[0]["rv_term_structure"] is None
    # deep into the series the trailing-window features are populated
    assert panel[-1]["realized_vol"] is not None
    assert panel[-1]["ret_autocorr_lag1"] is not None
    assert panel[-1]["range_position"] is not None


def test_s0_report_structure_and_feasibility():
    bars = _synthetic_bars(260)
    rep = mp.s0_report("BTCUSDT", bars, horizons=(1, 2, 4), min_bars=200)
    assert rep["symbol"] == "BTCUSDT" and rep["n_bars"] == 260
    assert rep["data_ok"] is True                               # >= min_bars, span > 0
    assert set(rep["features"]) == set(mp.FEATURES)
    # at least the trailing-window features are non-degenerate on this series
    assert "realized_vol" in rep["non_degenerate_features"]
    # each feature carries a first_look_ic entry per requested horizon
    for f in mp.FEATURES:
        assert set(rep["features"][f]["first_look_ic"]) == {"1", "2", "4"}


def test_s0_report_flags_thin_data_not_ok():
    bars = _synthetic_bars(50)
    rep = mp.s0_report("SOLUSDT", bars, min_bars=200)
    assert rep["data_ok"] is False                              # below the feasibility floor


# ---- S2 honest non-overlapping IC -----------------------------------------

def test_rank_handles_ties():
    assert mp.rank([10.0, 30.0, 20.0]) == [0.0, 2.0, 1.0]       # distinct → 0,1,2 by value
    assert mp.rank([5.0, 5.0, 9.0]) == [0.5, 0.5, 2.0]          # tie → mean rank


def test_spearman_ic_is_rank_pearson():
    # a monotone-but-nonlinear relationship → Spearman ≈ 1 even though Pearson < 1
    pairs = [(x, x ** 3) for x in range(-6, 7)]
    ic = mp.spearman_ic(pairs)
    assert ic is not None and ic["ic"] > 0.99


def test_s2_row_is_nonoverlapping():
    # the core honesty of S2: subsample at stride=H so windows are DISJOINT, so the
    # t-stat's N is ~n/H (the honest effective sample), not the overlap-inflated ~n.
    bars = _synthetic_bars(600)
    row8 = mp.s2_nonoverlap_row(bars, "realized_vol", 8)
    row1 = mp.s2_nonoverlap_row(bars, "realized_vol", 1)
    assert 60 <= row8["n_nonoverlap"] <= 80          # ~600/8 disjoint windows, NOT ~600
    assert row1["n_nonoverlap"] > 400                # H=1 is already disjoint (adjacent bars)
    # both directional + magnitude ICs are computed and reported
    assert set(row8) >= {"ic_signed_demeaned", "ic_signed_t", "ic_magnitude", "ic_magnitude_t"}


def test_s2_thin_data_is_no_data():
    bars = _synthetic_bars(20)
    out = mp.s2_scan(bars, "realized_vol", (8, 16), t_flag=2.0)
    assert out["verdict"] == "no_data" and out["has_directional_edge"] is False


# The discrimination logic (directional edge vs magnitude-only artifact vs no-edge) is
# the whole point of S2, so test it deterministically by controlling the per-horizon
# rows — not by fighting synthetic-data artifacts.

def _stub_rows(mp_mod, monkeypatch, rows_by_horizon):
    def fake(bars, feature, horizon, **kw):
        return {**{"horizon": horizon, "n_nonoverlap": 100, "feature": feature,
                   "ic_signed_demeaned": None, "ic_signed_t": None,
                   "ic_magnitude": None, "ic_magnitude_t": None},
                **rows_by_horizon[horizon]}
    monkeypatch.setattr(mp_mod, "s2_nonoverlap_row", fake)


def test_s2_verdict_directional_edge(monkeypatch):
    _stub_rows(mp, monkeypatch, {
        4: {"ic_signed_demeaned": 0.15, "ic_signed_t": 2.5, "ic_magnitude": 0.05, "ic_magnitude_t": 0.8},
    })
    out = mp.s2_scan([{}], "realized_vol", (4,), t_flag=2.0)
    assert out["has_directional_edge"] is True and out["verdict"] == "directional_edge"
    assert out["best_directional"]["ic_signed_t"] == 2.5


def test_s2_verdict_magnitude_only_artifact(monkeypatch):
    # significant magnitude IC, insignificant directional IC → the artifact, NOT an edge
    _stub_rows(mp, monkeypatch, {
        4: {"ic_signed_demeaned": 0.02, "ic_signed_t": 0.4, "ic_magnitude": 0.30, "ic_magnitude_t": 5.6},
    })
    out = mp.s2_scan([{}], "realized_vol", (4,), t_flag=2.0)
    assert out["has_directional_edge"] is False
    assert out["verdict"] == "magnitude_only_no_direction"
    assert out["magnitude_only_horizons"] == [4]


def test_s2_verdict_no_edge(monkeypatch):
    _stub_rows(mp, monkeypatch, {
        4: {"ic_signed_demeaned": 0.01, "ic_signed_t": 0.2, "ic_magnitude": 0.01, "ic_magnitude_t": 0.3},
    })
    out = mp.s2_scan([{}], "realized_vol", (4,), t_flag=2.0)
    assert out["has_directional_edge"] is False and out["verdict"] == "no_edge"
