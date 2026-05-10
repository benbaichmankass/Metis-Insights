"""`review_journal` dataset family (S-AI-WS5-E).

Source-of-truth: comms request artifacts written by the
`/health-review` skill. The skill emits a JSON payload conforming
to `comms/schema/health_review_response.template.json`; the
operator pastes that payload into Telegram, the bot files it under
`comms/requests/REQ-*.json :: .response.answers[*].free_text`,
and answered requests are eventually archived to
`comms/archive/REQ-*.json`. The schema's `trade_decision_grades[]`
list carries one entry per closed/rejected trade in the review
window with letter grades + ordinal sub-grades for entry, exit,
and risk management.

Label: `decision_grade_score`, a numeric mapping of the letter
grade — `A→4, B→3, C→2, D→1, F→0`. Continuous-target convention
(per WS5-C) plus an ordinal interpretation that matches operator
intuition ("strategy averages a 3.2 over the past 30 trades").
Unknown / missing letters fall back to `-1.0` and are dropped.

Bookkeeping (not for training): `request_id`, `reviewed_at`,
`rationale`, `alternative_action`. Useful for audit + display.

Leakage: `decision_grade_score` is derived from the same
free-text payload as `decision_grade`; both are post-hoc reviewer
output. A trainer targeting `decision_grade_score` MUST exclude
`decision_grade`, `entry_quality`, `exit_quality`,
`risk_management`, `rationale`, and `alternative_action` from
features (all are review-side outputs). `leakage_test_status:
skipped` (trainer responsibility, same as the other label
families).

Empty-state behaviour: if the comms artifacts directory exists
but contains no answered requests with parseable grades, the
family yields zero rows. This is the expected state until the
operator starts answering health-review prompts with the JSON
template.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus

_GRADE_TO_SCORE: Mapping[str, float] = {
    "A": 4.0,
    "B": 3.0,
    "C": 2.0,
    "D": 1.0,
    "F": 0.0,
}


def _coerce_str(value: Any) -> str:
    return "" if value is None else str(value)


def _coerce_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_review_payload(text: str) -> Mapping[str, Any] | None:
    """Parse a free-text answer as a health-review response payload."""
    if not text or not text.strip():
        return None
    try:
        obj = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    if not isinstance(obj.get("trade_decision_grades"), list):
        return None
    return obj


def _iter_payloads(
    artifact_paths: list[Path],
) -> Iterator[tuple[Path, Mapping[str, Any], Mapping[str, Any]]]:
    """Yield (artifact_path, request_obj, review_payload) for each
    artifact whose response carries a parseable health-review payload.

    Each artifact may carry multiple `answers[]`; only the first one
    that parses as a review payload is used (the schema documents
    one answer per request)."""
    for path in artifact_paths:
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(request, dict):
            continue
        response = request.get("response")
        if not isinstance(response, dict):
            continue
        answers = response.get("answers")
        if not isinstance(answers, list):
            continue
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            payload = _parse_review_payload(answer.get("free_text", ""))
            if payload is not None:
                yield path, request, payload
                break


class ReviewJournalBuilder(DatasetBuilder):
    family: ClassVar[str] = "review_journal"
    builder_version: ClassVar[str] = "v1"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.SKIPPED
    label_version: ClassVar[str] = "grade-score-letter-v1"
    schema: ClassVar[Mapping[str, type]] = {
        "request_id": str,
        "reviewed_at": str,
        "trade_id": int,
        "timestamp": str,
        "symbol": str,
        "direction": str,
        "setup": str,
        "entry_price": float,
        "exit_price": float,
        "stop_loss": float,
        "take_profit_1": float,
        "position_size": float,
        "exit_reason": str,
        "decision_grade": str,
        "decision_grade_score": float,
        "entry_quality": str,
        "exit_quality": str,
        "risk_management": str,
        "rationale": str,
        "alternative_action": str,
    }

    def iter_rows(
        self,
        *,
        comms_root: Path,
        include_archive: bool = True,
        setup: str | None = None,
        symbol: str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if not comms_root.is_dir():
            raise FileNotFoundError(
                f"comms root not found at {comms_root}"
            )

        artifact_paths: list[Path] = []
        requests_dir = comms_root / "requests"
        if requests_dir.is_dir():
            artifact_paths.extend(
                sorted(requests_dir.glob("REQ-*.json"))
            )
        if include_archive:
            archive_dir = comms_root / "archive"
            if archive_dir.is_dir():
                artifact_paths.extend(
                    sorted(archive_dir.glob("REQ-*.json"))
                )

        for _, request, payload in _iter_payloads(artifact_paths):
            request_id = _coerce_str(request.get("request_id"))
            reviewed_at = _coerce_str(payload.get("reviewed_at"))
            grades = payload.get("trade_decision_grades", [])
            for grade in grades:
                if not isinstance(grade, dict):
                    continue
                letter = _coerce_str(grade.get("decision_grade")).strip().upper()
                score = _GRADE_TO_SCORE.get(letter)
                if score is None:
                    continue
                if setup is not None and _coerce_str(grade.get("setup")) != setup:
                    continue
                if symbol is not None and _coerce_str(grade.get("symbol")) != symbol:
                    continue

                payload_row: dict[str, Any] = {
                    "request_id": request_id,
                    "reviewed_at": reviewed_at,
                    "trade_id": _coerce_int(grade.get("trade_id")),
                    "timestamp": _coerce_str(grade.get("timestamp")),
                    "symbol": _coerce_str(grade.get("symbol")),
                    "direction": _coerce_str(grade.get("direction")),
                    "setup": _coerce_str(grade.get("setup")),
                    "entry_price": _coerce_float(grade.get("entry_price")),
                    "exit_price": _coerce_float(grade.get("exit_price")),
                    "stop_loss": _coerce_float(grade.get("stop_loss")),
                    "take_profit_1": _coerce_float(grade.get("take_profit_1")),
                    "position_size": _coerce_float(grade.get("position_size")),
                    "exit_reason": _coerce_str(grade.get("exit_reason")),
                    "decision_grade": letter,
                    "decision_grade_score": float(score),
                    "entry_quality": _coerce_str(grade.get("entry_quality")),
                    "exit_quality": _coerce_str(grade.get("exit_quality")),
                    "risk_management": _coerce_str(grade.get("risk_management")),
                    "rationale": _coerce_str(grade.get("rationale")),
                    "alternative_action": _coerce_str(
                        grade.get("alternative_action")
                    ),
                }
                yield payload_row
