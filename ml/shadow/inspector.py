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

# Stage-name canonicalisation, so a legacy registry row (`research_only` /
# `limited_live`) still matches a canonical stage query. Light import — the
# module pulls only stdlib + yaml.
from ml.manifest import canonical_stage

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


# --- Soak-window censoring ------------------------------------------------
#
# `first_seen` is the oldest SURVIVING row for a model, and the log is rotated
# (`ict-shadow-log-rotate.timer`). So for any model already active when the last
# rotation ran, `first_seen` is the ROTATION BOUNDARY, not the model's soak
# start — measured 2026-08-10, all 19 live models reported a `first_seen` inside
# the same two-minute band despite promotions spanning weeks
# (BL-20260810-SHADOW-STATS-FIRSTSEEN-IS-LOG-ROTATION-NOT-SOAK-START).
#
# That matters because `first_seen` is the DENOMINATOR of the shadow->advisory
# promotion gate ("days in shadow"), and the error runs in the dangerous
# direction: a long soak looks short, so a promotion that is DUE reads as
# not-yet-ready and is deferred. It is unprovenanced-diagnostic sub-class B —
# an implicit input (the retained window) substituted for the declared one (the
# model's first sighting), with nothing in the envelope disclosing it.
#
# The fix does NOT lengthen retention (that only moves the boundary). It makes
# the record state whether its own start was OBSERVED or CENSORED, which is
# answerable from the log alone: a model whose first row sits at the log's
# oldest edge was almost certainly already running before it; a model whose
# first row is many cadences later genuinely started inside the window.

#: A model's first row is treated as censored when it lands within this many of
#: its own estimated cadences of the log's oldest retained row. >1 absorbs
#: jitter (a 5m head does not write exactly on the boundary); well under the
#: many-cadences gap a genuinely-later start produces.
_CENSOR_CADENCE_TOLERANCE = 1.5

SOAK_START_OBSERVED = "observed"
SOAK_START_LOG_CENSORED = "log_censored"
SOAK_START_UNKNOWN = "unknown"


@dataclass(frozen=True)
class LogCoverage:
    """What window the retained log actually covers — the denominator every
    `first_seen` must be read against."""

    oldest: datetime | None
    newest: datetime | None
    total_records: int

    @property
    def present(self) -> bool:
        return self.total_records > 0


def coverage(records: Iterable[ShadowRecord]) -> LogCoverage:
    """The retained window across ALL records, unfiltered.

    Deliberately computed over the whole log, not a model-filtered slice: the
    question "was this model's start truncated by rotation?" is only answerable
    against the log's own edge, and a filtered slice would make every model
    look like it started at its own first row (which is the bug).
    """
    oldest: datetime | None = None
    newest: datetime | None = None
    total = 0
    for r in records:
        total += 1
        if oldest is None or r.predicted_at_utc < oldest:
            oldest = r.predicted_at_utc
        if newest is None or r.predicted_at_utc > newest:
            newest = r.predicted_at_utc
    return LogCoverage(oldest=oldest, newest=newest, total_records=total)


def mean_cadence_seconds(stats: ModelStats) -> float | None:
    """Rough per-model write cadence, from its own span and count.

    A mean, not a median — O(1) and adequate here, because a shadow head writes
    on a fixed bar cadence. ``None`` when a single observation makes a gap
    undefined (one row cannot establish a spacing).
    """
    if stats.count < 2 or stats.first_seen is None or stats.last_seen is None:
        return None
    span = (stats.last_seen - stats.first_seen).total_seconds()
    if span <= 0:
        return None
    return span / (stats.count - 1)


def soak_start_basis(stats: ModelStats, cov: LogCoverage) -> str:
    """Is this model's ``first_seen`` its real soak start, or the log's edge?

    Three states, never collapsed:
      * ``observed``     — first row is well inside the retained window, so the
                           log captured this model's actual first sighting.
      * ``log_censored`` — first row sits at the log's oldest edge; rotation
                           truncated it, so ``first_seen`` is a LOWER BOUND on
                           the soak, not its start. Not the same as a short soak.
      * ``unknown``      — nothing to measure (no rows, or a single row whose
                           cadence is undefined so the test cannot be applied).
    """
    if stats.count == 0 or stats.first_seen is None or cov.oldest is None:
        return SOAK_START_UNKNOWN
    cadence = mean_cadence_seconds(stats)
    if cadence is None:
        # One row. We cannot tell an edge-hugging survivor from a fresh start,
        # and guessing either way would be the collapse this function exists to
        # prevent.
        return SOAK_START_UNKNOWN
    lead_in = (stats.first_seen - cov.oldest).total_seconds()
    if lead_in <= _CENSOR_CADENCE_TOLERANCE * cadence:
        return SOAK_START_LOG_CENSORED
    return SOAK_START_OBSERVED


# --- Registry-sourced soak start (the recovery half) ----------------------
#
# `soak_start_basis` above DISCLOSES that a `first_seen` may be a rotation
# boundary. Disclosure alone still leaves the promotion gate without a
# denominator: knowing a number is a lower bound does not tell you the real
# one. The durable, rotation-independent record of when a model entered a
# stage is the model REGISTRY's `stage_history` — it is written once at the
# transition and never touched by log rotation.
#
# This module does NOT resolve a registry path. The trainer VM reads
# `ml/registry-store/registry.jsonl`; the live VM reads the published mirror at
# `runtime_logs/trainer_mirror/registry.jsonl` (see
# `src/web/api/routers/training_center.py::get_registry`). Baking either path
# in here would be wrong on the other host, so callers pass rows in and this
# module owns only the SEMANTICS.

