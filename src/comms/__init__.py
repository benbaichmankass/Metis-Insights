"""
Operator-communication channel between Claude and the human operator.

The comms module owns a file-based ask/answer protocol:

    Claude writes a request artifact (`comms/requests/REQ-*.json`) →
    git-sync delivers it to the VM →
    the Telegram bot polls, sends the menu, captures the operator's reply →
    the bot writes the response back into the same artifact →
    git-sync delivers it back →
    Claude reads the answers and continues.

Public surface is intentionally tiny:

    from src.comms import (
        Request, Question, Choice, Response, Answer,
        STATUS, ANSWER_STATUS,
        RequestStore, can_transition, log_event,
    )

The bot integration (Telegram handlers, callback routing) lives in
``src/bot/comms_handler.py`` (deferred to PR 2 — see
``docs/claude/comms-architecture.md`` § Implementation phases).
"""
from __future__ import annotations

from .log import log_event
from .models import Answer, Choice, Question, Request, Response
from .state import ANSWER_STATUS, STATUS, can_transition, next_status_after_answer
from .store import RequestStore

__all__ = [
    "ANSWER_STATUS",
    "Answer",
    "Choice",
    "Question",
    "Request",
    "RequestStore",
    "Response",
    "STATUS",
    "can_transition",
    "log_event",
    "next_status_after_answer",
]
