"""
Dataclass models for comms requests/responses.

These mirror ``comms/schema/request.schema.json`` and
``comms/schema/response.schema.json``. Validation here is intentionally
lightweight — just enough to keep malformed artifacts out of the bot's
delivery loop. The schema files remain the canonical contract for any
external tooling.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from .state import ANSWER_STATUS, STATUS

SCHEMA_VERSION: int = 1

REQUEST_ID_RE = re.compile(r"^REQ-[0-9]{8}-[0-9]{6}-[a-z0-9]{4,12}$")
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")

INPUT_TYPES = ("choice", "multi_choice", "free_text", "yes_no")
ANSWER_TYPES = ("choice", "multi_choice", "free_text", "yes_no", "other")


class CommsValidationError(ValueError):
    """Raised when a request/response payload fails structural validation."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


@dataclass(frozen=True)
class Choice:
    id: str
    label: str

    def __post_init__(self) -> None:
        if not ID_RE.match(self.id):
            raise CommsValidationError(f"choice.id invalid: {self.id!r}")
        if not self.label or len(self.label) > 80:
            raise CommsValidationError("choice.label must be 1..80 chars")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label}


@dataclass
class Question:
    question_id: str
    prompt: str
    input_type: str
    choices: Optional[list[Choice]] = None
    allow_other: bool = False
    allow_free_text: bool = False
    required: bool = True
    default_choice: Optional[str] = None

    def __post_init__(self) -> None:
        if not ID_RE.match(self.question_id):
            raise CommsValidationError(f"question_id invalid: {self.question_id!r}")
        if not self.prompt or len(self.prompt) > 1000:
            raise CommsValidationError("question.prompt must be 1..1000 chars")
        if self.input_type not in INPUT_TYPES:
            raise CommsValidationError(
                f"question.input_type must be one of {INPUT_TYPES}, got {self.input_type!r}"
            )
        if self.input_type in ("choice", "multi_choice") and not self.choices:
            raise CommsValidationError(
                f"question {self.question_id!r}: choice/multi_choice requires choices"
            )
        if self.choices is not None:
            ids = [c.id for c in self.choices]
            if len(set(ids)) != len(ids):
                raise CommsValidationError(
                    f"question {self.question_id!r}: duplicate choice ids"
                )
            if self.default_choice is not None and self.default_choice not in ids:
                raise CommsValidationError(
                    f"question {self.question_id!r}: default_choice {self.default_choice!r} "
                    "not in choices"
                )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "question_id": self.question_id,
            "prompt": self.prompt,
            "input_type": self.input_type,
            "allow_other": self.allow_other,
            "allow_free_text": self.allow_free_text,
            "required": self.required,
        }
        if self.choices is not None:
            d["choices"] = [c.to_dict() for c in self.choices]
        if self.default_choice is not None:
            d["default_choice"] = self.default_choice
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Question":
        choices = data.get("choices")
        return cls(
            question_id=data["question_id"],
            prompt=data["prompt"],
            input_type=data["input_type"],
            choices=[Choice(**c) for c in choices] if choices else None,
            allow_other=bool(data.get("allow_other", False)),
            allow_free_text=bool(data.get("allow_free_text", False)),
            required=bool(data.get("required", True)),
            default_choice=data.get("default_choice"),
        )


@dataclass
class Answer:
    question_id: str
    answer_type: str
    received_at: str
    selected_ids: list[str] = field(default_factory=list)
    free_text: Optional[str] = None

    def __post_init__(self) -> None:
        if not ID_RE.match(self.question_id):
            raise CommsValidationError(f"answer.question_id invalid: {self.question_id!r}")
        if self.answer_type not in ANSWER_TYPES:
            raise CommsValidationError(
                f"answer_type must be one of {ANSWER_TYPES}, got {self.answer_type!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "question_id": self.question_id,
            "answer_type": self.answer_type,
            "received_at": self.received_at,
            "selected_ids": list(self.selected_ids),
        }
        if self.free_text is not None:
            d["free_text"] = self.free_text
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Answer":
        return cls(
            question_id=data["question_id"],
            answer_type=data["answer_type"],
            received_at=data["received_at"],
            selected_ids=list(data.get("selected_ids") or []),
            free_text=data.get("free_text"),
        )


@dataclass
class Response:
    request_id: str
    answered_at: str
    answers: list[Answer]
    status: str = ANSWER_STATUS.PARTIAL
    operator_telegram_user_id: Optional[int] = None
    operator_telegram_username: Optional[str] = None
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if not REQUEST_ID_RE.match(self.request_id):
            raise CommsValidationError(f"response.request_id invalid: {self.request_id!r}")
        if self.status not in ANSWER_STATUS.ALL:
            raise CommsValidationError(
                f"response.status must be one of {ANSWER_STATUS.ALL}, got {self.status!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "request_id": self.request_id,
            "answered_at": self.answered_at,
            "answers": [a.to_dict() for a in self.answers],
            "status": self.status,
        }
        op = _strip_none({
            "telegram_user_id": self.operator_telegram_user_id,
            "telegram_username": self.operator_telegram_username,
        })
        if op:
            d["operator"] = op
        if self.notes is not None:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Response":
        op = data.get("operator") or {}
        return cls(
            request_id=data["request_id"],
            answered_at=data["answered_at"],
            answers=[Answer.from_dict(a) for a in data.get("answers", [])],
            status=data.get("status", ANSWER_STATUS.PARTIAL),
            operator_telegram_user_id=op.get("telegram_user_id"),
            operator_telegram_username=op.get("telegram_username"),
            notes=data.get("notes"),
        )


