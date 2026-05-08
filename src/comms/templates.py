"""
Comms request templates for operator-initiated commands.

The trader bot exposes a few operator commands (currently
``/new_session`` and ``/test``) that queue work for downstream
consumers — Claude, the M5 backtest workflow — by writing a
``comms/requests/REQ-…json`` artifact. This module is the small
factory that builds those artifacts from minimal operator input
(a sprint id, a strategy name).

Both helpers return a fully-validated :class:`src.comms.Request` ready
to hand to :class:`src.comms.RequestStore`. The handlers in
``src/bot/telegram_query_bot.py`` are responsible for persistence
(``RequestStore.create``) and git-push (``GitPusher.commit_and_push``);
this module deliberately has no I/O.

The audit doc that scoped this work is
``docs/audits/M1-comms-audit-followups-fresh.md`` § P1-D.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .models import (
    CommsValidationError,
    Question,
    Request,
    make_request_id,
)

NEW_SESSION_TASK_PREFIX = "new_session"
TEST_STRATEGY_TASK_PREFIX = "test_strategy"

# Commit-subject prefix used by the handlers. Distinct from the
# bot's response-writeback prefix (``comms(response):``) so the
# notify-on-pull filter and downstream consumers can tell the two
# apart.
COMMS_ASK_COMMIT_PREFIX = "comms(ask):"

# Slug regex enforced by ``make_request_id`` is [a-z0-9]{4..12}. We
# pad short identifiers with the prefix below so a short sprint id
# like "S-9" or strategy name like "x" still produces a valid id.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_for(prefix: str, raw: str) -> str:
    """Build a request-id slug from ``prefix + cleaned(raw)``.

    Cleaning strips everything outside ``[a-z0-9]`` and lowercases. The
    result is bounded to 12 chars by ``make_request_id``; we leave the
    truncation to it so the rule stays in one place.
    """
    cleaned = _SLUG_RE.sub("", raw.lower())
    return f"{prefix}{cleaned}"


def make_new_session_request(
    sprint_id: str,
    *,
    now: Optional[datetime] = None,
) -> Request:
    """Build a comms request for ``/new_session <sprint_id>``.

    The artifact is operator-initiated (``source.actor == "operator"``)
    and carries the sprint id in both ``source.task`` (machine-readable
    handle: ``new_session:<sprint_id>``) and the question prompt
    (operator-readable). ``default_on_timeout="close"`` so a stale
    queue entry never gets re-sent as a notification.
    """
    sprint_id = (sprint_id or "").strip()
    if not sprint_id:
        raise CommsValidationError("sprint_id required")
    if not _SLUG_RE.sub("", sprint_id.lower()):
        raise CommsValidationError(
            f"sprint_id must contain at least one alphanumeric char, got {sprint_id!r}"
        )
    request_id = make_request_id(slug=_slug_for("ns", sprint_id), now=now)
    return Request(
        request_id=request_id,
        source_actor="operator",
        task=f"{NEW_SESSION_TASK_PREFIX}:{sprint_id}",
        topic=f"New session: {sprint_id}",
        context=(
            f"Operator queued a new Claude session via /new_session for "
            f"sprint {sprint_id!r}. Claude reads this artifact on the "
            "next sync to bootstrap the session."
        ),
        questions=[
            Question(
                question_id="sprint_id",
                prompt=f"Sprint to start: {sprint_id}",
                input_type="free_text",
                required=False,
            )
        ],
        default_on_timeout="close",
    )


def make_test_strategy_request(
    strategy: str,
    *,
    now: Optional[datetime] = None,
) -> Request:
    """Build a comms request for ``/test <strategy>``.

    Operator-initiated; the M5 backtest workflow consumes the artifact
    and writes results back via the existing ``apply_answer`` writeback.
    The single question is ``results`` (free text) and is
    ``required=True`` so M5 has to fill it in to drive the request to
    ``answered``.
    """
    strategy = (strategy or "").strip()
    if not strategy:
        raise CommsValidationError("strategy required")
    if not _SLUG_RE.sub("", strategy.lower()):
        raise CommsValidationError(
            f"strategy must contain at least one alphanumeric char, got {strategy!r}"
        )
    request_id = make_request_id(slug=_slug_for("ts", strategy), now=now)
    return Request(
        request_id=request_id,
        source_actor="operator",
        task=f"{TEST_STRATEGY_TASK_PREFIX}:{strategy}",
        topic=f"Strategy test: {strategy}",
        context=(
            f"Operator requested via /test that the M5 backtest workflow "
            f"run a backtest for strategy {strategy!r} and write the "
            "results into this artifact."
        ),
        questions=[
            Question(
                question_id="results",
                prompt=f"Backtest results for strategy {strategy!r}",
                input_type="free_text",
                required=True,
            )
        ],
        default_on_timeout="expire",
    )


def commit_subject_for(request: Request) -> str:
    """Build the git commit subject for an operator-initiated request.

    Pinned in tests so the subject prefix stays stable for filters
    (``scripts/notify_on_pull.py``) and downstream consumers.
    """
    return f"{COMMS_ASK_COMMIT_PREFIX} {request.request_id} {request.topic or request.task or ''}".rstrip()
