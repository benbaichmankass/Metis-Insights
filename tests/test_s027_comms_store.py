"""Tests for src/comms/store.py and src/comms/log.py — filesystem behaviour."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.comms.log import log_event
from src.comms.models import (
    Answer,
    Choice,
    CommsValidationError,
    Question,
    Request,
    Response,
)
from src.comms.state import ANSWER_STATUS, STATUS
from src.comms.store import RequestStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_request(
    request_id: str = "REQ-20260502-143015-acctmode",
    *,
    questions: list[Question] | None = None,
    status: str = STATUS.PENDING,
) -> Request:
    return Request(
        request_id=request_id,
        questions=questions or [
            Question(
                question_id="mode",
                prompt="Default mode for the new account?",
                input_type="choice",
                choices=[Choice("live", "Live"), Choice("paper", "Paper")],
                allow_other=True,
            )
        ],
        topic="acct mode",
        status=status,
    )


# ----------------------------------------------------------------------

class TestRequestStoreCreate:
    def test_create_writes_file(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        path = store.create(req)
        assert path.exists()
        assert path == tmp_path / "requests" / f"{req.request_id}.json"

    def test_create_writes_history_entry(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        loaded = store.load(req.request_id)
        assert len(loaded.history) == 1
        assert loaded.history[0]["to_status"] == STATUS.PENDING
        assert loaded.history[0]["from_status"] is None

    def test_create_refuses_to_overwrite(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        with pytest.raises(FileExistsError):
            store.create(req)

    def test_create_persists_full_payload(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        # Re-read the raw JSON to confirm the schema-aligned shape.
        path = tmp_path / "requests" / f"{req.request_id}.json"
        data = json.loads(path.read_text())
        assert data["request_id"] == req.request_id
        assert data["schema_version"] == 1
        assert data["status"] == STATUS.PENDING
        assert data["questions"][0]["choices"][0]["id"] == "live"
        assert data["source"]["actor"] == "claude"


class TestRequestStoreList:
    def test_list_active_yields_pending(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        store.create(_build_request("REQ-20260502-100000-acctaaaa"))
        store.create(_build_request("REQ-20260502-100100-acctbbbb"))
        active = list(store.list_active())
        assert {r.request_id for r in active} == {
            "REQ-20260502-100000-acctaaaa",
            "REQ-20260502-100100-acctbbbb",
        }

    def test_list_pending_filters_by_status(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        store.create(_build_request("REQ-20260502-100000-acctaaaa"))
        # Create a second one and transition it to sent.
        sent_req = _build_request("REQ-20260502-100100-acctbbbb")
        store.create(sent_req)
        store.mark_sent(sent_req)
        pending = store.list_pending()
        assert [r.request_id for r in pending] == ["REQ-20260502-100000-acctaaaa"]

    def test_list_active_skips_malformed(self, tmp_path: Path, caplog):
        store = RequestStore(tmp_path)
        store.create(_build_request("REQ-20260502-100000-acctaaaa"))
        # Drop a malformed file alongside.
        bad = tmp_path / "requests" / "REQ-20260502-100100-corruptt.json"
        bad.write_text("{not valid json")
        # Drop a structurally-bad one too.
        bad2 = tmp_path / "requests" / "REQ-20260502-100200-badsched.json"
        bad2.write_text(json.dumps({"request_id": "REQ-bad"}))
        active = list(store.list_active())
        assert {r.request_id for r in active} == {"REQ-20260502-100000-acctaaaa"}

    def test_list_active_empty_when_no_requests_dir(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        # Don't create anything; requests/ may not exist.
        assert list(store.list_active()) == []

    def test_list_awaiting_response(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        a = _build_request("REQ-20260502-100000-acctaaaa")
        b = _build_request("REQ-20260502-100100-acctbbbb")
        c = _build_request("REQ-20260502-100200-acctcccc")
        store.create(a)
        store.create(b)
        store.create(c)
        store.mark_sent(b)  # → sent
        store.transition(c, to_status=STATUS.CANCELLED, actor="claude")  # terminal soon
        awaiting = store.list_awaiting_response()
        assert [r.request_id for r in awaiting] == ["REQ-20260502-100100-acctbbbb"]


class TestRequestStoreTransition:
    def test_legal_transition_records_history(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        store.mark_sent(req, telegram_chat_id="123", telegram_message_id=42)
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.SENT
        assert loaded.delivery["sent_at"]
        assert loaded.delivery["telegram_chat_id"] == "123"
        assert loaded.delivery["telegram_message_id"] == 42
        assert loaded.delivery["send_attempts"] == 1
        assert any(
            h["from_status"] == STATUS.PENDING and h["to_status"] == STATUS.SENT
            for h in loaded.history
        )

    def test_illegal_transition_raises(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        with pytest.raises(CommsValidationError, match="illegal transition"):
            store.transition(req, to_status=STATUS.ACKNOWLEDGED)

    def test_mark_sent_refuses_when_not_pending(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        store.mark_sent(req)
        with pytest.raises(CommsValidationError, match="expected pending"):
            store.mark_sent(req)


class TestRequestStoreAttachResponse:
    def test_attach_response_complete(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        store.mark_sent(req)
        resp = Response(
            request_id=req.request_id,
            answered_at=_now(),
            answers=[Answer(
                question_id="mode",
                answer_type="choice",
                received_at=_now(),
                selected_ids=["live"],
            )],
            status=ANSWER_STATUS.COMPLETE,
        )
        store.attach_response(req, resp, new_status=STATUS.ANSWERED)
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.ANSWERED
        assert loaded.response is not None
        assert loaded.response.answers[0].selected_ids == ["live"]

    def test_attach_response_id_mismatch_raises(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        store.mark_sent(req)
        resp = Response(
            request_id="REQ-20260502-999999-different",
            answered_at=_now(),
            answers=[],
            status=ANSWER_STATUS.PARTIAL,
        )
        with pytest.raises(CommsValidationError, match="id mismatch"):
            store.attach_response(req, resp, new_status=STATUS.PARTIALLY_ANSWERED)


class TestRequestStoreArchive:
    def test_archive_terminal_moves_file(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        store.transition(req, to_status=STATUS.CANCELLED, actor="claude")
        archived = store.archive(req)
        assert archived == tmp_path / "archive" / f"{req.request_id}.json"
        assert archived.exists()
        assert not (tmp_path / "requests" / f"{req.request_id}.json").exists()

    def test_archive_non_terminal_raises(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        with pytest.raises(CommsValidationError, match="not terminal"):
            store.archive(req)

    def test_load_falls_back_to_archive(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        store.transition(req, to_status=STATUS.CANCELLED, actor="claude")
        store.archive(req)
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.CANCELLED


class TestAtomicity:
    def test_no_tmp_file_left_behind_on_normal_write(self, tmp_path: Path):
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        leftovers = list((tmp_path / "requests").glob(".*.tmp"))
        assert leftovers == []

    def test_concurrent_save_does_not_corrupt(self, tmp_path: Path):
        """Two saves in a row leave the file readable + matching the second write."""
        store = RequestStore(tmp_path)
        req = _build_request()
        store.create(req)
        # Round-trip mutate twice.
        req.topic = "first"
        store.save(req)
        req.topic = "second"
        store.save(req)
        loaded = store.load(req.request_id)
        assert loaded.topic == "second"


# ----------------------------------------------------------------------
# log_event

class TestLogEvent:
    def test_writes_one_ndjson_line(self, tmp_path: Path):
        log_path = tmp_path / "log.ndjson"
        log_event("request_created", request_id="REQ-1", actor="claude", log_path=log_path)
        lines = log_path.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "request_created"
        assert rec["request_id"] == "REQ-1"
        assert rec["actor"] == "claude"
        assert "at" in rec

    def test_appends_subsequent_calls(self, tmp_path: Path):
        log_path = tmp_path / "log.ndjson"
        for i in range(3):
            log_event("event", request_id=f"REQ-{i}", log_path=log_path)
        assert len(log_path.read_text().splitlines()) == 3

    def test_swallows_write_failure(self, tmp_path: Path, monkeypatch, caplog):
        # Point at a path where the parent is a regular file (mkdir will fail).
        bad_parent = tmp_path / "iamafile"
        bad_parent.write_text("x")
        log_path = bad_parent / "log.ndjson"
        # Should NOT raise.
        log_event("error", request_id="REQ-1", log_path=log_path)

    def test_includes_details_when_provided(self, tmp_path: Path):
        log_path = tmp_path / "log.ndjson"
        log_event(
            "answer_received",
            request_id="REQ-1",
            details={"question_id": "mode", "selected": ["live"]},
            log_path=log_path,
        )
        rec = json.loads(log_path.read_text().splitlines()[0])
        assert rec["details"] == {"question_id": "mode", "selected": ["live"]}