SOAK_START_REGISTRY = "registry"


def stage_entry_times(
    rows: Iterable[Mapping[str, Any]],
    *,
    stage: str,
) -> dict[str, datetime]:
    """``model_id`` -> when it last ENTERED *stage*, from registry rows.

    Reads `stage_history[].{to_stage, at}`. Takes the LATEST matching event,
    matching `ml/promotion/gates.py::_stage_entered_at` — a model demoted and
    re-promoted is soaking from the re-promotion, not from the first one.

    Deliberately does NOT fall back to `created_at` when a row has no matching
    transition. `gates.py` does, and that fallback is itself a collapsed state:
    "the registry records when this entered shadow" and "we substituted the
    model's creation date" are different claims, and the second silently
    inflates the soak of a model that was created early and promoted late.
    A row with no matching event is simply absent from the returned map, so
    the caller falls through to the log-based basis and SAYS so.

    Stage strings are canonicalised on both sides (the ladder collapsed 7->3
    in 2026-06; legacy rows still carry `research_only` / `limited_live`), so a
    legacy `to_stage` still matches a canonical query. An unrecognised stage on
    a row is skipped rather than raising — one malformed registry row must not
    blind the whole surface.
    """
    try:
        want = canonical_stage(stage)
    except ValueError:
        return {}

    out: dict[str, datetime] = {}
    for row in rows:
        model_id = row.get("model_id")
        if not model_id:
            continue
        for ev in row.get("stage_history") or ():
            if not isinstance(ev, Mapping):
                continue
            raw_to = ev.get("to_stage")
            if not raw_to:
                continue
            try:
                if canonical_stage(str(raw_to)) != want:
                    continue
            except ValueError:
                continue
            at = _parse_dt(ev.get("at"))
            if at is None:
                continue
            prev = out.get(str(model_id))
            if prev is None or at > prev:
                out[str(model_id)] = at
    return out


def _parse_dt(raw: Any) -> datetime | None:
    """Parse a registry timestamp to an aware UTC datetime, else None."""
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@dataclass(frozen=True)
class SoakStart:
    """When a model's soak actually began, and how confidently we know it.

    ``basis`` is the load-bearing field and is never collapsed:

      * ``registry``     — a durable stage-transition record. ``started_at`` is
                           the real soak start and ``days`` is a MEASURED
                           duration.
      * ``observed``     — no registry transition available, but the model's
                           first log row sits well inside the retained window,
                           so the log did capture its first sighting.
      * ``log_censored`` — no registry transition, and the first row hugs the
                           log's oldest edge. ``days`` is a LOWER BOUND, not
                           the soak. A gate reading this as the soak length
                           under-counts, which is the direction that defers a
                           promotion that is already due.
      * ``unknown``      — we could not look. Distinct from a short soak.
    """

    model_id: str
    stage: str
    started_at: datetime | None
    basis: str
    days: float | None

    @property
    def is_measured(self) -> bool:
        """True only for a registry-sourced start. `observed` is good evidence
        but still bounded by retention; only the registry is rotation-proof."""
        return self.basis == SOAK_START_REGISTRY

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "stage": self.stage,
            "soak_started_at": (
                self.started_at.isoformat(timespec="seconds")
                if self.started_at else None
            ),
            "soak_start_basis": self.basis,
            "soak_days": round(self.days, 2) if self.days is not None else None,
            "soak_days_is_lower_bound": self.basis == SOAK_START_LOG_CENSORED,
        }


def resolve_soak_start(
    stats: ModelStats,
    cov: LogCoverage,
    *,
    registry_entered_at: Mapping[str, datetime] | None = None,
    now: datetime | None = None,
) -> SoakStart:
    """Best available soak start for *stats*, preferring the durable record.

    Precedence is registry -> log. The registry wins whenever it has a
    transition for this model, because it is the only source rotation cannot
    truncate; the log-derived answer is the fallback and carries its own
    censoring verdict so a lower bound is never reported as a measurement.
    """
    now = now or datetime.now(timezone.utc)
    entered = (registry_entered_at or {}).get(stats.model_id)
    if entered is not None:
        return SoakStart(
            model_id=stats.model_id, stage=stats.stage, started_at=entered,
            basis=SOAK_START_REGISTRY,
            days=(now - entered).total_seconds() / 86400.0,
        )

    basis = soak_start_basis(stats, cov)
    started = stats.first_seen
    days = ((now - started).total_seconds() / 86400.0) if started else None
    if basis == SOAK_START_UNKNOWN:
        # Keep `started_at` for context but refuse to publish a duration: a
        # single-row model has no measurable soak and printing "0.0 days"
        # would read as a fact.
        return SoakStart(stats.model_id, stats.stage, started,
                         SOAK_START_UNKNOWN, None)
    return SoakStart(stats.model_id, stats.stage, started, basis, days)


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
