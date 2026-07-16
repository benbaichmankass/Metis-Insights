"""Shadow-predictions audit-log inspector (S-AI-WS8-PART-1).

Pure-logic module that streams JSONL records out of
``runtime_logs/shadow_predictions.jsonl`` (the audit log
``ml.predictors.shadow.ShadowPredictor`` writes), filters them, and
aggregates per-model stats. Wraps the same logic a future
dashboard endpoint will read so we don't duplicate parsing.

Record shape (one JSON object per line, written by
``ShadowPredictor.predict``)::

    {
      "predicted_at_utc": "2026-05-10T21:00:00.123+00:00",
      "model_id": "vwap-shadow-v0",
      "stage": "shadow",
      "score": 0.42,
      "row_keys": ["confidence", "direction", ...]
    }

Malformed lines (truncated tails, partial writes, ill-formed JSON)
are logged at WARNING and skipped — never raise. The audit log is
operational data, not a source-of-truth artifact.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, MutableMapping

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowRecord:
    """Validated audit-log entry. Constructed from a raw dict via
    :func:`record_from_dict`; invariants checked at construction time
    so downstream consumers can trust the typed fields.

    ``feature_row`` (added 2026-05-19) carries the strategy's
    signal-time feature dict — ``strategy_name``, ``symbol``,
    ``direction``, ``confidence``, etc. ``None`` for older log lines
    written before the field existed, so consumers must treat it as
    optional. The trade↔score join in
    ``src/web/api/routers/trade_scores.py`` uses ``feature_row.symbol``
    when present and falls back to timestamp-window matching.

    ``backfill_kind`` (added 2026-05-19) marks records emitted by
    ``python -m ml backfill-shadow-predictions``. The CLI replays
    every historical trade through the current shadow-stage model
    set, stamps the resulting record with ``backfill_kind:
    "retroactive_decision"`` and a ``trade_id``, and writes the
    line to ``runtime_logs/shadow_predictions_backfill.jsonl``.
    Real-time records leave both fields unset, so consumers can
    cleanly distinguish: ``trade_scores`` joins by ``trade_id``
    when present; ``shadow-drift`` excludes backfill records by
    default so the synthetic timestamps don't pollute the
    window-over-window comparison.
    """

    predicted_at_utc: datetime
    model_id: str
    stage: str
    score: float
    row_keys: tuple[str, ...]
    feature_row: Mapping[str, Any] | None = None
    backfill_kind: str | None = None
    trade_id: str | None = None


def record_from_dict(raw: Mapping[str, object]) -> ShadowRecord:
    """Coerce a raw JSONL record into a typed :class:`ShadowRecord`.

    Raises ``ValueError`` on a malformed entry (missing field, wrong
    type, unparseable timestamp). Callers that don't want to crash
    on a single bad row should catch ``ValueError`` per-record.
    """
    try:
        ts_raw = raw["predicted_at_utc"]
        model_id = raw["model_id"]
        stage = raw["stage"]
        score = raw["score"]
    except KeyError as exc:
        raise ValueError(f"missing field: {exc.args[0]!r}") from exc
    if not isinstance(ts_raw, str):
        raise ValueError(f"predicted_at_utc must be ISO-8601 str; got {type(ts_raw).__name__}")
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError as exc:
        raise ValueError(f"unparseable predicted_at_utc {ts_raw!r}: {exc}") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if not isinstance(model_id, str):
        raise ValueError(f"model_id must be str; got {type(model_id).__name__}")
    if not isinstance(stage, str):
        raise ValueError(f"stage must be str; got {type(stage).__name__}")
    try:
        score_f = float(score)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"score must be a finite float; got {score!r}") from exc
    if not math.isfinite(score_f):
        raise ValueError(f"score must be finite; got {score_f}")
    feature_row_raw = raw.get("feature_row")
    if feature_row_raw is None:
        feature_row: Mapping[str, Any] | None = None
    elif isinstance(feature_row_raw, dict):
        feature_row = {str(k): v for k, v in feature_row_raw.items()}
    else:
        # A non-dict feature_row is a malformed write; drop it rather
        # than crash the whole record (the score is the load-bearing
        # field, not the context dict).
        feature_row = None
    # `row_keys` is the sorted input-feature-name list. The regime heads
    # write it explicitly; the exit-head/peak-head records (event_source
    # "exit_head") carry `feature_row` but no `row_keys`, so derive it the
    # same way the writer/backfill does (`sorted(feature_row.keys())`,
    # backfill.py) — MB-20260716-PROMOREADY-EXITHEAD-SCHEMA. Without this the
    # loader skipped every exit-head record (`missing field: 'row_keys'`),
    # leaving the exit-head family out of the promotion-readiness report.
    row_keys = raw.get("row_keys")
    if row_keys is None and isinstance(feature_row, dict):
        row_keys = sorted(feature_row.keys())
    if not isinstance(row_keys, list) or not all(
        isinstance(k, str) for k in row_keys
    ):
        raise ValueError("row_keys must be a list of str (or derivable from feature_row)")
    backfill_kind_raw = raw.get("backfill_kind")
    backfill_kind = (
        str(backfill_kind_raw)
        if isinstance(backfill_kind_raw, str) and backfill_kind_raw
        else None
    )
    trade_id_raw = raw.get("trade_id")
    trade_id = (
        str(trade_id_raw)
        if trade_id_raw is not None and trade_id_raw != ""
        else None
    )
    return ShadowRecord(
        predicted_at_utc=ts,
        model_id=model_id,
        stage=stage,
        score=score_f,
        row_keys=tuple(row_keys),
        feature_row=feature_row,
        backfill_kind=backfill_kind,
        trade_id=trade_id,
    )


def iter_records(
    log_path: Path | str,
    *,
    logger: logging.Logger | None = None,
) -> Iterator[ShadowRecord]:
    """Stream :class:`ShadowRecord` from a JSONL log file.

    Per-line failures (bad JSON, missing field, bad timestamp) are
    logged at WARNING and skipped. Returning an empty iterator when
    the file does not exist is intentional — calling code shouldn't
    branch on `Path.exists()`.
    """
    log = logger if logger is not None else _LOGGER
    path = Path(log_path)
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning(
                    "shadow_log_skip lineno=%d err=json: %s", lineno, exc,
                )
                continue
            if not isinstance(obj, dict):
                log.warning(
                    "shadow_log_skip lineno=%d err=not-an-object", lineno,
                )
                continue
            try:
                yield record_from_dict(obj)
            except ValueError as exc:
                log.warning(
                    "shadow_log_skip lineno=%d err=%s", lineno, exc,
                )


def filter_records(
    records: Iterable[ShadowRecord],
    *,
    model_id: str | None = None,
    stage: str | None = None,
    since: datetime | None = None,
) -> Iterator[ShadowRecord]:
    """Apply optional filters. Each filter is independent and
    additive — passing more than one narrows the result.

    `since` is inclusive of the boundary timestamp.
    """
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    for r in records:
        if model_id is not None and r.model_id != model_id:
            continue
        if stage is not None and r.stage != stage:
            continue
        if since is not None and r.predicted_at_utc < since:
            continue
        yield r


@dataclass
class ModelStats:
    """Per-(model_id, stage) aggregate over a record stream."""

    model_id: str
    stage: str
    count: int = 0
    score_sum: float = 0.0
    score_min: float = math.inf
    score_max: float = -math.inf
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    row_keys_seen: set[str] = field(default_factory=set)

    def observe(self, r: ShadowRecord) -> None:
        self.count += 1
        self.score_sum += r.score
        self.score_min = min(self.score_min, r.score)
        self.score_max = max(self.score_max, r.score)
        if self.first_seen is None or r.predicted_at_utc < self.first_seen:
            self.first_seen = r.predicted_at_utc
        if self.last_seen is None or r.predicted_at_utc > self.last_seen:
            self.last_seen = r.predicted_at_utc
        self.row_keys_seen.update(r.row_keys)

    @property
    def score_mean(self) -> float:
        return self.score_sum / self.count if self.count else 0.0


def aggregate(
    records: Iterable[ShadowRecord],
) -> list[ModelStats]:
    """Group by ``(model_id, stage)`` and return a stable list
    ordered by total observation count (descending) then model_id
    (ascending) so the table output is deterministic.
    """
    by_key: MutableMapping[tuple[str, str], ModelStats] = {}
    for r in records:
        key = (r.model_id, r.stage)
        if key not in by_key:
            by_key[key] = ModelStats(model_id=r.model_id, stage=r.stage)
        by_key[key].observe(r)
    return sorted(
        by_key.values(),
        key=lambda s: (-s.count, s.model_id),
    )


def format_inspect_table(
    records: Iterable[ShadowRecord],
    *,
    limit: int | None = None,
) -> str:
    """Render the most-recent N records as a fixed-width table.

    Newest first. Returns the empty string when no records match
    (so the CLI can branch on a falsy return).
    """
    rows = list(records)
    rows.sort(key=lambda r: r.predicted_at_utc, reverse=True)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        return ""
    headers = ("predicted_at_utc", "model_id", "stage", "score", "row_keys")
    body = []
    for r in rows:
        body.append(
            (
                r.predicted_at_utc.isoformat(timespec="seconds"),
                r.model_id,
                r.stage,
                f"{r.score:.6f}",
                ",".join(r.row_keys),
            )
        )
    widths = [
        max(len(h), *(len(row[i]) for row in body)) for i, h in enumerate(headers)
    ]
    lines = []
    lines.append("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    lines.append("  ".join("-" * w for w in widths))
    for row in body:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))
    return "\n".join(lines)


def format_stats_table(stats: Iterable[ModelStats]) -> str:
    """Render aggregated per-model stats as a fixed-width table."""
    rows = list(stats)
    if not rows:
        return ""
    headers = (
        "model_id", "stage", "count", "mean", "min", "max",
        "first_seen", "last_seen",
    )
    body = []
    for s in rows:
        body.append(
            (
                s.model_id,
                s.stage,
                str(s.count),
                f"{s.score_mean:.6f}",
                f"{s.score_min:.6f}",
                f"{s.score_max:.6f}",
                s.first_seen.isoformat(timespec="seconds") if s.first_seen else "-",
                s.last_seen.isoformat(timespec="seconds") if s.last_seen else "-",
            )
        )
    widths = [
        max(len(h), *(len(row[i]) for row in body)) for i, h in enumerate(headers)
    ]
    lines = []
    lines.append("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    lines.append("  ".join("-" * w for w in widths))
    for row in body:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))
    return "\n".join(lines)
