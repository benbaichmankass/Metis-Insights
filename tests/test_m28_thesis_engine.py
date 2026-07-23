"""M28 P3 — tests for the S1 rule-based thesis former."""

from __future__ import annotations

from src.units.strategies.macro_thesis.thesis import TradeThesis
from src.units.strategies.macro_thesis.thesis_engine import (
    form_theses_from_reads,
    form_value_thesis,
    value_conviction,
)
from src.units.strategies.macro_thesis.valuation import ValueRead


def _read(metric="real_yield_10y", *, cheap_score, label, value=1.0, z=0.0, pct=0.5, n=100):
    return ValueRead(metric=metric, value=value, percentile=pct, z_score=z,
                     cheap_score=cheap_score, label=label, n=n)


# --------------------------------------------------------------------------
# value_conviction
# --------------------------------------------------------------------------

def test_value_conviction_extremity():
    assert value_conviction(_read(cheap_score=1.0, label="cheap")) == 1.0
    assert value_conviction(_read(cheap_score=0.0, label="rich")) == 1.0
    assert value_conviction(_read(cheap_score=0.5, label="fair")) == 0.0
    assert value_conviction(_read(cheap_score=0.9, label="cheap")) == 0.8
    assert value_conviction(_read(cheap_score=None, label="unknown")) is None


# --------------------------------------------------------------------------
# form_value_thesis
# --------------------------------------------------------------------------

def test_cheap_read_forms_long_thesis():
    r = _read(cheap_score=0.95, label="cheap", value=2.1)
    t = form_value_thesis("TLT", r, thesis_id="mth-1", created_at="2026-07-23T00:00:00Z")
    assert isinstance(t, TradeThesis)
    assert t.direction == "long"
    assert t.status == "draft"
    assert t.instrument == {"symbol": "TLT", "venue": "alpaca", "express_as": "debit_vertical"}
    assert t.valuation["label"] == "cheap"
    assert abs(t.thesis_conviction - 0.9) < 1e-9
    assert t.conviction_provenance["source"] == "value_read"
    assert t.account == "alpaca_options_paper"
    assert "TLT" in t.rationale


def test_rich_read_forms_short_thesis():
    r = _read(cheap_score=0.05, label="rich")
    t = form_value_thesis("GLD", r, thesis_id="mth-2", created_at="t")
    assert t.direction == "short"


def test_fair_read_forms_no_thesis():
    r = _read(cheap_score=0.5, label="fair")
    assert form_value_thesis("SPY", r, thesis_id="mth-3", created_at="t") is None


def test_unknown_read_forms_no_thesis():
    r = _read(cheap_score=None, label="unknown", value=None)
    assert form_value_thesis("SPY", r, thesis_id="mth-4", created_at="t") is None


def test_min_conviction_gate():
    # cheap_score 0.72 >= 0.70 threshold → directional (long); conviction 0.44
    r = _read(cheap_score=0.72, label="cheap")
    assert form_value_thesis("TLT", r, thesis_id="m", created_at="t",
                             min_conviction=0.5) is None      # 0.44 < 0.5 → gated
    assert form_value_thesis("TLT", r, thesis_id="m", created_at="t",
                             min_conviction=0.1) is not None  # 0.44 >= 0.1 → forms


def test_links_signals_and_events():
    r = _read(cheap_score=0.9, label="cheap")
    events = [{"event_id": "evt-fomc-1",
               "on_outcome": [{"if": {"field": "direction", "op": "eq", "value": "dovish"},
                               "action": "add"}]}]
    t = form_value_thesis("TLT", r, thesis_id="m", created_at="t",
                          signal_ids=["sig-a", "sig-b"], watched_events=events,
                          world_view={"regime": "easing"})
    assert t.signals == ["sig-a", "sig-b"]
    assert t.watched_events[0]["event_id"] == "evt-fomc-1"
    assert t.world_view == {"regime": "easing"}


def test_form_value_thesis_is_pure_no_shared_refs():
    r = _read(cheap_score=0.9, label="cheap")
    events = [{"event_id": "e", "on_outcome": []}]
    t = form_value_thesis("TLT", r, thesis_id="m", created_at="t", watched_events=events)
    events[0]["event_id"] = "MUTATED"         # caller mutates its input
    assert t.watched_events[0]["event_id"] == "e"   # thesis kept its own copy


# --------------------------------------------------------------------------
# form_theses_from_reads (batch)
# --------------------------------------------------------------------------

def test_batch_skips_neutral_and_sorts_by_conviction():
    reads = {
        "TLT": _read(cheap_score=0.95, label="cheap"),   # conviction 0.9, long
        "GLD": _read(cheap_score=0.5, label="fair"),     # skipped
        "SLV": _read(cheap_score=0.1, label="rich"),     # conviction 0.8, short
        "SPY": _read(cheap_score=None, label="unknown"), # skipped
    }
    theses = form_theses_from_reads(reads, id_prefix="20260723", created_at="t")
    assert [t.instrument["symbol"] for t in theses] == ["TLT", "SLV"]   # conviction desc
    assert theses[0].thesis_id == "mth-20260723-TLT"
    assert theses[0].direction == "long"
    assert theses[1].direction == "short"


def test_batch_deterministic_ids_and_min_conviction():
    reads = {"TLT": _read(cheap_score=0.6, label="cheap"),   # conviction 0.2
             "SLV": _read(cheap_score=0.95, label="cheap")}  # conviction 0.9
    theses = form_theses_from_reads(reads, id_prefix="d", created_at="t",
                                    min_conviction=0.5)
    assert [t.instrument["symbol"] for t in theses] == ["SLV"]   # TLT gated out
    # deterministic: a second identical scan reproduces the same id
    again = form_theses_from_reads(reads, id_prefix="d", created_at="t",
                                   min_conviction=0.5)
    assert again[0].thesis_id == theses[0].thesis_id


def test_batch_empty_when_no_views():
    reads = {"SPY": _read(cheap_score=0.5, label="fair")}
    assert form_theses_from_reads(reads, id_prefix="d", created_at="t") == []
