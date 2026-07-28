"""Unit tests for the M24 decision-time correlated-exposure feature builder
(``src/runtime/allocator_corr.py`` — ``MB-20260629-ALLOC-CORR``)."""
from __future__ import annotations

import math
from types import SimpleNamespace

from src.runtime import allocator_corr as ac


def _seq(fn, n=30):
    return [fn(i) for i in range(n)]


# --- pearson ---------------------------------------------------------------

def test_pearson_perfect_positive():
    a = _seq(lambda i: i)
    b = _seq(lambda i: 2 * i + 1)  # exact affine, positive slope
    assert math.isclose(ac.pearson(a, b), 1.0, abs_tol=1e-9)


def test_pearson_perfect_negative():
    a = _seq(lambda i: i)
    b = _seq(lambda i: -3 * i + 5)
    assert math.isclose(ac.pearson(a, b), -1.0, abs_tol=1e-9)


def test_pearson_zero_variance_is_none():
    a = _seq(lambda i: i)
    b = _seq(lambda i: 7.0)  # constant → undefined correlation
    assert ac.pearson(a, b) is None


def test_pearson_too_few_observations_is_none():
    a = [0.1, 0.2, 0.3]
    b = [0.2, 0.4, 0.6]
    assert ac.pearson(a, b, min_obs=20) is None
    # ...but a low floor lets the same clean pair through.
    assert ac.pearson(a, b, min_obs=3) is not None


def test_pearson_aligns_on_recent_tail():
    # b is a's affine image but longer; alignment on the last min(len) points
    # must still recover the perfect correlation.
    a = _seq(lambda i: i, n=25)
    b = _seq(lambda i: 4 * i, n=40)
    r = ac.pearson(a, b, min_obs=10)
    assert r is not None and math.isclose(r, 1.0, abs_tol=1e-9)


def test_pearson_drops_non_finite_pairs():
    a = _seq(lambda i: i)
    b = _seq(lambda i: 2 * i)
    a2 = list(a)
    a2[0] = float("nan")  # dropped, correlation still ~1 over the rest
    r = ac.pearson(a2, b, min_obs=10)
    assert r is not None and math.isclose(r, 1.0, abs_tol=1e-9)


def test_pearson_clamped_to_unit_interval():
    a = _seq(lambda i: i)
    b = _seq(lambda i: 1000000.0 * i)
    r = ac.pearson(a, b)
    assert -1.0 <= r <= 1.0


# --- direction sign --------------------------------------------------------

def test_direction_sign_spellings():
    assert ac._direction_sign("long") == 1
    assert ac._direction_sign("BUY") == 1
    assert ac._direction_sign("short") == -1
    assert ac._direction_sign("sell") == -1
    assert ac._direction_sign(-1) == -1
    assert ac._direction_sign("nonsense") is None
    assert ac._direction_sign(None) is None


# --- pairwise_correlations -------------------------------------------------

def test_pairwise_correlations_only_measurable_pairs_sorted_keys():
    rets = {
        "BTCUSDT": _seq(lambda i: i),
        "ETHUSDT": _seq(lambda i: 2 * i),      # perfectly corr with BTC
        "FLAT": _seq(lambda i: 1.0),            # zero variance → no pair
    }
    pw = ac.pairwise_correlations(rets, min_obs=10)
    assert ("BTCUSDT", "ETHUSDT") in pw
    assert math.isclose(pw[("BTCUSDT", "ETHUSDT")], 1.0, abs_tol=1e-9)
    # no pair involving the flat (variance-free) series
    assert all("FLAT" not in k for k in pw)


# --- correlated_exposure ---------------------------------------------------

def test_correlated_exposure_empty_book_is_one_independent_bet():
    out = ac.correlated_exposure(
        candidate_symbol="BTCUSDT",
        candidate_direction="long",
        candidate_risk=100.0,
        open_positions=[],
        returns={},
    )
    assert out["n_book"] == 0
    assert out["effective_independent_bets"] == 1.0
    assert out["corr_weighted_aligned_risk"] == 0.0
    assert out["corr_concentration"] == 0.0
    assert out["max_abs_corr"] is None


def test_correlated_exposure_two_correlated_longs_add_exposure():
    rets = {"BTCUSDT": _seq(lambda i: i), "ETHUSDT": _seq(lambda i: 2 * i)}
    out = ac.correlated_exposure(
        candidate_symbol="BTCUSDT",
        candidate_direction="long",
        candidate_risk=50.0,
        open_positions=[{"symbol": "ETHUSDT", "direction": "long", "risk": 100.0}],
        returns=rets,
        min_obs=10,
    )
    # perfect +corr, same direction → adds |r|*risk = 100 of aligned exposure.
    assert math.isclose(out["max_abs_corr"], 1.0, abs_tol=1e-9)
    assert math.isclose(out["corr_weighted_aligned_risk"], 100.0, abs_tol=1e-9)
    assert math.isclose(out["corr_concentration"], 2.0, abs_tol=1e-9)  # 100/50
    # a perfectly-correlated held symbol adds ~0 independent bets.
    assert math.isclose(out["effective_independent_bets"], 1.0, abs_tol=1e-9)


