"""M28 P1 — tests for the valuation feed composition (config → value reads)."""

from __future__ import annotations

import pytest

from src.units.strategies.macro_thesis.valuation_feed import (
    build_valuation_reads,
    compute_metric,
    load_valuation_config,
)


# --------------------------------------------------------------------------
# config load
# --------------------------------------------------------------------------

def test_real_config_loads_with_seed_universe():
    cfg = load_valuation_config()
    assert isinstance(cfg, dict)
    instruments = cfg.get("instruments", {})
    # The narrow seed universe (decision (d)).
    for sym in ("TLT", "IEF", "GLD", "SLV", "SPY"):
        assert sym in instruments, f"{sym} missing from seed universe"
    assert "credit_risk" in cfg.get("context", {})


def test_load_missing_config_is_empty_not_raise():
    assert load_valuation_config("/nonexistent/path/x.yaml") == {}


# --------------------------------------------------------------------------
# compute_metric
# --------------------------------------------------------------------------

def test_compute_real_yield_direct():
    sv = {"DFII10": 2.1}
    assert compute_metric("real_yield_10y", {"series": "DFII10"}, sv) == 2.1


def test_compute_credit_spread():
    sv = {"BAMLH0A0HYM2": 3.4}
    assert compute_metric("credit_spread", {"series": "BAMLH0A0HYM2"}, sv) == 3.4


def test_compute_term_slope():
    sv = {"DGS10": 4.2, "DGS3MO": 5.0}
    assert compute_metric("term_slope", {"long": "DGS10", "short": "DGS3MO"}, sv) == pytest.approx(-0.8)


def test_compute_erp_present_and_missing():
    inp = {"earnings_yield": {"source": "sp500_earnings_yield"}, "real_yield": {"series": "DFII10"}}
    sv_full = {"sp500_earnings_yield": 0.052, "DFII10": 0.020}
    assert compute_metric("equity_risk_premium", inp, sv_full) == 0.032
    # earnings yield not injected yet -> honest-null
    assert compute_metric("equity_risk_premium", inp, {"DFII10": 0.02}) is None


def test_compute_gold_silver_ratio_present_and_missing():
    inp = {"gold": {"source": "price_gld"}, "silver": {"source": "price_slv"}}
    assert compute_metric("gold_silver_ratio", inp, {"price_gld": 2000.0, "price_slv": 25.0}) == 80.0
    assert compute_metric("gold_silver_ratio", inp, {"price_gld": 2000.0}) is None


def test_compute_unknown_metric_is_none():
    assert compute_metric("no_such_metric", {}, {"x": 1}) is None


def test_compute_missing_series_is_none():
    assert compute_metric("real_yield_10y", {"series": "DFII10"}, {}) is None


# --------------------------------------------------------------------------
# build_valuation_reads
# --------------------------------------------------------------------------

_CFG = {
    "instruments": {
        "TLT": {"asset_class": "bond", "metrics": [
            {"metric": "real_yield_10y", "inputs": {"series": "DFII10"}, "higher_is_cheaper": False},
        ]},
        "SPY": {"asset_class": "equity", "metrics": [
            {"metric": "equity_risk_premium",
             "inputs": {"earnings_yield": {"source": "ey"}, "real_yield": {"series": "DFII10"}},
             "higher_is_cheaper": True},
        ]},
    },
    "context": {
        "credit_risk": {"asset_class": "macro", "metrics": [
            {"metric": "credit_spread", "inputs": {"series": "HY"}, "higher_is_cheaper": True},
        ]},
    },
}


def _rows_by_symbol(rows):
    return {r["symbol"]: r for r in rows}


def test_build_reads_high_real_yield_reads_rich_for_bonds():
    # DFII10 = 3.0 sits at the top of its history; higher_is_cheaper=False ⇒ RICH.
    hist = {"real_yield_10y": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]}
    sv = {"DFII10": 3.0}
    rows = build_valuation_reads(_CFG, sv, hist, observed_at="2026-07-23T00:00:00Z", as_of="2026-07-22")
    by = _rows_by_symbol(rows)
    assert by["TLT"]["label"] == "rich"
    assert by["TLT"]["value"] == 3.0
    assert by["TLT"]["as_of"] == "2026-07-22"
    assert by["TLT"]["source"] == "fred"


def test_build_reads_wide_credit_reads_cheap():
    hist = {"credit_spread": [2.0, 2.5, 3.0, 3.5, 4.0]}
    sv = {"HY": 4.0}   # top of history, higher_is_cheaper=True ⇒ cheap
    rows = build_valuation_reads(_CFG, sv, hist, observed_at="t", as_of="d")
    by = _rows_by_symbol(rows)
    assert by["credit_risk"]["label"] == "cheap"
    assert by["credit_risk"]["asset_class"] == "macro"


def test_build_reads_erp_missing_input_is_unknown_but_row_emitted():
    # No earnings yield injected -> ERP uncomputable -> unknown, but the row is
    # still emitted (records the attempt, honest-null).
    rows = build_valuation_reads(
        _CFG, {"DFII10": 2.0, "HY": 3.0},
        {"equity_risk_premium": [0.01, 0.02, 0.03]},
        observed_at="t", as_of="d",
    )
    by = _rows_by_symbol(rows)
    assert by["SPY"]["label"] == "unknown"
    assert by["SPY"]["value"] is None


def test_build_reads_row_count_and_shape():
    rows = build_valuation_reads(_CFG, {"DFII10": 2.0, "ey": 0.05, "HY": 3.0}, {}, observed_at="t", as_of="d")
    # TLT + SPY + credit_risk = 3 metric rows
    assert len(rows) == 3
    for r in rows:
        assert {"symbol", "metric", "value", "label", "cheap_score",
                "observed_at", "as_of", "source", "inputs"} <= set(r.keys())


def test_build_reads_over_real_config_never_raises():
    cfg = load_valuation_config()
    # Inject only DFII10; everything else honest-nulls. Must not raise.
    rows = build_valuation_reads(cfg, {"DFII10": 1.8}, {}, observed_at="t", as_of="d")
    assert isinstance(rows, list) and len(rows) >= 5
    # The DFII10-driven metrics computed; the price/earnings ones are unknown.
    tlt = [r for r in rows if r["symbol"] == "TLT"][0]
    assert tlt["value"] == 1.8


def test_build_reads_empty_config_is_empty_list():
    assert build_valuation_reads({}, {}, {}, observed_at="t", as_of="d") == []
