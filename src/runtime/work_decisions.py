"""The decision round-trip — the OPERATOR's half of the operating layer.

Phase H of ``docs/design/operating-layer-build-plan-DESIGN.md``. The model says
decisions must be answerable **from the UI**, *"so the operator is not the
bottleneck on their own decisions"* — and the measured constraint is exactly
that: **DECISION**, 256 of 370 research units superseded unread, 1 of 117
dispositions actioned.

This module owns three things and nothing else: what a decision REQUEST is, what
a SUBMITTED answer is, and how the two grade into a state a consumer may render.

────────────────────────────────────────────────────────────────────────────
THE ONE RULE THAT SHAPES EVERYTHING HERE: **the repo is the source of truth,
and this module holds no truth at rest.**
────────────────────────────────────────────────────────────────────────────

``operating-layer-schema-and-state-DESIGN.md`` § 2 settles it: the live layer
holds *observations* and *truth in transit*, never truth. So:

* The **question** lives in the repo — ``decision_requests[]`` on a work object,
  paired with a ``blocked_on`` edge of ``kind: operator_decision``.
* The **answer**, once it is truth, lives in the repo too — an ``answer`` block
  nested inside that same request.
* The live layer holds ONLY the in-between: an append-only JSONL of submissions
  that have been made and not yet committed to the repo.

**The consequence is that ``committed`` is DERIVED FROM THE REPO, never from a
flag in the transit log**, and that is deliberate rather than incidental. A
"state" column in the transit log would be a second, drifting record of a fact
the repo already holds — and, worse, it would let a lost commit *look* answered.
Reading committedness off the object file makes the transit contract structural:

    TRANSIT FAILS BACK, NEVER FORWARD.

An answer that does not reach the repo leaves its question **unanswered** — not
"answered", not ambiguous. A question wrongly shown as answered is a decision
nobody made, which is the one outcome a decision channel must never produce.

**Open windows are enumerable and close observably.** Every ``in_transit`` row
is listable at any moment with the age of its window, so a submission that never
committed is a reportable condition rather than a silent one.

────────────────────────────────────────────────────────────────────────────
FOUR ANSWER STATES, NEVER COLLAPSED
────────────────────────────────────────────────────────────────────────────

``not_submitted``  nobody has answered
``in_transit``     an answer was submitted and has NOT reached the repo — the
                   question is still **unanswered**, and the window is open
``committed``      the answer is in the repo; this is the only state that means
                   *decided*
``unreadable``     **we could not look.** The transit log could not be read, so
                   we cannot say whether an answer is in flight

The pair that matters is ``unreadable`` vs ``not_submitted``. Collapsing them
reports *"we could not read the channel"* as *"the operator has not answered"* —
which would put a question on the operator's plate that they may already have
answered, and would make a broken transit log indistinguishable from a quiet
one. That is the same distinction ``exit_anchor.py``'s ``deferred``/``no_anchor``
makes, and the reason ``collapsed-state-guard`` exists.

⚠️ ``committed`` is graded from the REPO and therefore survives an unreadable
transit log — a decision already made cannot be un-made by a read failure.

Observe-only from the trader's point of view: nothing here reads back into an
order path, and no route in this module can place, modify or refuse a trade.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

# ── the four answer states ───────────────────────────────────────────────────
NOT_SUBMITTED = "not_submitted"
IN_TRANSIT = "in_transit"
COMMITTED = "committed"
UNREADABLE = "unreadable"

ANSWER_STATES: tuple[str, ...] = (NOT_SUBMITTED, IN_TRANSIT, COMMITTED, UNREADABLE)

# ── how we got (or did not get) the transit log ──────────────────────────────
# Deliberately separate from the answer states above: this says whether the
# CHANNEL could be read, which is a different question from what any one
# request's answer state is. `absent` is a positive reading (an append-only log
# that has never been written has nothing in it); `unreadable` is not.
TRANSIT_READ = "read"
TRANSIT_ABSENT = "absent"
TRANSIT_UNREADABLE = "unreadable"

# How long a submitted-but-uncommitted window may stand before it is a
# REPORTABLE condition. A bound, not an enforcement: nothing here expires a
# submission — an answer the operator gave must not evaporate because a
# committer was slow. It only makes the open window visible.
STALE_TRANSIT_SECONDS = int(os.environ.get("WORK_DECISION_STALE_SECONDS", "3600") or 3600)

_TRANSIT_BASENAME = "work_decision_transit.jsonl"

# Bound on what one submission may carry, so the write route cannot be used to
# grow an unbounded file on the live box.
MAX_FREE_TEXT_CHARS = 2000
MAX_TRANSIT_BYTES = 4_000_000


def transit_log_path() -> Path:
    """The live layer's ONLY file. Holds truth in transit and nothing else."""
    return Path(runtime_logs_dir()) / _TRANSIT_BASENAME


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# The REQUEST — read out of a work object
# ─────────────────────────────────────────────────────────────────────────────