def test_correlated_exposure_opposite_direction_hedges():
    rets = {"BTCUSDT": _seq(lambda i: i), "ETHUSDT": _seq(lambda i: 2 * i)}
    out = ac.correlated_exposure(
        candidate_symbol="BTCUSDT",
        candidate_direction="long",
        candidate_risk=50.0,
        open_positions=[{"symbol": "ETHUSDT", "direction": "short", "risk": 100.0}],
        returns=rets,
        min_obs=10,
    )
    # +corr but opposite direction → the held position hedges → negative.
    assert out["corr_weighted_aligned_risk"] < 0.0
    assert math.isclose(out["corr_weighted_aligned_risk"], -100.0, abs_tol=1e-9)


def test_correlated_exposure_negative_correlation_diversifies():
    rets = {"BTCUSDT": _seq(lambda i: i), "GLD": _seq(lambda i: -i)}
    out = ac.correlated_exposure(
        candidate_symbol="BTCUSDT",
        candidate_direction="long",
        candidate_risk=50.0,
        open_positions=[{"symbol": "GLD", "direction": "long", "risk": 100.0}],
        returns=rets,
        min_obs=10,
    )
    # same nominal direction but anti-correlated instrument → diversifies (neg).
    # |corr|=1 means the two are mutually predictable, so by the |corr|-based
    # independence metric they collapse toward a single combined bet (~1.0),
    # even though the *directional* exposure they net to is a hedge.
    assert out["corr_weighted_aligned_risk"] < 0.0
    assert math.isclose(out["effective_independent_bets"], 1.0, abs_tol=1e-9)


def test_correlated_exposure_unmeasured_symbol_counts_coverage():
    rets = {"BTCUSDT": _seq(lambda i: i)}  # no returns for the held symbol
    out = ac.correlated_exposure(
        candidate_symbol="BTCUSDT",
        candidate_direction="long",
        candidate_risk=50.0,
        open_positions=[{"symbol": "XRPUSDT", "direction": "long", "risk": 100.0}],
        returns=rets,
        min_obs=10,
    )
    assert out["n_book"] == 1
    assert out["n_book_measured"] == 0
    # nothing measurable → features stay None, never a fabricated 0.
    assert out["max_abs_corr"] is None
    assert out["corr_weighted_aligned_risk"] is None
    assert out["corr_concentration"] is None


def test_correlated_exposure_missing_risk_falls_back_to_count():
    rets = {"BTCUSDT": _seq(lambda i: i), "ETHUSDT": _seq(lambda i: 2 * i)}
    out = ac.correlated_exposure(
        candidate_symbol="BTCUSDT",
        candidate_direction="long",
        candidate_risk=None,          # candidate risk unknown
        open_positions=[{"symbol": "ETHUSDT", "direction": "long"}],  # pos risk unknown
        returns=rets,
        min_obs=10,
    )
    # risk falls back to 1.0 → weighted = |r| * 1 = 1.0; concentration None
    # because the candidate risk denominator is unknown.
    assert math.isclose(out["corr_weighted_aligned_risk"], 1.0, abs_tol=1e-9)
    assert out["corr_concentration"] is None


# --- candidate adapter -----------------------------------------------------

def test_candidate_adapter_reads_attrs():
    rets = {"BTCUSDT": _seq(lambda i: i), "ETHUSDT": _seq(lambda i: 2 * i)}
    cand = SimpleNamespace(
        symbol="BTCUSDT", direction="long", entry_price=100.0, stop_loss=95.0, qty=10.0
    )
    out = ac.candidate_correlated_exposure(
        cand,
        [{"symbol": "ETHUSDT", "direction": "long", "risk": 100.0}],
        rets,
        min_obs=10,
    )
    # candidate risk = |100-95| * 10 = 50
    assert math.isclose(out["max_abs_corr"], 1.0, abs_tol=1e-9)
    assert math.isclose(out["corr_concentration"], 2.0, abs_tol=1e-9)  # 100/50


def test_candidate_adapter_falls_back_to_side():
    rets = {"BTCUSDT": _seq(lambda i: i), "ETHUSDT": _seq(lambda i: 2 * i)}
    cand = SimpleNamespace(symbol="BTCUSDT", side="buy", entry_price=100.0, stop_loss=95.0)
    out = ac.candidate_correlated_exposure(
        cand, [{"symbol": "ETHUSDT", "direction": "long", "risk": 100.0}], rets, min_obs=10
    )
    assert out["corr_weighted_aligned_risk"] > 0.0


def test_candidate_adapter_never_raises_on_junk():
    out = ac.candidate_correlated_exposure(object(), [{"symbol": "X"}], {}, min_obs=10)
    assert out["n_book_measured"] == 0
    assert out["max_abs_corr"] is None
