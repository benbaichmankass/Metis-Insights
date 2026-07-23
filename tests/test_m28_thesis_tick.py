"""M28 P3 — tests for the macro/value thesis sleeve tick (observe-only scanner)."""

from __future__ import annotations

import json

from src.units.strategies.macro_thesis.thesis import TradeThesis
from src.units.strategies.macro_thesis.thesis_tick import (
    _should_run,
    _valueread_from_snapshot,
    form_tick_theses,
    read_thesis_soak,
    write_thesis_soak,
)

_CFG = {"execution": "shadow", "min_conviction": 0.4, "account": "alpaca_options_paper",
        "express_as": "debit_vertical", "universe": []}


def _snap(symbol, metric, cheap_score, label, value=1.0):
    return {"symbol": symbol, "metric": metric, "value": value, "cheap_score": cheap_score,
            "label": label, "z_score": 0.0, "percentile": 0.5, "n_history": 100,
            "higher_is_cheaper": True, "observed_at": "2026-07-23T00:00:00Z"}


# --------------------------------------------------------------------------
# reconstruction
# --------------------------------------------------------------------------

def test_valueread_from_snapshot_round_trips_fields():
    r = _valueread_from_snapshot(_snap("TLT", "real_yield_10y", 0.9, "cheap", value=2.1))
    assert r.metric == "real_yield_10y"
    assert r.value == 2.1
    assert r.cheap_score == 0.9
    assert r.label == "cheap"
    assert r.n == 100


def test_valueread_from_snapshot_honest_null_n():
    r = _valueread_from_snapshot({"symbol": "X", "metric": "m", "n_history": None})
    assert r.n == 0
    assert r.label == "unknown"


# --------------------------------------------------------------------------
# form_tick_theses (pure)
# --------------------------------------------------------------------------

def test_forms_one_thesis_per_directional_symbol():
    rows = [
        _snap("TLT", "real_yield_10y", 0.95, "cheap"),   # conviction 0.9, long
        _snap("GLD", "real_yield_10y", 0.5, "fair"),     # neutral → skipped
        _snap("SLV", "gold_silver_ratio", 0.05, "rich"), # conviction 0.9, short
    ]
    theses = form_tick_theses(rows, cfg=_CFG, now_iso="2026-07-23T00:00:00Z", id_prefix="20260723")
    by = {t.instrument["symbol"]: t for t in theses}
    assert set(by) == {"TLT", "SLV"}
    assert by["TLT"].direction == "long"
    assert by["SLV"].direction == "short"
    assert by["TLT"].thesis_id == "mth-20260723-TLT"
    assert all(isinstance(t, TradeThesis) for t in theses)
    assert by["TLT"].account == "alpaca_options_paper"


def test_strongest_metric_governs_per_symbol():
    # TLT has two metrics; the higher-conviction one (0.95 → 0.9) governs
    rows = [
        _snap("TLT", "real_yield_10y", 0.72, "cheap"),     # conviction 0.44
        _snap("TLT", "term_slope", 0.95, "cheap"),         # conviction 0.90 → wins
    ]
    theses = form_tick_theses(rows, cfg=_CFG, now_iso="t", id_prefix="d")
    assert len(theses) == 1
    assert theses[0].valuation["metric"] == "term_slope"
    assert abs(theses[0].thesis_conviction - 0.9) < 1e-9


def test_min_conviction_gate_applied():
    rows = [_snap("TLT", "m", 0.72, "cheap")]   # conviction 0.44
    assert form_tick_theses(rows, cfg={**_CFG, "min_conviction": 0.5}, now_iso="t",
                            id_prefix="d") == []
    assert len(form_tick_theses(rows, cfg={**_CFG, "min_conviction": 0.1}, now_iso="t",
                                id_prefix="d")) == 1


def test_universe_allowlist_restricts_symbols():
    rows = [_snap("TLT", "m", 0.95, "cheap"), _snap("SLV", "m", 0.95, "cheap")]
    theses = form_tick_theses(rows, cfg={**_CFG, "universe": ["TLT"]}, now_iso="t",
                              id_prefix="d")
    assert [t.instrument["symbol"] for t in theses] == ["TLT"]


def test_empty_snapshots_form_nothing():
    assert form_tick_theses([], cfg=_CFG, now_iso="t", id_prefix="d") == []


def test_rows_without_symbol_or_score_skipped():
    rows = [{"metric": "m", "cheap_score": 0.95, "label": "cheap"},   # no symbol
            _snap("SPY", "m", None, "unknown"),                       # no score
            _snap("TLT", "m", 0.95, "cheap")]
    theses = form_tick_theses(rows, cfg=_CFG, now_iso="t", id_prefix="d")
    assert [t.instrument["symbol"] for t in theses] == ["TLT"]


# --------------------------------------------------------------------------
# soak I/O
# --------------------------------------------------------------------------

def test_soak_write_read_newest_first(tmp_path):
    p = tmp_path / "soak.jsonl"
    n = write_thesis_soak([{"event": "would_form", "thesis_id": "a", "at": "t1"},
                           {"event": "would_form", "thesis_id": "b", "at": "t2"}], path=p)
    assert n == 2
    rows = read_thesis_soak(path=p)
    assert rows[0]["thesis_id"] == "b"
    assert read_thesis_soak(path=tmp_path / "nope.jsonl") == []


# --------------------------------------------------------------------------
# cadence gate
# --------------------------------------------------------------------------

def test_should_run_first_time_and_after_cadence(tmp_path):
    sp = tmp_path / "state.json"
    # no state file → run
    assert _should_run(1000.0, 3600, state_path=sp) is True
    sp.write_text(json.dumps({"last_run_epoch": 1000.0}))
    # only 100s later, cadence 3600 → don't run
    assert _should_run(1100.0, 3600, state_path=sp) is False
    # 3600s later → run
    assert _should_run(4600.0, 3600, state_path=sp) is True
    # cadence 0 → always run (paused-off / immediate)
    assert _should_run(1100.0, 0, state_path=sp) is True