def normalise_option(raw: Any) -> dict[str, Any] | None:
    """One multiple-choice option: ``{key, label, implication}``.

    ``key`` is what a submission names, so an option without one is not
    answerable and is dropped rather than given a synthesised key — a
    synthesised key would let a submission select something the author never
    wrote down.
    """
    if not isinstance(raw, dict):
        return None
    key = raw.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    return {
        "key": key.strip(),
        "label": raw.get("label") if isinstance(raw.get("label"), str) else None,
        "implication": (
            raw.get("implication") if isinstance(raw.get("implication"), str) else None
        ),
    }


def normalise_answer(raw: Any) -> dict[str, Any] | None:
    """The COMMITTED answer, read from the object file in the repo.

    An ``answer`` block that names no ``chosen`` option and carries no free text
    is **not** an answer: it is returned as ``None`` so the request grades
    ``not_submitted`` rather than ``committed``. Failing back is the whole
    contract — a half-written block must not read as a decision.
    """
    if not isinstance(raw, dict):
        return None
    chosen = raw.get("chosen")
    free_text = raw.get("free_text")
    has_choice = isinstance(chosen, str) and bool(chosen.strip())
    has_text = isinstance(free_text, str) and bool(free_text.strip())
    if not has_choice and not has_text:
        return None
    return {
        "chosen": chosen.strip() if has_choice else None,
        "freeText": free_text.strip() if has_text else None,
        "answeredAt": raw.get("answered_at") if isinstance(raw.get("answered_at"), str) else None,
        "answeredBy": raw.get("answered_by") if isinstance(raw.get("answered_by"), str) else None,
        "committedBy": (
            raw.get("committed_by") if isinstance(raw.get("committed_by"), str) else None
        ),
        "submissionId": (
            raw.get("submission_id") if isinstance(raw.get("submission_id"), str) else None
        ),
    }


def normalise_requests(object_data: dict[str, Any], object_id: str) -> list[dict[str, Any]]:
    """Every answerable decision request declared on one work object.

    A request with no ``id`` is DROPPED and the caller is expected to report the
    count, because a request that cannot be addressed by a submission is not a
    channel — it is a paragraph. Silently synthesising an id would make an
    unanswerable question look answerable.
    """
    raw_list = object_data.get("decision_requests")
    if not isinstance(raw_list, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        req_id = raw.get("id")
        if not isinstance(req_id, str) or not req_id.strip():
            continue
        options = [o for o in (normalise_option(o) for o in raw.get("options") or []) if o]
        allows_free_text = raw.get("allows_free_text")
        out.append(
            {
                "id": req_id.strip(),
                "objectId": object_id,
                "question": raw.get("question") if isinstance(raw.get("question"), str) else None,
                "options": options,
                # An explicit `false` closes free text; anything else leaves it
                # open, because a decision channel that can only pick from a
                # list cannot carry "none of these, and here is why".
                "allowsFreeText": allows_free_text is not False,
                "urgency": raw.get("urgency") if raw.get("urgency") in ("routine", "blocking") else "routine",
                "askedOn": str(raw.get("asked_on")) if raw.get("asked_on") is not None else None,
                "context": raw.get("context") if isinstance(raw.get("context"), str) else None,
                "answer": normalise_answer(raw.get("answer")),
            }
        )
    return out


def malformed_request_count(object_data: dict[str, Any]) -> int:
    """How many declared requests were DROPPED for having no usable ``id``.

    Reported rather than swallowed: a question the operator can see and cannot
    answer is worse than one that was never asked.
    """
    raw_list = object_data.get("decision_requests")
    if not isinstance(raw_list, list):
        return 0
    usable = 0
    for raw in raw_list:
        if isinstance(raw, dict):
            req_id = raw.get("id")
            if isinstance(req_id, str) and req_id.strip():
                usable += 1
    return len(raw_list) - usable


# ─────────────────────────────────────────────────────────────────────────────
# The TRANSIT log — the live layer's only content
# ─────────────────────────────────────────────────────────────────────────────


def read_transit(path: Path | None = None) -> tuple[list[dict[str, Any]], str, str | None]:
    """Return ``(rows, state, error)`` where ``state`` is one of the three
    ``TRANSIT_*`` values.

    ⚠️ A missing file is ``absent``, NOT ``unreadable``. An append-only log that
    has never been written genuinely contains nothing, and that is a positive
    reading. A file we could not open is *we did not look* and must never be
    served as "nothing has been submitted".

    A single malformed line does not make the whole log unreadable — it is
    skipped and counted by the caller through the returned row count, because
    one bad append must not hide every good submission behind it.
    """
    p = path or transit_log_path()
    try:
        if not p.exists():
            return [], TRANSIT_ABSENT, None
    except OSError as exc:
        return [], TRANSIT_UNREADABLE, str(exc)
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], TRANSIT_UNREADABLE, str(exc)
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, TRANSIT_READ, None


