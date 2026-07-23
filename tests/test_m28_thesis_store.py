"""M28 — tests for the point-in-time TradeThesis store."""

from __future__ import annotations

from src.units.strategies.macro_thesis.thesis import TradeThesis, transition
from src.units.strategies.macro_thesis.thesis_store import (
    read_latest_theses,
    read_open_theses,
    read_theses_by_status,
    read_thesis_records,
    write_theses,
)


def _thesis(tid, status="draft", updated_at="2026-07-23T00:00:00Z", **kw):
    return TradeThesis(thesis_id=tid, created_at="2026-07-23T00:00:00Z",
                       updated_at=updated_at, status=status, **kw)


def test_write_accepts_objects_and_dicts(tmp_path):
    p = tmp_path / "theses.jsonl"
    n = write_theses([_thesis("mth-a"), {"thesis_id": "mth-b", "created_at": "t",
                                         "updated_at": "t", "status": "draft"}], path=p)
    assert n == 2
    # a non-thesis item is skipped, not raised
    assert write_theses([12345, None], path=p) == 0
    recs = read_thesis_records(path=p)
    assert {r["thesis_id"] for r in recs} == {"mth-a", "mth-b"}


def test_records_newest_first(tmp_path):
    p = tmp_path / "theses.jsonl"
    write_theses([_thesis("mth-a"), _thesis("mth-b")], path=p)
    recs = read_thesis_records(path=p)
    assert recs[0]["thesis_id"] == "mth-b"    # last appended is first out


def test_latest_supersedes_by_updated_at(tmp_path):
    p = tmp_path / "theses.jsonl"
    d = _thesis("mth-a", status="draft", updated_at="2026-07-23T00:00:00Z")
    write_theses([d], path=p)
    a = transition(d, "active", updated_at="2026-07-23T06:00:00Z")
    write_theses([a], path=p)                 # a NEW line, not an overwrite
    assert len(read_thesis_records(path=p)) == 2      # history preserved
    latest = read_latest_theses(path=p)
    assert isinstance(latest["mth-a"], TradeThesis)
    assert latest["mth-a"].status == "active"


def test_latest_picks_newest_per_thesis(tmp_path):
    p = tmp_path / "theses.jsonl"
    write_theses([
        _thesis("mth-a", updated_at="t1"),
        _thesis("mth-b", updated_at="t1"),
        _thesis("mth-a", status="active", updated_at="t2"),   # newer a
    ], path=p)
    latest = read_latest_theses(path=p)
    assert latest["mth-a"].status == "active"
    assert latest["mth-b"].status == "draft"
    assert len(latest) == 2


def test_read_by_status_and_open(tmp_path):
    p = tmp_path / "theses.jsonl"
    d = _thesis("mth-a", updated_at="t0")
    a = transition(d, "active", updated_at="t1")
    closed = transition(a, "closed", updated_at="t2", close_reason="target")
    write_theses([d, a, closed], path=p)             # a's whole lifecycle
    write_theses([_thesis("mth-b", status="draft", updated_at="t0")], path=p)
    # mth-a is now closed; mth-b still draft
    assert {t.thesis_id for t in read_theses_by_status("closed", path=p)} == {"mth-a"}
    assert {t.thesis_id for t in read_theses_by_status("draft", path=p)} == {"mth-b"}
    # open = non-terminal → only mth-b (mth-a closed is terminal)
    assert {t.thesis_id for t in read_open_theses(path=p)} == {"mth-b"}


def test_latest_ignores_rows_without_thesis_id(tmp_path):
    p = tmp_path / "theses.jsonl"
    write_theses([{"created_at": "t", "updated_at": "t", "status": "draft"},
                  _thesis("mth-a")], path=p)
    assert list(read_latest_theses(path=p).keys()) == ["mth-a"]


def test_read_missing_file_is_empty(tmp_path):
    assert read_thesis_records(path=tmp_path / "nope.jsonl") == []
    assert read_latest_theses(path=tmp_path / "nope.jsonl") == {}
    assert read_open_theses(path=tmp_path / "nope.jsonl") == []


def test_read_skips_bad_lines(tmp_path):
    p = tmp_path / "theses.jsonl"
    p.write_text('{"thesis_id":"mth-a","created_at":"t","updated_at":"t","status":"draft"}\nNOPE\n\n')
    assert len(read_thesis_records(path=p)) == 1


def test_records_limit(tmp_path):
    p = tmp_path / "theses.jsonl"
    write_theses([_thesis(f"mth-{i}", updated_at=f"t{i:02d}") for i in range(5)], path=p)
    assert len(read_thesis_records(path=p, limit=2)) == 2
