"""Tests for `ReviewJournalBuilder` (S-AI-WS5-E)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml.datasets.families.review_journal import ReviewJournalBuilder
from ml.datasets.validate import validate_dataset


def _trade_grade(**overrides):
    base = dict(
        trade_id=1,
        timestamp="2026-05-01T12:00:00Z",
        symbol="BTCUSDT",
        direction="long",
        setup="vwap",
        entry_price=42000.0,
        exit_price=42500.0,
        stop_loss=41500.0,
        take_profit_1=43000.0,
        position_size=0.01,
        exit_reason="tp_cross",
        decision_grade="B",
        entry_quality="optimal",
        exit_quality="tp_appropriate",
        risk_management="correct",
        rationale="textbook setup",
        alternative_action="none",
    )
    base.update(overrides)
    return base


def _payload(**overrides):
    base = dict(
        request_id="REQ-20260501-120000-test",
        reviewed_at="2026-05-01T13:00:00+00:00",
        reviewer="claude",
        overall_assessment="healthy",
        findings={},
        anomalies=[],
        trade_decision_grades=[_trade_grade()],
        recommended_action="none",
        operator_attention_required=False,
    )
    base.update(overrides)
    return base


def _write_request(
    comms_root: Path,
    request_id: str,
    *,
    answered: bool,
    free_text: str | None = None,
    in_archive: bool = False,
) -> Path:
    target_dir = comms_root / ("archive" if in_archive else "requests")
    target_dir.mkdir(parents=True, exist_ok=True)
    obj: dict = {
        "request_id": request_id,
        "schema_version": 1,
        "created_at": "2026-05-01T12:00:00+00:00",
        "topic": "health review",
        "questions": [
            {"question_id": "review_json", "input_type": "free_text"}
        ],
        "status": "acknowledged" if answered else "pending",
    }
    if answered and free_text is not None:
        obj["response"] = {
            "answers": [
                {"question_id": "review_json", "free_text": free_text}
            ]
        }
    path = target_dir / f"{request_id}.json"
    path.write_text(json.dumps(obj))
    return path


class TestReviewJournalBuilder:
    def test_build_round_trip(self, tmp_path: Path):
        comms = tmp_path / "comms"
        _write_request(
            comms, "REQ-1", answered=True,
            free_text=json.dumps(_payload()),
        )
        builder = ReviewJournalBuilder()
        out = tmp_path / "datasets"
        paths = builder.build(
            output_dir=out,
            version="v001",
            source="comms-artifacts",
            commit_sha="abc",
            comms_root=comms,
        )
        assert paths.root == out / "review_journal" / "all" / "all" / "v001"

        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        row = emitted[0]
        assert row["decision_grade"] == "B"
        assert row["decision_grade_score"] == pytest.approx(3.0)
        assert row["setup"] == "vwap"
        assert row["request_id"] == "REQ-1"
        assert row["reviewed_at"] == "2026-05-01T13:00:00+00:00"

        metadata = json.loads(paths.metadata.read_text())
        assert metadata["family"] == "review_journal"
        assert metadata["leakage_test_status"] == "skipped"
        assert metadata["label_version"] == "grade-score-letter-v1"

        report = validate_dataset(paths.root)
        assert report.ok, report.errors

    def test_letter_grades_map_to_scores(self, tmp_path: Path):
        comms = tmp_path / "comms"
        grades = [
            _trade_grade(trade_id=i, decision_grade=letter)
            for i, letter in enumerate(["A", "B", "C", "D", "F"], start=1)
        ]
        _write_request(
            comms, "REQ-letters", answered=True,
            free_text=json.dumps(_payload(trade_decision_grades=grades)),
        )
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert [r["decision_grade_score"] for r in emitted] == [
            4.0, 3.0, 2.0, 1.0, 0.0,
        ]

    def test_unknown_grade_dropped(self, tmp_path: Path):
        comms = tmp_path / "comms"
        grades = [
            _trade_grade(trade_id=1, decision_grade="A"),
            _trade_grade(trade_id=2, decision_grade="X"),  # invalid
            _trade_grade(trade_id=3, decision_grade=""),    # empty
            _trade_grade(trade_id=4, decision_grade=None),  # null
        ]
        _write_request(
            comms, "REQ-mix", answered=True,
            free_text=json.dumps(_payload(trade_decision_grades=grades)),
        )
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["trade_id"] == 1

    def test_pending_request_yields_nothing(self, tmp_path: Path):
        comms = tmp_path / "comms"
        _write_request(comms, "REQ-pending", answered=False)
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 0

    def test_garbled_free_text_skipped(self, tmp_path: Path):
        comms = tmp_path / "comms"
        # Not JSON.
        _write_request(
            comms, "REQ-garbled", answered=True,
            free_text="not json at all",
        )
        # Valid JSON but missing trade_decision_grades.
        _write_request(
            comms, "REQ-no-grades", answered=True,
            free_text=json.dumps({"overall_assessment": "healthy"}),
        )
        # Valid payload but list is empty.
        _write_request(
            comms, "REQ-empty-list", answered=True,
            free_text=json.dumps(_payload(trade_decision_grades=[])),
        )
        # Valid payload with a real grade — this should produce a row.
        _write_request(
            comms, "REQ-good", answered=True,
            free_text=json.dumps(_payload()),
        )
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["request_id"] == "REQ-good"

    def test_archive_included_by_default(self, tmp_path: Path):
        comms = tmp_path / "comms"
        _write_request(
            comms, "REQ-active", answered=True,
            free_text=json.dumps(_payload()),
        )
        _write_request(
            comms, "REQ-archived", answered=True,
            free_text=json.dumps(_payload()),
            in_archive=True,
        )
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 2
        ids = sorted(r["request_id"] for r in emitted)
        assert ids == ["REQ-active", "REQ-archived"]

    def test_archive_excluded_when_flag_off(self, tmp_path: Path):
        comms = tmp_path / "comms"
        _write_request(
            comms, "REQ-active", answered=True,
            free_text=json.dumps(_payload()),
        )
        _write_request(
            comms, "REQ-archived", answered=True,
            free_text=json.dumps(_payload()),
            in_archive=True,
        )
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
            include_archive=False,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["request_id"] == "REQ-active"

    def test_setup_filter(self, tmp_path: Path):
        comms = tmp_path / "comms"
        grades = [
            _trade_grade(trade_id=1, setup="vwap"),
            _trade_grade(trade_id=2, setup="turtle_soup"),
        ]
        _write_request(
            comms, "REQ-setups", answered=True,
            free_text=json.dumps(_payload(trade_decision_grades=grades)),
        )
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms, setup="vwap",
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["setup"] == "vwap"

    def test_empty_comms_root_yields_nothing(self, tmp_path: Path):
        comms = tmp_path / "comms"
        comms.mkdir()
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
        )
        emitted = [
            line
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert emitted == []

    def test_missing_comms_root_raises(self, tmp_path: Path):
        builder = ReviewJournalBuilder()
        with pytest.raises(FileNotFoundError, match="comms root"):
            list(
                builder.iter_rows(comms_root=tmp_path / "nope")
            )

    def test_grade_letter_case_insensitive(self, tmp_path: Path):
        comms = tmp_path / "comms"
        _write_request(
            comms, "REQ-lower", answered=True,
            free_text=json.dumps(
                _payload(trade_decision_grades=[
                    _trade_grade(decision_grade="b"),
                ])
            ),
        )
        builder = ReviewJournalBuilder()
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001",
            source="x", commit_sha="x", comms_root=comms,
        )
        emitted = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(emitted) == 1
        assert emitted[0]["decision_grade"] == "B"
        assert emitted[0]["decision_grade_score"] == pytest.approx(3.0)


def test_registry_includes_review_journal():
    from ml.datasets import list_families, get_builder

    assert "review_journal" in list_families()
    assert isinstance(get_builder("review_journal"), ReviewJournalBuilder)
