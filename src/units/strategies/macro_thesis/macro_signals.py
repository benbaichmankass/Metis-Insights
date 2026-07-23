"""M28 — macro_signals: the traceable-evidence store (schema §3).

Every input a thesis rests on — a macro reading, a news/filing extraction, a TA
read — is a **signal** row: a structured, source-linked claim. A thesis's
``signals[]`` are ``signal_id`` refs into this store, so every trade is
reconstructable and every input replayable at backtest time.

Two halves, both pure/append-only (mirroring ``thesis_store`` / ``event_store``):

- :func:`make_signal` — the honest-null row constructor (the
  ``screenshot_parse.py`` spirit: omit/``None`` any field the source doesn't
  support; never fabricate a number). Validates ``direction`` ∈
  ``{bullish,bearish,neutral}`` and ``source`` against the free-source stack,
  clamps ``magnitude``/``confidence`` to ``[0,1]`` (non-numeric → ``None``).
- the append-only JSONL store — ``write_signals`` + newest-first / per-entity /
  per-event / per-thesis reads.

Signals are immutable evidence (append-once — a correction is a NEW ``signal_id``,
not an overwrite), so there is no lifecycle-supersede here (unlike theses/events).
Best-effort, never raises, no order path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional, Sequence

from .thesis import FREE_SOURCES

logger = logging.getLogger(__name__)

SIGNALS_LOG_NAME = "macro_signals.jsonl"
DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})


def new_signal_id(token: str) -> str:
    """``sig-<token>`` id (schema §3). The caller supplies the token (ULID/uuid
    in the live path) so this stays deterministic + replayable."""
    return f"sig-{token}"


def _unit(x: Any) -> Optional[float]:
    """Coerce to a ``[0,1]``-clamped float; non-numeric → ``None`` (honest-null)."""
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def make_signal(
    signal_id: str,
    *,
    ts: str,
    observed_at: str,
    source: str,
    claim: str,
    entity: Optional[str] = None,
    direction: str = "neutral",
    magnitude: Any = None,
    confidence: Any = None,
    source_url: Optional[str] = None,
    extractor_id: Optional[str] = None,
    event_ref: Optional[str] = None,
    raw_ref: Optional[str] = None,
) -> dict:
    """Shape one schema-§3 ``macro_signals`` row, honest-null throughout.

    ``direction`` outside ``{bullish,bearish,neutral}`` falls back to
    ``neutral`` (the non-committal default — never a fabricated conviction);
    ``magnitude``/``confidence`` clamp to ``[0,1]`` or ``None``; an unknown
    ``source`` (outside the free-stack enum) is preserved verbatim but flagged
    ``source_known:false`` so a downstream consumer can audit it. Pure — never
    raises, never fabricates."""
    return {
        "signal_id": signal_id,
        "ts": ts,
        "observed_at": observed_at,
        "source": source,
        "source_known": source in FREE_SOURCES,
        "source_url": source_url,
        "extractor_id": extractor_id,
        "claim": claim,
        "entity": entity,
        "direction": direction if direction in DIRECTIONS else "neutral",
        "magnitude": _unit(magnitude),
        "confidence": _unit(confidence),
        "event_ref": event_ref,
        "raw_ref": raw_ref,
    }


def _log_path(path: Optional[Any]):
    if path is not None:
        from pathlib import Path
        return Path(path)
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / SIGNALS_LOG_NAME


def write_signals(rows: Iterable[dict], *, path: Optional[Any] = None) -> int:
    """Append signal rows (append-once evidence). Best-effort; skips a bad row."""
    p = _log_path(path)
    written = 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                try:
                    fh.write(json.dumps(row, default=str) + "\n")
                    written += 1
                except (TypeError, ValueError):
                    continue
    except OSError as exc:
        logger.warning("macro_signals: append failed (%s)", exc)
    return written


def read_signal_records(*, path: Optional[Any] = None, limit: Optional[int] = None) -> list[dict]:
    """All signal rows, newest-first (append order reversed)."""
    p = _log_path(path)
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    out.reverse()
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


def read_signals_for_entity(entity: str, *, path: Optional[Any] = None) -> list[dict]:
    """Signals concerning one ``entity`` (symbol/asset-class/company), newest-first."""
    return [r for r in read_signal_records(path=path) if r.get("entity") == entity]


def read_signals_by_event(event_id: str, *, path: Optional[Any] = None) -> list[dict]:
    """Signals whose ``event_ref`` names ``event_id``, newest-first."""
    return [r for r in read_signal_records(path=path) if r.get("event_ref") == event_id]


def read_signals_for_thesis(
    signal_ids: Sequence[str], *, path: Optional[Any] = None
) -> list[dict]:
    """Resolve a thesis's ``signals[]`` refs → the evidence rows (order preserved,
    missing ids silently dropped)."""
    wanted = set(signal_ids or [])
    by_id = {r.get("signal_id"): r for r in read_signal_records(path=path)}
    return [by_id[sid] for sid in signal_ids or [] if sid in wanted and sid in by_id]
