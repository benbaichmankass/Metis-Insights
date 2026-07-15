"""Observe-only soak log for the market-neutral pairs sleeve (M22 D2).

Mirrors the canonical soak trio (``allocator_soak.py`` / ``exit_ladder_soak.py``):
a pure builder, a best-effort JSONL writer under ``runtime_logs_dir()``, and a
pure reader envelope. One row per pair evaluated per tick, capturing the live
spread/z decision AND (when the executor acts) the placement/close outcome — so
the paper soak is fully observable on ``/api/bot/pairs/soak`` and the dashboard.

Event kinds (``event``):
  * ``skip_flat``      — flat, |z| below entry threshold (the common no-op).
  * ``skip_concurrency`` — entry signalled but a leg is already held by another
    open pair (disjoint-legs gate blocked it).
  * ``skip_size``      — entry signalled but sizing refused (sub-min qty / no funds).
  * ``skip_state_unreadable`` — both legs open but the durable spread bookkeeping
    couldn't be read (skip this tick; the per-leg backstop SL/TP protects).
  * ``shadow_open`` / ``shadow_close`` — the would-be open/close under
    ``execution: shadow`` (computed + logged, placed NOTHING).
  * ``open``           — both legs placed (atomic 2-leg entry).
  * ``open_failed``    — leg-imbalance: one leg failed; the filled leg was unwound.
  * ``hold``           — in a position, no exit this tick.
  * ``close``          — spread exit fired; both legs closed.

Never drives an order — pure observability. The executor writes these; nothing
reads them back to make a trading decision.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SOAK_LOG_NAME = "pairs_soak.jsonl"


def build_pairs_soak_record(*, event: str, pair: str, symbol_a: str, symbol_b: str,
                            account_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Pure builder — returns a JSON-able dict, or None on bad input. Never raises."""
    try:
        if not event or not pair:
            return None
        rec: Dict[str, Any] = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "event": str(event), "pair": str(pair),
            "symbol_a": str(symbol_a), "symbol_b": str(symbol_b),
            "account_id": str(account_id),
        }
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        return rec
    except Exception:  # noqa: BLE001
        return None


def soak_log_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / SOAK_LOG_NAME


def record_pairs_soak(record: Optional[Dict[str, Any]]) -> bool:
    """Best-effort append of one JSON line. Swallows all I/O errors."""
    if not record:
        return False
    try:
        path = soak_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except OSError:
        return False


def read_soak_records(*, limit: int = 100, pair: Optional[str] = None,
                      event: Optional[str] = None) -> Dict[str, Any]:
    """Newest-first envelope {present, log_path, count, records, summary}."""
    path = soak_log_path()
    if not path.exists():
        return {"present": False, "log_path": str(path), "count": 0,
                "records": [], "summary": {"total_scanned": 0, "by_event": {}}}
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"present": True, "log_path": str(path), "count": 0, "records": [],
                "error": str(exc), "summary": {"total_scanned": 0, "by_event": {}}}
    recs: List[Dict[str, Any]] = []
    by_event: Dict[str, int] = {}
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        by_event[r.get("event", "?")] = by_event.get(r.get("event", "?"), 0) + 1
        recs.append(r)
    total = len(recs)
    recs.reverse()  # newest-first
    if pair:
        recs = [r for r in recs if r.get("pair") == pair]
    if event:
        recs = [r for r in recs if r.get("event") == event]
    recs = recs[: max(1, min(int(limit), 1000))]
    return {"present": True, "log_path": str(path), "count": len(recs),
            "records": recs, "summary": {"total_scanned": total, "by_event": by_event}}
