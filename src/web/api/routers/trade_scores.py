"""Tier-1 read endpoint joining shadow-prediction scores to trades.

Backs the dashboard's Journals "model scores" column. For each
trade in the requested window, returns the list of shadow-prediction
records whose ``predicted_at_utc`` falls between the trade's
``openedAt`` and ``closedAt`` (or "now" for open trades), grouped by
``model_id``.

Best-effort: an empty ``records`` list per trade means no shadow
predictions were logged during the trade's open window (or the
audit log is missing). The dashboard renders an em-dash in that case.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from ml.shadow.inspector import iter_records

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/trades", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(os.environ.get("TRADE_JOURNAL_DB", str(_REPO_ROOT / "trade_journal.db")))
# Aligned with the WS7 shadow-predictions writer (which respects
# runtime_logs_dir()).
_SHADOW_LOG = runtime_logs_dir() / "shadow_predictions.jsonl"


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _decode_notes_closed_at(notes: Any) -> str | None:
    if not isinstance(notes, str) or not notes:
        return None
    try:
        decoded = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    val = decoded.get("closed_at")
    return str(val) if val is not None else None


def _load_trade_windows(limit: int, include_open: bool) -> list[dict[str, Any]]:
    if not _DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        status_filter = "" if include_open else "AND t.status = 'closed'"
        sql = f"""
            SELECT t.id, t.symbol, t.status, t.timestamp AS opened_at,
                   t.notes, op.updated_at AS op_updated_at
            FROM trades t
            LEFT JOIN order_packages op ON op.linked_trade_id = t.id
            WHERE COALESCE(t.is_backtest, 0) = 0
              {status_filter}
            ORDER BY datetime(COALESCE(op.updated_at, t.timestamp)) DESC
            LIMIT ?
        """
        cur = conn.execute(sql, (limit,))
        rows = cur.fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        opened_at = _parse_iso(r["opened_at"])
        if opened_at is None:
            continue
        closed_iso = r["op_updated_at"] or _decode_notes_closed_at(r["notes"])
        closed_at = _parse_iso(closed_iso)
        if closed_at is None and r["status"] == "closed":
            closed_at = opened_at  # degenerate; window collapses
        out.append({
            "id": str(r["id"]),
            "symbol": r["symbol"],
            "status": r["status"],
            "opened_at": opened_at,
            "closed_at": closed_at,
        })
    return out


def _shadow_records_safe() -> list:
    if not _SHADOW_LOG.exists():
        return []
    try:
        return list(iter_records(_SHADOW_LOG))
    except (OSError, ValueError):
        # OSError = file read failure (permissions, missing midway through tail).
        # ValueError = inspector's malformed-record signal. Both are
        # legitimate "no data this tick"; the endpoint stays best-effort and
        # the dashboard shows an em-dash per trade.
        logger.exception("trade_scores: failed to read shadow predictions log")
        return []


@router.get("/scores")
def get_trade_scores(
    limit: int = Query(default=50, ge=1, le=200),
    include_open: bool = Query(default=True),
) -> dict[str, Any]:
    trades = _load_trade_windows(limit, include_open)
    shadow = _shadow_records_safe()
    out_trades: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for t in trades:
        window_end = t["closed_at"] or now
        window_start = t["opened_at"]
        trade_symbol = t.get("symbol")
        # Group records by model_id and keep first/last/min/max/mean/count.
        per_model: dict[str, dict[str, Any]] = {}
        for r in shadow:
            if r.predicted_at_utc < window_start or r.predicted_at_utc > window_end:
                continue
            # 2026-05-19: when the shadow record carries the
            # signal-time `feature_row.symbol`, require it to match the
            # trade's symbol. This stops a vwap BTCUSDT prediction from
            # being credited to an ETHUSDT turtle_soup trade just
            # because their open windows happened to overlap. Older
            # records (without `feature_row`) fall back to the
            # window-only join — no regression.
            if r.feature_row is not None and trade_symbol is not None:
                record_symbol = r.feature_row.get("symbol")
                if record_symbol and record_symbol != trade_symbol:
                    continue
            slot = per_model.setdefault(
                r.model_id,
                {
                    "model_id": r.model_id,
                    "stage": r.stage,
                    "count": 0,
                    "score_first": None,
                    "score_last": None,
                    "score_min": None,
                    "score_max": None,
                    "score_sum": 0.0,
                    "first_ts": None,
                    "last_ts": None,
                },
            )
            slot["count"] += 1
            slot["score_sum"] += r.score
            slot["score_min"] = r.score if slot["score_min"] is None else min(slot["score_min"], r.score)
            slot["score_max"] = r.score if slot["score_max"] is None else max(slot["score_max"], r.score)
            if slot["first_ts"] is None or r.predicted_at_utc < slot["first_ts"]:
                slot["first_ts"] = r.predicted_at_utc
                slot["score_first"] = r.score
            if slot["last_ts"] is None or r.predicted_at_utc > slot["last_ts"]:
                slot["last_ts"] = r.predicted_at_utc
                slot["score_last"] = r.score
        scores_out = []
        for slot in per_model.values():
            scores_out.append({
                "model_id": slot["model_id"],
                "stage": slot["stage"],
                "count": slot["count"],
                "score_first": slot["score_first"],
                "score_last": slot["score_last"],
                "score_min": slot["score_min"],
                "score_max": slot["score_max"],
                "score_mean": slot["score_sum"] / slot["count"] if slot["count"] else None,
                "first_ts": slot["first_ts"].isoformat() if slot["first_ts"] else None,
                "last_ts": slot["last_ts"].isoformat() if slot["last_ts"] else None,
            })
        scores_out.sort(key=lambda s: s["model_id"])
        out_trades.append({
            "trade_id": t["id"],
            "symbol": t["symbol"],
            "status": t["status"],
            "opened_at": t["opened_at"].isoformat(),
            "closed_at": t["closed_at"].isoformat() if t["closed_at"] else None,
            "scores": scores_out,
        })
    return {
        "log_present": _SHADOW_LOG.is_file(),
        "log_path": str(_SHADOW_LOG),
        "shadow_record_count": len(shadow),
        "trades": out_trades,
    }