@dataclass
class Request:
    request_id: str
    questions: list[Question]
    source_actor: str = "claude"
    created_at: str = field(default_factory=_utcnow_iso)
    schema_version: int = SCHEMA_VERSION
    expires_at: Optional[str] = None
    topic: Optional[str] = None
    context: Optional[str] = None
    default_on_timeout: str = "expire"
    status: str = STATUS.PENDING
    session_id: Optional[str] = None
    branch: Optional[str] = None
    pr_number: Optional[int] = None
    task: Optional[str] = None
    delivery: dict[str, Any] = field(default_factory=dict)
    response: Optional[Response] = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not REQUEST_ID_RE.match(self.request_id):
            raise CommsValidationError(f"request_id invalid: {self.request_id!r}")
        if self.schema_version != SCHEMA_VERSION:
            raise CommsValidationError(
                f"unsupported schema_version {self.schema_version} (expected {SCHEMA_VERSION})"
            )
        if self.source_actor not in ("claude", "operator", "system"):
            raise CommsValidationError(f"source.actor invalid: {self.source_actor!r}")
        if not self.questions:
            raise CommsValidationError("request must have at least one question")
        if len(self.questions) > 10:
            raise CommsValidationError("request limited to 10 questions per artifact")
        ids = [q.question_id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise CommsValidationError("duplicate question_id within request")
        if self.status not in STATUS.ALL:
            raise CommsValidationError(f"status invalid: {self.status!r}")
        if self.default_on_timeout not in ("expire", "use_defaults", "close"):
            raise CommsValidationError(
                f"default_on_timeout invalid: {self.default_on_timeout!r}"
            )

    def required_question_ids(self) -> list[str]:
        return [q.question_id for q in self.questions if q.required]

    def is_terminal(self) -> bool:
        return self.status in STATUS.TERMINAL

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            deadline = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return now >= deadline

    def to_dict(self) -> dict[str, Any]:
        source = _strip_none({
            "actor": self.source_actor,
            "session_id": self.session_id,
            "branch": self.branch,
            "pr_number": self.pr_number,
            "task": self.task,
        })
        d: dict[str, Any] = {
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "source": source,
            "questions": [q.to_dict() for q in self.questions],
            "status": self.status,
            "default_on_timeout": self.default_on_timeout,
        }
        if self.expires_at is not None:
            d["expires_at"] = self.expires_at
        if self.topic is not None:
            d["topic"] = self.topic
        if self.context is not None:
            d["context"] = self.context
        if self.delivery:
            d["delivery"] = dict(self.delivery)
        if self.response is not None:
            d["response"] = self.response.to_dict()
        if self.history:
            d["history"] = [dict(h) for h in self.history]
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Request":
        source = data.get("source") or {}
        response_data = data.get("response")
        return cls(
            request_id=data["request_id"],
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            created_at=data.get("created_at") or _utcnow_iso(),
            expires_at=data.get("expires_at"),
            source_actor=source.get("actor", "claude"),
            session_id=source.get("session_id"),
            branch=source.get("branch"),
            pr_number=source.get("pr_number"),
            task=source.get("task"),
            topic=data.get("topic"),
            context=data.get("context"),
            questions=[Question.from_dict(q) for q in data.get("questions", [])],
            default_on_timeout=data.get("default_on_timeout", "expire"),
            status=data.get("status", STATUS.PENDING),
            delivery=dict(data.get("delivery") or {}),
            response=Response.from_dict(response_data) if response_data else None,
            history=[dict(h) for h in (data.get("history") or [])],
        )

    def append_history(
        self, *, from_status: Optional[str], to_status: str,
        actor: Optional[str] = None, note: Optional[str] = None,
    ) -> None:
        entry: dict[str, Any] = {
            "at": _utcnow_iso(),
            "from_status": from_status,
            "to_status": to_status,
        }
        if actor is not None:
            entry["actor"] = actor
        if note is not None:
            entry["note"] = note
        self.history.append(entry)


def make_request_id(*, slug: str, now: Optional[datetime] = None) -> str:
    """Build a canonical request id: ``REQ-YYYYMMDD-HHMMSS-<slug>``.

    ``slug`` is normalised to lowercase ``[a-z0-9]+`` (4..12 chars) before
    embedding so the id always matches the schema regex.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "", slug.lower())
    if len(cleaned) < 4:
        raise CommsValidationError(
            f"slug must contain at least 4 [a-z0-9] characters after cleaning, got {slug!r}"
        )
    cleaned = cleaned[:12]
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return f"REQ-{ts}-{cleaned}"


def asdict_compact(obj: Any) -> dict[str, Any]:
    """Best-effort serialisation for arbitrary dataclass instances."""
    return _strip_none(asdict(obj))


def required_answered_count(
    request: Request, answers: Iterable[Answer]
) -> int:
    """Count how many of the request's *required* questions have an answer."""
    answered_ids = {a.question_id for a in answers}
    return sum(1 for q in request.questions if q.required and q.question_id in answered_ids)
