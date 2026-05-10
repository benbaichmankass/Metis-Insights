"""Tests for `ml.shadow.inspector` (S-AI-WS8-PART-1)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ml.shadow.inspector import (
    aggregate,
    filter_records,
    format_inspect_table,
    format_stats_table,
    iter_records,
    record_from_dict,
)


def _record(
    *,
    ts: str = "2026-05-10T12:00:00+00:00",
    model_id: str = "m-a",
    stage: str = "shadow",
    score: float = 0.5,
    row_keys: list[str] | None = None,
) -> dict:
    return {
        "predicted_at_utc": ts,
        "model_id": model_id,
        "stage": stage,
        "score": score,
        "row_keys": list(row_keys) if row_keys is not None else ["confidence", "direction"],
    }


def _write_log(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


class TestRecordFromDict:
    def test_happy_path(self):
        r = record_from_dict(_record())
        assert r.model_id == "m-a"
        assert r.stage == "shadow"
        assert r.score == pytest.approx(0.5)
        assert r.row_keys == ("confidence", "direction")
        assert r.predicted_at_utc.tzinfo is not None

    def test_naive_timestamp_assumed_utc(self):
        r = record_from_dict(_record(ts="2026-05-10T12:00:00"))
        assert r.predicted_at_utc.tzinfo == timezone.utc

    @pytest.mark.parametrize("missing", [
        "predicted_at_utc", "model_id", "stage", "score", "row_keys",
    ])
    def test_missing_field_raises(self, missing):
        bad = _record()
        bad.pop(missing)
        with pytest.raises(ValueError, match=missing):
            record_from_dict(bad)

    def test_unparseable_timestamp_raises(self):
        with pytest.raises(ValueError, match="predicted_at_utc"):
            record_from_dict(_record(ts="not-a-timestamp"))

    def test_non_finite_score_raises(self):
        with pytest.raises(ValueError, match="finite"):
            record_from_dict(_record(score=float("inf")))

    def test_row_keys_must_be_strs(self):
        with pytest.raises(ValueError, match="row_keys"):
            r = _record()
            r["row_keys"] = ["ok", 42]
            record_from_dict(r)


class TestIterRecords:
    def test_missing_file_returns_empty_iter(self, tmp_path):
        out = list(iter_records(tmp_path / "no-such-log.jsonl"))
        assert out == []

    def test_streams_well_formed_records(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        _write_log(log, [_record(model_id="a"), _record(model_id="b")])
        out = list(iter_records(log))
        assert [r.model_id for r in out] == ["a", "b"]

    def test_skips_blank_lines(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        log.write_text(
            json.dumps(_record(model_id="a")) + "\n"
            + "\n"
            + json.dumps(_record(model_id="b")) + "\n"
        )
        out = list(iter_records(log))
        assert [r.model_id for r in out] == ["a", "b"]

    def test_skips_malformed_json_with_warning(self, tmp_path, caplog):
        log = tmp_path / "audit.jsonl"
        log.write_text(
            json.dumps(_record(model_id="a")) + "\n"
            + "{not json\n"
            + json.dumps(_record(model_id="b")) + "\n"
        )
        with caplog.at_level(logging.WARNING):
            out = list(iter_records(log))
        assert [r.model_id for r in out] == ["a", "b"]
        assert any(
            "shadow_log_skip" in rec.message and "lineno=2" in rec.message
            for rec in caplog.records
        )

    def test_skips_non_object_lines(self, tmp_path, caplog):
        log = tmp_path / "audit.jsonl"
        log.write_text(
            json.dumps(_record(model_id="a")) + "\n"
            + json.dumps([1, 2, 3]) + "\n"
        )
        with caplog.at_level(logging.WARNING):
            out = list(iter_records(log))
        assert [r.model_id for r in out] == ["a"]
        assert any(
            "not-an-object" in rec.message for rec in caplog.records
        )

    def test_skips_invalid_records(self, tmp_path, caplog):
        log = tmp_path / "audit.jsonl"
        bad = _record()
        bad.pop("score")
        log.write_text(
            json.dumps(_record(model_id="a")) + "\n"
            + json.dumps(bad) + "\n"
        )
        with caplog.at_level(logging.WARNING):
            out = list(iter_records(log))
        assert [r.model_id for r in out] == ["a"]
        assert any("score" in rec.message for rec in caplog.records)


def _r(model_id="m-a", stage="shadow", score=0.5, ts="2026-05-10T12:00:00+00:00"):
    return record_from_dict(_record(
        model_id=model_id, stage=stage, score=score, ts=ts,
    ))


class TestFilterRecords:
    def test_no_filters_returns_all(self):
        recs = [_r(model_id="a"), _r(model_id="b")]
        assert [r.model_id for r in filter_records(recs)] == ["a", "b"]

    def test_filter_by_model_id(self):
        recs = [_r(model_id="a"), _r(model_id="b"), _r(model_id="a")]
        out = list(filter_records(recs, model_id="a"))
        assert [r.model_id for r in out] == ["a", "a"]

    def test_filter_by_stage(self):
        recs = [
            _r(model_id="a", stage="shadow"),
            _r(model_id="b", stage="advisory"),
        ]
        out = list(filter_records(recs, stage="advisory"))
        assert [r.model_id for r in out] == ["b"]

    def test_filter_by_since_inclusive(self):
        recs = [
            _r(model_id="a", ts="2026-05-10T11:00:00+00:00"),
            _r(model_id="b", ts="2026-05-10T12:00:00+00:00"),
            _r(model_id="c", ts="2026-05-10T13:00:00+00:00"),
        ]
        cutoff = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
        out = list(filter_records(recs, since=cutoff))
        assert [r.model_id for r in out] == ["b", "c"]

    def test_filter_by_naive_since_assumed_utc(self):
        recs = [_r(model_id="a", ts="2026-05-10T11:00:00+00:00")]
        cutoff = datetime(2026, 5, 10, 10)  # naive
        out = list(filter_records(recs, since=cutoff))
        assert [r.model_id for r in out] == ["a"]


class TestAggregate:
    def test_per_model_per_stage_grouping(self):
        recs = [
            _r(model_id="a", stage="shadow", score=0.1),
            _r(model_id="a", stage="shadow", score=0.5),
            _r(model_id="a", stage="advisory", score=0.9),
            _r(model_id="b", stage="shadow", score=0.7),
        ]
        stats = aggregate(recs)
        assert {(s.model_id, s.stage) for s in stats} == {
            ("a", "shadow"), ("a", "advisory"), ("b", "shadow"),
        }
        a_shadow = next(s for s in stats if s.model_id == "a" and s.stage == "shadow")
        assert a_shadow.count == 2
        assert a_shadow.score_mean == pytest.approx(0.3)
        assert a_shadow.score_min == pytest.approx(0.1)
        assert a_shadow.score_max == pytest.approx(0.5)

    def test_first_and_last_seen(self):
        recs = [
            _r(model_id="a", ts="2026-05-10T13:00:00+00:00"),
            _r(model_id="a", ts="2026-05-10T11:00:00+00:00"),
            _r(model_id="a", ts="2026-05-10T12:00:00+00:00"),
        ]
        stats = aggregate(recs)
        assert len(stats) == 1
        s = stats[0]
        assert s.first_seen == datetime(2026, 5, 10, 11, tzinfo=timezone.utc)
        assert s.last_seen == datetime(2026, 5, 10, 13, tzinfo=timezone.utc)

    def test_sorted_by_count_desc(self):
        recs = (
            [_r(model_id="b", score=0.5)] * 1
            + [_r(model_id="a", score=0.5)] * 3
        )
        stats = aggregate(recs)
        assert [s.model_id for s in stats] == ["a", "b"]

    def test_empty_returns_empty_list(self):
        assert aggregate([]) == []


class TestFormatTables:
    def test_inspect_orders_newest_first_and_respects_limit(self):
        recs = [
            _r(model_id="a", ts="2026-05-10T11:00:00+00:00"),
            _r(model_id="b", ts="2026-05-10T12:00:00+00:00"),
            _r(model_id="c", ts="2026-05-10T13:00:00+00:00"),
        ]
        out = format_inspect_table(recs, limit=2)
        lines = out.splitlines()
        # Header + separator + 2 rows = 4 lines.
        assert len(lines) == 4
        # Newest first.
        assert "c" in lines[2]
        assert "b" in lines[3]

    def test_inspect_empty_returns_empty_string(self):
        assert format_inspect_table([]) == ""

    def test_stats_empty_returns_empty_string(self):
        assert format_stats_table([]) == ""

    def test_stats_table_contains_expected_columns(self):
        recs = [_r(model_id="a"), _r(model_id="a"), _r(model_id="b")]
        out = format_stats_table(aggregate(recs))
        header = out.splitlines()[0]
        assert "model_id" in header
        assert "count" in header
        assert "mean" in header
        assert "first_seen" in header