def latest_submissions(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Newest submission per ``(object_id, request_id)``.

    Append-only, last-write-wins — the operator may change their mind before the
    answer commits, and the file keeps every attempt as the audit trail.
    """
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        obj_id, req_id = row.get("object_id"), row.get("request_id")
        if not isinstance(obj_id, str) or not isinstance(req_id, str):
            continue
        key = (obj_id, req_id)
        prev = latest.get(key)
        if prev is None:
            latest[key] = row
            continue
        a, b = _parse_iso(row.get("submitted_at")), _parse_iso(prev.get("submitted_at"))
        # Undateable rows lose to a dated one; between two undateable rows, file
        # order decides. Never guess a timestamp — a synthesised one would sort.
        if a is not None and (b is None or a >= b):
            latest[key] = row
        elif a is None and b is None:
            latest[key] = row
    return latest


def append_submission(
    *,
    object_id: str,
    request_id: str,
    chosen: str | None,
    free_text: str | None,
    submitted_by: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Append ONE submission. Returns the row as written.

    Raises ``OSError`` on a write failure — deliberately NOT swallowed. A
    submission that silently failed to land would leave the operator believing
    they had answered, which is the forward failure this whole module refuses.
    """
    p = path or transit_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.stat().st_size > MAX_TRANSIT_BYTES:
        raise OSError(
            f"transit log exceeds {MAX_TRANSIT_BYTES} bytes; commit the open "
            f"windows before submitting more"
        )
    row = {
        "submission_id": uuid.uuid4().hex,
        "object_id": object_id,
        "request_id": request_id,
        "chosen": chosen,
        "free_text": (free_text or None),
        "submitted_at": _utcnow_iso(),
        "submitted_by": submitted_by or "operator",
        # ⚠️ There is deliberately NO `state` field. Committedness is read from
        # the repo; a state column here would be a second record of a fact the
        # repo already holds, and a lost commit would look answered.
        "schema": 1,
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Grading
# ─────────────────────────────────────────────────────────────────────────────


def grade_answer_state(
    request: dict[str, Any],
    submission: dict[str, Any] | None,
    transit_state: str,
) -> str:
    """The four-state grade. See the module docstring for why each is distinct.

    Order is load-bearing: the repo is checked FIRST, so a committed decision
    survives an unreadable transit log. A decision already made cannot be
    un-made by a read failure.
    """
    if request.get("answer"):
        return COMMITTED
    if transit_state == TRANSIT_UNREADABLE:
        return UNREADABLE
    if submission is not None:
        return IN_TRANSIT
    return NOT_SUBMITTED


def transit_window(submission: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Age of an open transit window, so it closes OBSERVABLY.

    ``ageSeconds`` is ``None`` — never ``0`` — when the submission carries no
    parsable timestamp. Zero is a real reading (submitted just now); *we cannot
    date it* is not, and a fabricated zero would make an ancient open window
    render as fresh.
    """
    if submission is None:
        return {"submissionId": None, "submittedAt": None, "ageSeconds": None, "stale": None}
    submitted_at = submission.get("submitted_at")
    parsed = _parse_iso(submitted_at)
    if parsed is None:
        return {
            "submissionId": submission.get("submission_id"),
            "submittedAt": submitted_at if isinstance(submitted_at, str) else None,
            "ageSeconds": None,
            # Undateable: we cannot show it is fresh, and the fail-safe reading
            # of an open write window is that it is stale.
            "stale": True,
        }
    ref = now or datetime.now(timezone.utc)
    age = int((ref - parsed).total_seconds())
    return {
        "submissionId": submission.get("submission_id"),
        "submittedAt": parsed.isoformat().replace("+00:00", "Z"),
        "ageSeconds": age,
        "stale": age > STALE_TRANSIT_SECONDS,
    }


def render_answer_yaml_block(
    *,
    chosen: str | None,
    free_text: str | None,
    answered_at: str,
    answered_by: str,
    committed_by: str,
    submission_id: str,
) -> dict[str, Any]:
    """The exact mapping a committer writes into the object's request.

    Kept here rather than in the committer so the writer and the reader
    (``normalise_answer``) share one definition of the shape. Two copies is how
    a committed answer starts failing to grade as committed.
    """
    return {
        "chosen": chosen,
        "free_text": free_text,
        "answered_at": answered_at,
        "answered_by": answered_by,
        "committed_by": committed_by,
        "submission_id": submission_id,
    }


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    Used by the committer. A half-written work object is a corrupted source of
    truth, and the store's whole point is that the repo is the source of truth.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
