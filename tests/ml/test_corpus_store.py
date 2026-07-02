"""Tests for the standing corpus store (M19 corpus C1b). Pure stdlib — no ML stack."""
from __future__ import annotations

import json

from ml.datasets import corpus_store as cs


def test_corpus_root_precedence(tmp_path, monkeypatch):
    # explicit arg wins over env
    monkeypatch.setenv(cs.CORPUS_ROOT_ENV, str(tmp_path / "from_env"))
    assert cs.corpus_root(tmp_path / "explicit") == tmp_path / "explicit"
    # env wins over default
    assert cs.corpus_root() == tmp_path / "from_env"
    # default when neither
    monkeypatch.delenv(cs.CORPUS_ROOT_ENV, raising=False)
    assert cs.corpus_root() == cs.corpus_root(None)  # stable default


def test_write_read_roundtrip_and_catalog(tmp_path):
    rows = [
        {"date": "2020-01-02", "value": 1.5},
        {"date": "2020-01-03", "value": 1.6},
        {"date": "2020-01-06", "value": 1.4},
    ]
    entry = cs.write_series(
        "fred_ust10y", "macro", "fred", rows,
        refreshed_at="2026-07-02T00:00:00Z", root=tmp_path, source_ref="DGS10",
    )
    assert entry["row_count"] == 3
    assert entry["first_date"] == "2020-01-02"
    assert entry["last_date"] == "2020-01-06"
    assert entry["source_ref"] == "DGS10"
    assert entry["group"] == "macro"

    # series file exists and is ascending
    series_file = tmp_path / "macro" / "fred_ust10y.jsonl"
    assert series_file.is_file()
    read = cs.read_series("fred_ust10y", root=tmp_path)
    assert [r["date"] for r in read] == ["2020-01-02", "2020-01-03", "2020-01-06"]
    assert [r["value"] for r in read] == [1.5, 1.6, 1.4]

    # catalog persists the entry
    cat = cs.load_catalog(tmp_path)
    assert "fred_ust10y" in cat
    assert cat["fred_ust10y"]["path"] == "macro/fred_ust10y.jsonl"


def test_multiple_series_share_one_catalog(tmp_path):
    cs.write_series("a", "macro", "fred", [{"date": "2021-01-01", "value": 1.0}],
                    refreshed_at="t", root=tmp_path)
    cs.write_series("b", "commodity", "fred", [{"date": "2021-01-01", "value": 2.0}],
                    refreshed_at="t", root=tmp_path)
    cat = cs.load_catalog(tmp_path)
    assert set(cat) == {"a", "b"}
    assert cat["b"]["group"] == "commodity"
    # each in its own group dir
    assert (tmp_path / "macro" / "a.jsonl").is_file()
    assert (tmp_path / "commodity" / "b.jsonl").is_file()


def test_rewrite_is_idempotent_and_updates_entry(tmp_path):
    cs.write_series("x", "macro", "fred", [{"date": "2021-01-01", "value": 1.0}],
                    refreshed_at="t1", root=tmp_path)
    cs.write_series(
        "x", "macro", "fred",
        [{"date": "2021-01-01", "value": 1.0}, {"date": "2021-01-04", "value": 2.0}],
        refreshed_at="t2", root=tmp_path,
    )
    assert len(cs.read_series("x", root=tmp_path)) == 2  # rewritten, not appended-dup
    assert cs.load_catalog(tmp_path)["x"]["last_date"] == "2021-01-04"
    assert cs.load_catalog(tmp_path)["x"]["refreshed_at"] == "t2"


def test_missing_and_nonfinite_values_dropped(tmp_path):
    rows = [
        {"date": "2021-01-01", "value": 1.0},
        {"date": "2021-01-02", "value": None},   # missing → dropped
        {"date": "2021-01-03", "value": "."},    # non-numeric → dropped
        {"date": "2021-01-04", "value": 2.0},
    ]
    entry = cs.write_series("y", "macro", "fred", rows, refreshed_at="t", root=tmp_path)
    assert entry["row_count"] == 2
    assert [r["date"] for r in cs.read_series("y", root=tmp_path)] == ["2021-01-01", "2021-01-04"]


def test_unsorted_input_is_sorted(tmp_path):
    rows = [
        {"date": "2021-03-03", "value": 3.0},
        {"date": "2021-01-01", "value": 1.0},
        {"date": "2021-02-02", "value": 2.0},
    ]
    cs.write_series("z", "macro", "fred", rows, refreshed_at="t", root=tmp_path)
    assert [r["date"] for r in cs.read_series("z", root=tmp_path)] == [
        "2021-01-01", "2021-02-02", "2021-03-03",
    ]


def test_empty_and_unknown_reads(tmp_path):
    assert cs.load_catalog(tmp_path) == {}          # absent corpus
    assert cs.read_series("nope", root=tmp_path) == []  # unknown series
    # empty series still writes a catalog entry with null bounds
    entry = cs.write_series("empty", "macro", "fred", [], refreshed_at="t", root=tmp_path)
    assert entry["row_count"] == 0
    assert entry["first_date"] is None and entry["last_date"] is None


def test_catalog_is_valid_json(tmp_path):
    cs.write_series("j", "macro", "fred", [{"date": "2021-01-01", "value": 1.0}],
                    refreshed_at="t", root=tmp_path)
    raw = (tmp_path / cs._CATALOG_NAME).read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["j"]["source"] == "fred"
