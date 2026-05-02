"""
State machine for comms request lifecycle.

The artifact at ``comms/requests/REQ-*.json`` mutates through the states
below. Transitions are validated by ``can_transition``; consumers should
never write a status that isn't reachable from the current one.

    pending ──► sent ──► partially_answered ──► answered ──► acknowledged
       │         │              │
       │         └──┐           └──► answered ──► acknowledged
       │            ▼
       ├─► cancelled
       │
       └─► (sent/partially_answered) ──► expired

Terminal states: acknowledged, expired, cancelled.

The bot owns: pending → sent, sent → partially_answered → answered.
Claude owns: draft authoring (writes pending directly), answered → acknowledged.
The polling loop owns: → expired (on TTL elapse).
Either side can: → cancelled before delivery.
"""
from __future__ import annotations

from typing import Final, Mapping


class STATUS:
    """Request lifecycle states."""

    PENDING: Final = "pending"
    SENT: Final = "sent"
    PARTIALLY_ANSWERED: Final = "partially_answered"
    ANSWERED: Final = "answered"
    ACKNOWLEDGED: Final = "acknowledged"
    EXPIRED: Final = "expired"
    CANCELLED: Final = "cancelled"

    ALL: Final = (
        PENDING,
        SENT,
        PARTIALLY_ANSWERED,
        ANSWERED,
        ACKNOWLEDGED,
        EXPIRED,
        CANCELLED,
    )
    TERMINAL: Final = (ACKNOWLEDGED, EXPIRED, CANCELLED)
    AWAITING_DELIVERY: Final = (PENDING,)
    AWAITING_RESPONSE: Final = (SENT, PARTIALLY_ANSWERED)


class ANSWER_STATUS:
    """Per-response statuses (embedded inside the request's .response)."""

    PARTIAL: Final = "partial"
    COMPLETE: Final = "complete"
    INVALID: Final = "invalid"

    ALL: Final = (PARTIAL, COMPLETE, INVALID)


_TRANSITIONS: Mapping[str, frozenset[str]] = {
    STATUS.PENDING: frozenset({STATUS.SENT, STATUS.CANCELLED, STATUS.EXPIRED}),
    STATUS.SENT: frozenset(
        {STATUS.PARTIALLY_ANSWERED, STATUS.ANSWERED, STATUS.EXPIRED, STATUS.CANCELLED}
    ),
    STATUS.PARTIALLY_ANSWERED: frozenset(
        {STATUS.ANSWERED, STATUS.EXPIRED, STATUS.CANCELLED}
    ),
    STATUS.ANSWERED: frozenset({STATUS.ACKNOWLEDGED}),
    STATUS.ACKNOWLEDGED: frozenset(),
    STATUS.EXPIRED: frozenset(),
    STATUS.CANCELLED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """Return True iff a status transition from ``current`` to ``target`` is valid.

    Unknown source/target statuses both yield False — never raise. The bot
    polls a queue; a malformed file should be marked invalid via the response
    path, not crash the loop.
    """
    if current not in _TRANSITIONS:
        return False
    return target in _TRANSITIONS[current]


def next_status_after_answer(
    *, total_required: int, answered_required: int
) -> str:
    """Pick request.status when a new answer arrives.

    Bot calls this immediately after applying an answer to decide whether to
    move the request to ``answered`` (all required questions covered) or
    ``partially_answered`` (still waiting for more).
    """
    if total_required <= 0:
        return STATUS.ANSWERED
    return STATUS.ANSWERED if answered_required >= total_required else STATUS.PARTIALLY_ANSWERED
