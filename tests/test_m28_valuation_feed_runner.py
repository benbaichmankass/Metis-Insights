"""M28 P1 — tests for the valuation feed runner (required_series + run)."""

from __future__ import annotations

from src.units.strategies.macro_thesis.valuation_feed import (
    load_valuation_config,
    required_series,
    run_valuation_feed,
)

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
        "term_structure": {"asset_class": "macro", "metrics": [
            {"metric": "term_slope", "inputs": {"long": "DGS10", "short": "DGS3MO"}, "higher_is_cheaper": True},
        ]},
    },
}


# --------------------------------------------------------------------------
# required_series
# --------------------------------------------------------------------------

def test_required_series_collects_fred_ids_and_sources():
    req = required_series(_CFG)
    assert req["series"] == ["DFII10", "DGS10", "DGS3MO"]   # sorted, deduped
    assert req["sources"] == ["ey"]                          # unwired non-FRED input


def test_required_series_over_real_config():
    req = required_series(load_valuation_config())
    # Free FRED series the seed universe needs.
    for sid in ("DFII10", "BAMLH0A0HYM2", "DGS10", "DGS3MO"):
        assert sid in req["series"], f"{sid} not resolved"
    # The not-yet-wired non-FRED inputs (earnings yield, metal prices).
    for src in ("sp500_earnings_yield", "price_gld", "price_slv"):
        assert src in req["sources"]


def test_required_series_empty_config():
    assert required_series({}) == {"series": [], "sources": []}


# --------------------------------------------------------------------------
# run_valuation_feed
# --------------------------------------------------------------------------

def test_run_fetches_only_required_and_builds_rows():
    fetched: dict = {}

    def fetch(ids):
        fetched["ids"] = list(ids)
        return {"DFII10": 2.5, "DGS10": 4.2, "DGS3MO": 5.0}  # ey deliberately absent

    def hist(metric):
        return {"real_yield_10y": [0.5, 1.0, 1.5, 2.0], "term_slope": [-1, 0, 1]}.get(metric, [])

    rows = run_valuation_feed(_CFG, fetch, observed_at="t", as_of="d", history_fn=hist)
    # Fetch was asked for exactly the required FRED series.
    assert fetched["ids"] == ["DFII10", "DGS10", "DGS3MO"]
    by = {r["symbol"]: r for r in rows}
    # DFII10=2.5 is at/above the top of [0.5..2.0], higher_is_cheaper=False ⇒ rich.
    assert by["TLT"]["value"] == 2.5 and by["TLT"]["label"] == "rich"
    # SPY ERP uncomputable (no earnings yield fetched) ⇒ unknown, honest-null.
    assert by["SPY"]["value"] is None and by["SPY"]["label"] == "unknown"
    # term_slope computed (4.2 - 5.0 = -0.8).
    assert by["term_structure"]["value"] is not None


def test_run_no_history_fn_yields_unknown_labels_but_records_value():
    def fetch(ids):
        return {"DFII10": 2.0, "DGS10": 4.0, "DGS3MO": 4.5}

    rows = run_valuation_feed(_CFG, fetch, observed_at="t", as_of="d")  # no history_fn
    by = {r["symbol"]: r for r in rows}
    assert by["TLT"]["value"] == 2.0          # point-in-time value still recorded
    assert by["TLT"]["label"] == "unknown"    # no history ⇒ can't say cheap/rich


def test_run_is_fail_permissive_on_fetch_exception():
    def bad_fetch(ids):
        raise RuntimeError("network down")

    rows = run_valuation_feed(_CFG, bad_fetch, observed_at="t", as_of="d")
    # Degrades to all-missing, still returns rows (unknown), never raises.
    assert all(r["value"] is None for r in rows)


def test_run_is_fail_permissive_on_history_exception():
    def fetch(ids):
        return {"DFII10": 2.0, "DGS10": 4.0, "DGS3MO": 4.5}

    def bad_hist(metric):
        raise ValueError("bad history store")

    rows = run_valuation_feed(_CFG, fetch, observed_at="t", as_of="d", history_fn=bad_hist)
    by = {r["symbol"]: r for r in rows}
    assert by["TLT"]["value"] == 2.0          # value fetched fine
    assert by["TLT"]["label"] == "unknown"    # history failed ⇒ honest-null read
