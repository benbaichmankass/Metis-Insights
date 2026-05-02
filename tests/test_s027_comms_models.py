"""Tests for src/comms/models.py — schema-aligned dataclass validation + round-trips."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.comms.models import (
    Answer,
    Choice,
    CommsValidationError,
    Question,
    Request,
    Response,
    SCHEMA_VERSION,
    make_request_id,
    required_answered_count,
)
from src.comms.state import ANSWER_STATUS, STATUS

REPO_ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# Fixtures

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _basic_req(**overrides) -> Request:
    kwargs: dict = dict(
        request_id="REQ-20260502-143015-acctmode",
        questions=[
            Question(
                question_id="mode",
                prompt="Default mode for the new account?",
                input_type="choice",
                choices=[Choice("live", "Live"), Choice("paper", "Paper")],
                allow_other=True,
            )
        ],
        topic="acct mode",
    )
    kwargs.update(overrides)
    return Request(**kwargs)


# ----------------------------------------------------------------------
# Choice / Question / Answer / Response validation

class TestChoice:
    def test_valid(self):
        c = Choice(id="live", label="Live")
        assert c.to_dict() == {"id": "live", "label": "Live"}

    @pytest.mark.parametrize("bad_id", ["", "Live", "live!", "x" * 41])
    def test_bad_id(self, bad_id):
        with pytest.raises(CommsValidationError):
            Choice(id=bad_id, label="Live")

    @pytest.mark.parametrize("bad_label", ["", "x" * 81])
    def test_bad_label(self, bad_label):
        with pytest.raises(CommsValidationError):
            Choice(id="x", label=bad_label)


class TestQuestion:
    def test_choice_requires_choices(self):
        with pytest.raises(CommsValidationError, match="requires choices"):
            Question(question_id="q1", prompt="?", input_type="choice")

    def test_multi_choice_requires_choices(self):
        with pytest.raises(CommsValidationError, match="requires choices"):
            Question(question_id="q1", prompt="?", input_type="multi_choice", choices=[])

    def test_free_text_no_choices_ok(self):
        q = Question(question_id="why", prompt="Why?", input_type="free_text")
        assert q.to_dict()["input_type"] == "free_text"

    def test_yes_no_no_choices_ok(self):
        q = Question(question_id="ok", prompt="OK?", input_type="yes_no")
        assert q.to_dict()["input_type"] == "yes_no"

    def test_unknown_input_type_rejected(self):
        with pytest.raises(CommsValidationError, match="input_type"):
            Question(question_id="q", prompt="?", input_type="numeric")

    def test_duplicate_choice_ids_rejected(self):
        with pytest.raises(CommsValidationError, match="duplicate"):
            Question(
                question_id="q",
                prompt="?",
                input_type="choice",
                choices=[Choice("a", "A"), Choice("a", "A again")],
            )

    def test_default_choice_must_be_in_choices(self):
        with pytest.raises(CommsValidationError, match="default_choice"):
            Question(
                question_id="q",
                prompt="?",
                input_type="choice",
                choices=[Choice("a", "A")],
                default_choice="b",
            )

    def test_round_trip(self):
        original = Question(
            question_id="mode",
            prompt="?",
            input_type="choice",
            choices=[Choice("a", "A"), Choice("b", "B")],
            allow_other=True,
            default_choice="a",
        )
        copy = Question.from_dict(original.to_dict())
        assert copy.to_dict() == original.to_dict()


class TestAnswer:
    def test_valid(self):
        a = Answer(
            question_id="mode",
            answer_type="choice",
            received_at=_now().isoformat(),
            selected_ids=["live"],
        )
        assert a.to_dict()["selected_ids"] == ["live"]

    @pytest.mark.parametrize("bad_type", ["numeric", "", "CHOICE"])
    def test_bad_answer_type(self, bad_type):
        with pytest.raises(CommsValidationError):
            Answer(question_id="x", answer_type=bad_type, received_at=_now().isoformat())


class TestResponse:
    def test_valid(self):
        r = Response(
            request_id="REQ-20260502-143015-acctmode",
            answered_at=_now().isoformat(),
            answers=[],
        )
        assert r.status == ANSWER_STATUS.PARTIAL

    def test_bad_request_id(self):
        with pytest.raises(CommsValidationError):
            Response(request_id="not-a-req-id", answered_at=_now().isoformat(), answers=[])

    def test_bad_status(self):
        with pytest.raises(CommsValidationError):
            Response(
                request_id="REQ-20260502-143015-acctmode",
                answered_at=_now().isoformat(),
                answers=[],
                status="finished",
            )


# ----------------------------------------------------------------------
# Request

class TestRequest:
    def test_valid(self):
        r = _basic_req()
        assert r.status == STATUS.PENDING
        assert r.schema_version == SCHEMA_VERSION

    def test_id_format_enforced(self):
        with pytest.raises(CommsValidationError, match="request_id"):
            _basic_req(request_id="REQ-bad")

    def test_no_questions_rejected(self):
        with pytest.raises(CommsValidationError, match="at least one"):
            _basic_req(questions=[])

    def test_too_many_questions_rejected(self):
        qs = [
            Question(question_id=f"q{i}", prompt="?", input_type="yes_no")
            for i in range(11)
        ]
        with pytest.raises(CommsValidationError, match="10 questions"):
            _basic_req(questions=qs)

    def test_duplicate_question_ids_rejected(self):
        with pytest.raises(CommsValidationError, match="duplicate question_id"):
            _basic_req(questions=[
                Question(question_id="q1", prompt="?", input_type="yes_no"),
                Question(question_id="q1", prompt="?", input_type="yes_no"),
            ])

    def test_unknown_status_rejected(self):
        with pytest.raises(CommsValidationError, match="status"):
            _basic_req(status="weird")

    def test_unknown_schema_version_rejected(self):
        with pytest.raises(CommsValidationError, match="schema_version"):
            _basic_req(schema_version=99)

    def test_unknown_default_on_timeout_rejected(self):
        with pytest.raises(CommsValidationError, match="default_on_timeout"):
            _basic_req(default_on_timeout="weird")

    def test_required_question_ids(self):
        r = _basic_req(questions=[
            Question(question_id="a", prompt="?", input_type="yes_no", required=True),
            Question(question_id="b", prompt="?", input_type="yes_no", required=False),
        ])
        assert r.required_question_ids() == ["a"]

    def test_round_trip_preserves_payload(self):
        r = _basic_req()
        r.append_history(from_status=None, to_status="pending", actor="claude")
        copy = Request.from_dict(r.to_dict())
        assert copy.to_dict() == r.to_dict()

    def test_round_trip_with_response(self):
        r = _basic_req()
        r.response = Response(
            request_id=r.request_id,
            answered_at=_now().isoformat(),
            answers=[Answer(
                question_id="mode",
                answer_type="choice",
                received_at=_now().isoformat(),
                selected_ids=["live"],
            )],
            status=ANSWER_STATUS.COMPLETE,
        )
        copy = Request.from_dict(r.to_dict())
        assert copy.response is not None
        assert copy.response.answers[0].selected_ids == ["live"]
        assert copy.to_dict() == r.to_dict()

    def test_is_expired_with_past_deadline(self):
        past = (_now() - timedelta(hours=1)).isoformat()
        r = _basic_req(expires_at=past)
        assert r.is_expired() is True

    def test_is_expired_with_future_deadline(self):
        future = (_now() + timedelta(hours=1)).isoformat()
        r = _basic_req(expires_at=future)
        assert r.is_expired() is False

    def test_is_expired_with_no_deadline(self):
        r = _basic_req()
        assert r.is_expired() is False

    def test_is_expired_malformed_returns_false(self):
        r = _basic_req(expires_at="not a timestamp")
        assert r.is_expired() is False

    def test_is_terminal(self):
        for status in (STATUS.ACKNOWLEDGED, STATUS.EXPIRED, STATUS.CANCELLED):
            assert _basic_req(status=status).is_terminal()
        for status in (STATUS.PENDING, STATUS.SENT, STATUS.PARTIALLY_ANSWERED, STATUS.ANSWERED):
            assert not _basic_req(status=status).is_terminal()


# ----------------------------------------------------------------------
# make_request_id

class TestMakeRequestId:
    def test_format(self):
        rid = make_request_id(slug="acctmode")
        # REQ-YYYYMMDD-HHMMSS-acctmode
        assert rid.startswith("REQ-")
        parts = rid.split("-")
        assert len(parts) == 4
        assert len(parts[1]) == 8
        assert len(parts[2]) == 6
        assert parts[3] == "acctmode"

    def test_normalises_slug(self):
        rid = make_request_id(slug="ACCT MODE!!")
        assert rid.endswith("-acctmode")

    def test_short_slug_rejected(self):
        with pytest.raises(CommsValidationError):
            make_request_id(slug="ab")

    def test_long_slug_truncated(self):
        rid = make_request_id(slug="a" * 50)
        assert rid.split("-")[3] == "a" * 12

    def test_deterministic_with_clock(self):
        fixed = datetime(2026, 5, 2, 14, 30, 15, tzinfo=timezone.utc)
        rid = make_request_id(slug="acctmode", now=fixed)
        assert rid == "REQ-20260502-143015-acctmode"


# ----------------------------------------------------------------------
# required_answered_count

class TestRequiredAnsweredCount:
    def test_counts_only_required_questions(self):
        r = _basic_req(questions=[
            Question(question_id="a", prompt="?", input_type="yes_no", required=True),
            Question(question_id="b", prompt="?", input_type="yes_no", required=True),
            Question(question_id="c", prompt="?", input_type="yes_no", required=False),
        ])
        answers = [
            Answer(question_id="a", answer_type="yes_no", received_at=_now().isoformat(), selected_ids=["yes"]),
            Answer(question_id="c", answer_type="yes_no", received_at=_now().isoformat(), selected_ids=["yes"]),
        ]
        assert required_answered_count(r, answers) == 1


# ----------------------------------------------------------------------
# Schema files exist and parse

class TestSchemaFiles:
    """The schemas are reference contracts; ensure they parse + match the model regex."""

    def test_request_schema_loads(self):
        path = REPO_ROOT / "comms" / "schema" / "request.schema.json"
        data = json.loads(path.read_text())
        assert data["title"] == "Comms Request"
        assert data["properties"]["schema_version"]["const"] == SCHEMA_VERSION

    def test_response_schema_loads(self):
        path = REPO_ROOT / "comms" / "schema" / "response.schema.json"
        data = json.loads(path.read_text())
        assert data["title"] == "Comms Response"

    def test_schema_request_id_pattern_matches_our_regex(self):
        from src.comms.models import REQUEST_ID_RE
        path = REPO_ROOT / "comms" / "schema" / "request.schema.json"
        data = json.loads(path.read_text())
        schema_pattern = data["properties"]["request_id"]["pattern"]
        assert schema_pattern == REQUEST_ID_RE.pattern
