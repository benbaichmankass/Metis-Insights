"""M28 — tests for the macro_signals traceable-evidence store (schema §3)."""

from __future__ import annotations

from src.units.strategies.macro_thesis.macro_signals import (
    make_signal,
    new_signal_id,
    read_signal_records,
    read_signals_by_event,
    read_signals_for_entity,
    read_signals_for_thesis,
    write_signals,
)


# --------------------------------------------------------------------------
# make_signal — honest-null shaping + validation
# --------------------------------------------------------------------------

def test_new_signal_id():
    assert new_signal_id("01ABC") == "sig-01ABC"


def test_make_signal_full_row():
    s = make_signal(new_signal_id("1"), ts="2026-07-23T00:00:00Z",
                    observed_at="2026-07-23T00:00:01Z", source="fred",
                    claim="real 10y yield rich", entity="TLT", direction="bearish",
                    magnitude=0.7, confidence=0.6, source_url="u",
                    extractor_id="fred-erp-v1", event_ref="evt-1", raw_ref="h")
    assert s["signal_id"] == "sig-1"
    assert s["source_known"] is True
    assert s["direction"] == "bearish"
    assert s["magnitude"] == 0.7
    assert s["confidence"] == 0.6
    assert s["entity"] == "TLT"


def test_make_signal_honest_null_defaults():
    s = make_signal("sig-x", ts="t", observed_at="t", source="rss", claim="c")
    # unsupported fields are None, not fabricated
    assert s["magnitude"] is None
    assert s["confidence"] is None
    assert s["entity"] is None
    assert s["direction"] == "neutral"          # non-committal default


def test_make_signal_invalid_direction_falls_back_neutral():
    s = make_signal("sig-x", ts="t", observed_at="t", source="rss", claim="c",
                    direction="mega-bull")
    assert s["direction"] == "neutral"


def test_make_signal_clamps_and_nulls_units():
    hi = make_signal("s", ts="t", observed_at="t", source="rss", claim="c",
                     magnitude=1.5, confidence=-0.2)
    assert hi["magnitude"] == 1.0
    assert hi["confidence"] == 0.0
    bad = make_signal("s", ts="t", observed_at="t", source="rss", claim="c",
                      magnitude="n/a", confidence=float("nan"))
    assert bad["magnitude"] is None            # non-numeric -> honest-null
    assert bad["confidence"] is None           # NaN -> honest-null


def test_make_signal_flags_unknown_source():
    s = make_signal("s", ts="t", observed_at="t", source="some_paid_vendor",
                    claim="c")
    assert s["source"] == "some_paid_vendor"   # preserved verbatim
    assert s["source_known"] is False          # but flagged for audit


# --------------------------------------------------------------------------
# store: write + reads
# --------------------------------------------------------------------------

def _sig(sid, entity=None, event_ref=None, source="fred"):
    return make_signal(sid, ts="t", observed_at="t", source=source, claim="c",
                       entity=entity, event_ref=event_ref)


def test_write_read_newest_first(tmp_path):
    p = tmp_path / "sig.jsonl"
    n = write_signals([_sig("sig-a"), _sig("sig-b")], path=p)
    assert n == 2
    recs = read_signal_records(path=p)
    assert recs[0]["signal_id"] == "sig-b"     # last appended first out
    assert write_signals(["notadict", None], path=p) == 0   # bad rows skipped


def test_read_for_entity(tmp_path):
    p = tmp_path / "sig.jsonl"
    write_signals([_sig("sig-a", entity="TLT"), _sig("sig-b", entity="GLD"),
                   _sig("sig-c", entity="TLT")], path=p)
    tlt = read_signals_for_entity("TLT", path=p)
    assert {r["signal_id"] for r in tlt} == {"sig-a", "sig-c"}


def test_read_by_event(tmp_path):
    p = tmp_path / "sig.jsonl"
    write_signals([_sig("sig-a", event_ref="evt-1"), _sig("sig-b", event_ref="evt-2")],
                  path=p)
    assert {r["signal_id"] for r in read_signals_by_event("evt-1", path=p)} == {"sig-a"}


def test_read_for_thesis_resolves_refs_in_order(tmp_path):
    p = tmp_path / "sig.jsonl"
    write_signals([_sig("sig-a"), _sig("sig-b"), _sig("sig-c")], path=p)
    # order preserved per the requested ids; a missing id is dropped
    got = read_signals_for_thesis(["sig-c", "sig-a", "sig-missing"], path=p)
    assert [r["signal_id"] for r in got] == ["sig-c", "sig-a"]


def test_read_missing_file_is_empty(tmp_path):
    assert read_signal_records(path=tmp_path / "nope.jsonl") == []
    assert read_signals_for_thesis(["sig-a"], path=tmp_path / "nope.jsonl") == []


def test_read_skips_bad_lines(tmp_path):
    p = tmp_path / "sig.jsonl"
    p.write_text('{"signal_id":"sig-a","entity":"TLT"}\nNOPE\n\n')
    assert len(read_signal_records(path=p)) == 1
