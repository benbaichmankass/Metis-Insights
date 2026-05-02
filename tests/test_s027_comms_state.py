"""Tests for src/comms/state.py — transition rules and helpers."""
from __future__ import annotations

import pytest

from src.comms.state import (
    ANSWER_STATUS,
    STATUS,
    can_transition,
    next_status_after_answer,
)


class TestCanTransition:
    @pytest.mark.parametrize("src,dst", [
        (STATUS.PENDING, STATUS.SENT),
        (STATUS.PENDING, STATUS.CANCELLED),
        (STATUS.PENDING, STATUS.EXPIRED),
        (STATUS.SENT, STATUS.PARTIALLY_ANSWERED),
        (STATUS.SENT, STATUS.ANSWERED),
        (STATUS.SENT, STATUS.EXPIRED),
        (STATUS.SENT, STATUS.CANCELLED),
        (STATUS.PARTIALLY_ANSWERED, STATUS.ANSWERED),
        (STATUS.PARTIALLY_ANSWERED, STATUS.EXPIRED),
        (STATUS.PARTIALLY_ANSWERED, STATUS.CANCELLED),
        (STATUS.ANSWERED, STATUS.ACKNOWLEDGED),
    ])
    def test_legal_transitions(self, src, dst):
        assert can_transition(src, dst)

    @pytest.mark.parametrize("src,dst", [
        (STATUS.PENDING, STATUS.ANSWERED),       # must go via sent
        (STATUS.PENDING, STATUS.PARTIALLY_ANSWERED),
        (STATUS.PENDING, STATUS.ACKNOWLEDGED),
        (STATUS.SENT, STATUS.PENDING),           # no rewind
        (STATUS.SENT, STATUS.ACKNOWLEDGED),      # must go via answered
        (STATUS.PARTIALLY_ANSWERED, STATUS.SENT),
        (STATUS.PARTIALLY_ANSWERED, STATUS.PENDING),
        (STATUS.ANSWERED, STATUS.SENT),
        (STATUS.ANSWERED, STATUS.EXPIRED),       # answered is past expiry
        (STATUS.ACKNOWLEDGED, STATUS.PENDING),   # terminal
        (STATUS.ACKNOWLEDGED, STATUS.ANSWERED),
        (STATUS.EXPIRED, STATUS.PENDING),        # terminal
        (STATUS.CANCELLED, STATUS.PENDING),      # terminal
    ])
    def test_illegal_transitions(self, src, dst):
        assert not can_transition(src, dst)

    def test_unknown_source_returns_false(self):
        assert can_transition("not_a_status", STATUS.SENT) is False

    def test_unknown_target_returns_false(self):
        assert can_transition(STATUS.PENDING, "not_a_status") is False

    def test_terminal_states_have_no_outgoing(self):
        for terminal in STATUS.TERMINAL:
            for any_status in STATUS.ALL:
                assert not can_transition(terminal, any_status), (
                    f"terminal {terminal!r} should not transition to {any_status!r}"
                )


class TestNextStatusAfterAnswer:
    def test_all_required_answered_marks_answered(self):
        assert next_status_after_answer(total_required=3, answered_required=3) == STATUS.ANSWERED

    def test_some_required_answered_marks_partial(self):
        assert (
            next_status_after_answer(total_required=3, answered_required=1)
            == STATUS.PARTIALLY_ANSWERED
        )

    def test_zero_required_marks_answered_immediately(self):
        # All-optional request: any answer (even none) is "complete enough".
        assert next_status_after_answer(total_required=0, answered_required=0) == STATUS.ANSWERED

    def test_negative_required_marks_answered(self):
        assert next_status_after_answer(total_required=-1, answered_required=0) == STATUS.ANSWERED


class TestStatusConstants:
    def test_status_all_includes_every_state(self):
        expected = {
            "pending", "sent", "partially_answered", "answered",
            "acknowledged", "expired", "cancelled",
        }
        assert set(STATUS.ALL) == expected

    def test_terminal_subset(self):
        assert set(STATUS.TERMINAL).issubset(set(STATUS.ALL))
        assert set(STATUS.TERMINAL) == {"acknowledged", "expired", "cancelled"}

    def test_answer_status_all(self):
        assert set(ANSWER_STATUS.ALL) == {"partial", "complete", "invalid"}
