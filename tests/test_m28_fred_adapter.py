"""M28 P1 — tests for the FRED live-adapter (pure parts + injectable network)."""

from __future__ import annotations

import pytest

from src.units.strategies.macro_thesis.fred_adapter import (
    fetch_fred_series_history,
    fred_fetch_and_history,
    metric_histories,
    parse_fredgraph_csv,
)


# --------------------------------------------------------------------------
# parse_fredgraph_csv
# --------------------------------------------------------------------------

def test_parse_basic_with_missing_and_header():
    csv = "DATE,DFII10\n2026-01-01,2.0\n2026-01-02,.\n2026-01-03,2.1\n"
    assert parse_fredgraph_csv(csv) == [2.0, 2.1]   # header skipped, "." dropped


def test_parse_empty_and_junk():
    assert parse_fredgraph_csv("") == []
    assert parse_fredgraph_csv("DATE,V\nbad-row\n2026,notanumber\n2026-01-01,3.5\n") == [3.5]


# --------------------------------------------------------------------------
# metric_histories
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
        "term_structure": {"asset_class": "macro", "metrics": [
            {"metric": "term_slope", "inputs": {"long": "DGS10", "short": "DGS3MO"}, "higher_is_cheaper": True},
        ]},
    },
}


def test_metric_histories_direct_and_slope_and_erp():
    sh = {
        "DFII10": [1.0, 2.0, 3.0],
        "DGS10": [4.0, 4.5, 5.0],
        "DGS3MO": [3.0, 3.5, 4.0, 4.2],   # longer: aligns to last 3
    }
    mh = metric_histories(_CFG, sh)
    assert mh["real_yield_10y"] == [1.0, 2.0, 3.0]           # direct series
    # term_slope aligns tails: DGS10[3] vs DGS3MO last-3 [3.5,4.0,4.2]
    assert mh["term_slope"] == pytest.approx([0.5, 0.5, 0.8])
    # ERP needs earnings-yield source "ey" (absent) -> honest-null empty
    assert mh["equity_risk_premium"] == []


def test_metric_histories_erp_when_earnings_present():
    cfg = {"instruments": {"SPY": {"asset_class": "equity", "metrics": [
        {"metric": "equity_risk_premium",
         "inputs": {"earnings_yield": {"source": "ey"}, "real_yield": {"series": "DFII10"}},
         "higher_is_cheaper": True}]}}}
    sh = {"ey": [0.05, 0.06], "DFII10": [0.02, 0.02]}
    assert metric_histories(cfg, sh)["equity_risk_premium"] == pytest.approx([0.03, 0.04])


def test_metric_histories_gold_silver_ratio_div_guard():
    cfg = {"instruments": {"SLV": {"asset_class": "commodity", "metrics": [
        {"metric": "gold_silver_ratio",
         "inputs": {"gold": {"source": "g"}, "silver": {"source": "s"}},
         "higher_is_cheaper": True}]}}}
    sh = {"g": [2000.0, 2100.0], "s": [25.0, 0.0]}   # second silver 0 -> dropped
    assert metric_histories(cfg, sh)["gold_silver_ratio"] == [80.0]


# --------------------------------------------------------------------------
# network layer (injectable urlopen) + off-VM guard
# --------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, text):
        self._text = text
    def read(self):
        return self._text.encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _fake_urlopen_factory(by_id):
    def _open(url, timeout=None):
        # url ends with id={SID}
        sid = url.rsplit("id=", 1)[-1]
        if sid not in by_id:
            raise RuntimeError("404")
        return _FakeResp(by_id[sid])
    return _open


def test_fetch_requires_offvm_or_injected(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(RuntimeError):
        fetch_fred_series_history(["DFII10"])   # no urlopen, not off-VM


def test_fetch_with_injected_urlopen():
    fake = _fake_urlopen_factory({"DFII10": "DATE,DFII10\n2026-01-01,2.0\n2026-01-02,2.1\n"})
    out = fetch_fred_series_history(["DFII10", "MISSING"], urlopen=fake)
    assert out["DFII10"] == [2.0, 2.1]
    assert out["MISSING"] == []   # 404 -> honest-null, not fatal


def test_fred_fetch_and_history_wires_run_valuation_feed():
    fake = _fake_urlopen_factory({
        "DFII10": "D,V\n" + "\n".join(f"2026-01-{i:02d},{1.0 + i*0.1}" for i in range(1, 6)),
        "DGS10": "D,V\n2026-01-01,4.0\n2026-01-02,4.5",
        "DGS3MO": "D,V\n2026-01-01,3.0\n2026-01-02,3.2",
        "BAMLH0A0HYM2": "D,V\n2026-01-01,3.0\n2026-01-02,3.5",
    })
    fetch_fn, history_fn = fred_fetch_and_history(_CFG, urlopen=fake)
    # latest DFII10 = 1.5 (i=5)
    assert fetch_fn(["DFII10"])["DFII10"] == pytest.approx(1.5)
    assert history_fn("real_yield_10y")[-1] == pytest.approx(1.5)
    # ERP source absent -> empty history
    assert history_fn("equity_risk_premium") == []
