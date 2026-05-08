"""Tests for the M1 P1-D operator commands ``/new_session`` and ``/test``.

These pin:

  - the ``src.comms.templates`` factory functions (artifact shape,
    schema validity, file naming);
  - the commit subject prefix used by the bot handlers
    (``comms(ask):``);
  - basic round-trip via ``RequestStore.create`` so a freshly minted
    artifact lands in ``comms/requests/`` and reloads cleanly.

The Telegram handler integration itself is not exercised here — the
command handlers wrap a thin call into ``store.create`` +
``GitPusher.commit_and_push`` and the existing comms handler tests
already cover the latter.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.comms import RequestStore
from src.comms.models import CommsValidationError
from src.comms.state import STATUS
from src.comms.templates import (
    COMMS_ASK_COMMIT_PREFIX,
    NEW_SESSION_TASK_PREFIX,
    TEST_STRATEGY_TASK_PREFIX,
    commit_subject_for,
    make_new_session_request,
    make_test_strategy_request,
)


def _now_fixed() -> datetime:
    return datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# make_new_session_request

class TestMakeNewSessionRequest:
    def test_happy_path(self):
        req = make_new_session_request("S-099", now=_now_fixed())
        assert req.source_actor == "operator"
        assert req.task == f"{NEW_SESSION_TASK_PREFIX}:S-099"
        assert req.topic == "New session: S-099"
        assert req.status == STATUS.PENDING
        assert req.default_on_timeout == "close"
        assert len(req.questions) == 1
        q = req.questions[0]
        assert q.question_id == "sprint_id"
        assert q.input_type == "free_text"
        assert q.required is False
        assert "S-099" in q.prompt

    def test_request_id_format(self):
        req = make_new_session_request("S-099", now=_now_fixed())
        # REQ-YYYYMMDD-HHMMSS-<slug>; slug starts with our "ns" prefix.
        assert req.request_id.startswith("REQ-20260508-120000-ns")
        assert "s099" in req.request_id

    def test_strips_whitespace(self):
        req = make_new_session_request("  S-099  ", now=_now_fixed())
        assert req.task.endswith("S-099")

    @pytest.mark.parametrize("bad", ["", "   ", "---", "/"])
    def test_rejects_empty_or_punct_only(self, bad):
        with pytest.raises(CommsValidationError):
            make_new_session_request(bad)

    def test_short_sprint_id_padded_via_prefix(self):
        # sprint id = "S-9" → cleaned alphanumeric is "s9" (2 chars), but
        # the slug is prefixed with "ns" so make_request_id sees "nss9"
        # (4 chars) and accepts it.
        req = make_new_session_request("S-9", now=_now_fixed())
        assert "s9" in req.request_id


# ----------------------------------------------------------------------
# make_test_strategy_request

class TestMakeTestStrategyRequest:
    def test_happy_path(self):
        req = make_test_strategy_request("vwap", now=_now_fixed())
        assert req.source_actor == "operator"
        assert req.task == f"{TEST_STRATEGY_TASK_PREFIX}:vwap"
        assert req.topic == "Strategy test: vwap"
        assert req.status == STATUS.PENDING
        assert req.default_on_timeout == "expire"
        assert len(req.questions) == 1
        q = req.questions[0]
        assert q.question_id == "results"
        assert q.input_type == "free_text"
        assert q.required is True

    def test_request_id_format(self):
        req = make_test_strategy_request("vwap", now=_now_fixed())
        assert req.request_id.startswith("REQ-20260508-120000-ts")
        assert "vwap" in req.request_id

    @pytest.mark.parametrize("bad", ["", "   ", "!!!"])
    def test_rejects_empty(self, bad):
        with pytest.raises(CommsValidationError):
            make_test_strategy_request(bad)


# ----------------------------------------------------------------------
# Commit subject + RequestStore round-trip

class TestCommitSubject:
    def test_new_session_prefix(self):
        req = make_new_session_request("S-099", now=_now_fixed())
        subject = commit_subject_for(req)
        assert subject.startswith(COMMS_ASK_COMMIT_PREFIX + " ")
        assert req.request_id in subject
        assert "S-099" in subject

    def test_test_strategy_prefix(self):
        req = make_test_strategy_request("vwap", now=_now_fixed())
        subject = commit_subject_for(req)
        assert subject.startswith(COMMS_ASK_COMMIT_PREFIX + " ")
        assert req.request_id in subject

    def test_prefix_distinct_from_response(self):
        # The bot's response writeback uses ``comms(response):``; this
        # prefix must differ so the notify-on-pull filter and downstream
        # consumers can tell asks from answers.
        from src.bot.comms_handler import COMMS_COMMIT_PREFIX as RESPONSE_PREFIX
        assert COMMS_ASK_COMMIT_PREFIX != RESPONSE_PREFIX


class TestRequestStoreRoundTrip:
    def test_new_session_lands_in_requests_dir(self, tmp_path: Path):
        store = RequestStore(tmp_path / "comms")
        req = make_new_session_request("S-099", now=_now_fixed())
        path = store.create(req)
        assert path.exists()
        assert path.parent.name == "requests"
        assert path.name == f"{req.request_id}.json"

    def test_test_strategy_lands_in_requests_dir(self, tmp_path: Path):
        store = RequestStore(tmp_path / "comms")
        req = make_test_strategy_request("vwap", now=_now_fixed())
        path = store.create(req)
        assert path.exists()
        assert path.parent.name == "requests"

    def test_artifact_round_trips_through_json(self, tmp_path: Path):
        store = RequestStore(tmp_path / "comms")
        req = make_new_session_request("S-099", now=_now_fixed())
        path = store.create(req)
        # Reload via store and via raw JSON; both must succeed.
        loaded = store.load(req.request_id)
        assert loaded.task == req.task
        assert loaded.source_actor == "operator"
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        assert raw["source"]["actor"] == "operator"
        assert raw["status"] == STATUS.PENDING

    def test_creates_one_history_entry_at_create(self, tmp_path: Path):
        store = RequestStore(tmp_path / "comms")
        req = make_test_strategy_request("vwap", now=_now_fixed())
        store.create(req)
        loaded = store.load(req.request_id)
        assert len(loaded.history) == 1
        assert loaded.history[0]["to_status"] == STATUS.PENDING
        assert loaded.history[0]["actor"] == "operator"
