"""S-014 — Dashboard data feed endpoints.

Exposes four read-only endpoints consumed by the Vercel React dashboard.
No authentication is required for GET requests — all data is operational
telemetry with no secrets. Restrict network-level access via firewall.

Contract note (S-061, ict-trading-bot#556): every optional field is
serialized as JSON ``null`` when the source value is missing. The
dashboard distinguishes "really 0" from "not measured" on this — fall-
through to a fabricated ``0`` or ``"unknown"`` here is a contract bug.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["dashboard"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(os.environ.get("TRADE_JOURNAL_DB", str(_REPO_ROOT / "trade_journal.db")))
_AUDIT_LOG = _REPO_ROOT / "runtime_logs" / "signal_audit.jsonl"
_HEARTBEAT = _REPO_ROOT / "runtime_logs" / "heartbeat.txt"
_BOT_LOG = _REPO_ROOT / "bot.log"
_LOG_TAIL = 100
_SIGNAL_TAIL = 50

# trades.direction values seen in the wild and their dashboard-side
# wire equivalents. The dashboard's Position type expects
# "buy"/"sell"; the DB column historically stores "long"/"short".
_SIDE_MAP = {"buy": "buy", "sell": "sell", "long": "buy", "short": "sell"}


def _normalise_side(direction: Any) -> str:
    if not isinstance(direction, str):
        return str(direction or "")
    return _SIDE_MAP.get(direction.strip().lower(), direction.strip().lower())


def _bot_status() -> str:
    from src.runtime.heartbeat import heartbeat_label  # local import keeps router cheap
    if not _HEARTBEAT.exists():
        return "stopped"
    age = time.time() - _HEARTBEAT.stat().st_mtime
    # Thresholds derived from TICK_INTERVAL_SECONDS — see
    # src/runtime/heartbeat.py for the running/paused/stopped convention
    # (matches scripts/check_heartbeat.py grace factor of 2.0).
    return heartbeat_label(age)


# S-067 follow-up #9: vm_health implementation moved to
# src/web/api/_vm_health.py to remove the diag.py / dashboard.py
# fork. Re-exported under the legacy ``_vm_health`` name so
# tests (e.g. tests/test_dashboard_data_contract.py monkeypatching
# ``dashboard_router._vm_health``) keep working without modification.
from src.web.api._vm_health import vm_health as _vm_health  # noqa: E402


def _pnl_stats() -> tuple[float, float, int, float]:
    """Returns (pnl24h, totalPnL, openTrades, winRate).

    Raises ``sqlite3.Error`` on a structural DB failure (missing
    table / column, locked DB, corrupt file). The early-return-zeroes
    branch fires only when the DB file genuinely does not exist —
    that's a legitimate "no trades yet" case on a fresh install,
    distinct from "DB present but unreadable". ``get_stats`` catches
    the raised error and surfaces it as a 503 so the dashboard renders
    a real outage badge instead of fabricated zero metrics.
    """
    if not _DB_PATH.exists():
        return 0.0, 0.0, 0, 0.0
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        cur = conn.cursor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN substr(COALESCE(created_at,timestamp),1,10)=?
                                      AND status!='open' THEN pnl ELSE 0 END),0),
                    COALESCE(SUM(CASE WHEN status!='open' THEN pnl ELSE 0 END),0),
                    COUNT(CASE WHEN status='open' THEN 1 END),
                    COUNT(CASE WHEN status!='open' THEN 1 END),
                    COUNT(CASE WHEN status!='open' AND pnl>0 THEN 1 END)
                FROM trades
                WHERE COALESCE(is_backtest,0)=0
                """,
                (today,),
            )
            row = cur.fetchone()
        except sqlite3.Error:
            # S-067: structural failures (missing column, locked DB,
            # corrupt file) used to be silently swallowed under a
            # blanket ``except Exception`` and surfaced to the
            # dashboard as fabricated `(0, 0, 0, 0)`. Log loudly and
            # re-raise so ``get_stats`` can convert to 503.
            logger.exception("dashboard: _pnl_stats sqlite read failed")
            raise
        pnl24h, total_pnl, open_trades, closed, winners = row
        win_rate = (winners / closed * 100.0) if closed else 0.0
        return float(pnl24h), float(total_pnl), int(open_trades), round(win_rate, 1)
    finally:
        conn.close()


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    lines: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        # S-067 borderline: was silently `return []`. Keep the
        # empty-list shape (the dashboard's logs/signals panels
        # branch on length and want to render a "no entries" stub
        # rather than blow up) but log so the next debugging
        # session sees the underlying read failure.
        logger.warning(
            "dashboard: tail_jsonl(%s) read failed: %s: %s",
            path, type(exc).__name__, exc,
        )
        return []
    return [json.loads(raw) for raw in lines[-n:] if raw.strip()]


def _tail_plain_log(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()
    except OSError as exc:
        # S-067 borderline: same shape as _tail_jsonl above.
        logger.warning(
            "dashboard: tail_plain_log(%s) read failed: %s: %s",
            path, type(exc).__name__, exc,
        )
        return []
    entries = []
    for line in raw_lines[-n:]:
        line = line.rstrip()
        if not line:
            continue
        level: str = "info"
        llow = line.lower()
        if "error" in llow:
            level = "error"
        elif "warn" in llow:
            level = "warn"
        elif "trade" in llow or "order" in llow or "filled" in llow:
            level = "trade"
        entries.append(
            {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "message": line,
            }
        )
    return entries


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    try:
        pnl24h, total_pnl, open_trades, win_rate = _pnl_stats()
    except sqlite3.Error as exc:
        # S-067: the DB is reachable-but-broken. Surface a real outage
        # rather than a fabricated `pnl24h: 0` that an operator would
        # read as "no trades today".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "stats_unavailable",
                "reason": f"db error: {type(exc).__name__}",
            },
        )
    return {
        "pnl24h": round(pnl24h, 2),
        "totalPnL": round(total_pnl, 2),
        "openTrades": open_trades,
        "winRate": win_rate,
        "status": _bot_status(),
        "datasource": "live",
        "vmHealth": _vm_health(),
    }


@router.get("/logs")
async def get_logs() -> list[dict[str, Any]]:
    entries = _tail_jsonl(_AUDIT_LOG, _LOG_TAIL)
    if entries:
        out = []
        for e in entries:
            level = str(e.get("level", e.get("result", "info"))).lower()
            if level not in ("info", "warn", "error", "trade"):
                level = "info"
            out.append(
                {
                    "id": e.get("id", str(uuid.uuid4())),
                    "timestamp": e.get(
                        "ts",
                        e.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    ),
                    "level": level,
                    "message": e.get("message", e.get("msg", json.dumps(e))),
                }
            )
        return out
    return _tail_plain_log(_BOT_LOG, _LOG_TAIL)


@router.get("/positions")
async def get_positions() -> list[dict[str, Any]]:
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, account_id, symbol, direction, position_size,
                       entry_price, COALESCE(pnl, 0), created_at,
                       stop_loss, take_profit_1, strategy_name
                FROM trades
                WHERE status = 'open'
                  AND COALESCE(is_backtest, 0) = 0
                ORDER BY created_at DESC
                LIMIT 50
                """
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("dashboard: /positions sqlite read failed")
        return []
    return [
        {
            "id": str(r[0]),
            "account": r[1],
            "symbol": r[2],
            "side": _normalise_side(r[3]),
            "qty": r[4],
            "entryPrice": r[5],
            "unrealizedPnl": round(float(r[6]), 2),
            "openedAt": r[7],
            "stopLoss": float(r[8]) if r[8] is not None else None,
            "takeProfit": float(r[9]) if r[9] is not None else None,
            "pattern": r[10] if r[10] else None,
        }
        for r in rows
    ]


@router.get("/signals")
async def get_signals() -> list[dict[str, Any]]:
    raw = _tail_jsonl(_AUDIT_LOG, _SIGNAL_TAIL)
    out = []
    for e in raw:
        side = str(e.get("side", e.get("direction", ""))).lower()
        if side not in ("buy", "sell", "long", "short"):
            continue
        # Pass through missing fields as None — the dashboard treats
        # null as "not provided by the writer" and renders accordingly,
        # versus 0/"unknown" which it would render as a real value.
        # Writer-side fix lives in src/runtime/pipeline.py log_signal().
        pattern = e.get("pattern")
        if pattern in (None, ""):
            pattern = e.get("signal_type")
        confidence = e.get("confidence")
        if confidence is None:
            confidence = e.get("score")
        # The pipeline writes the entry price under any of three field
        # names depending on the call site (src/runtime/pipeline.py:218,
        # :524, :1142). Cover all three so the dashboard never sees a
        # spurious None just because the writer chose a different alias.
        price = e.get("price")
        if price is None:
            price = e.get("entry_price")
        if price is None:
            price = e.get("entry")
        out.append(
            {
                "id": e.get("id", str(uuid.uuid4())),
                "timestamp": e.get("ts", e.get("timestamp", "")),
                "symbol": e.get("symbol", "BTCUSDT"),
                "side": side,
                "pattern": pattern,
                "confidence": confidence,
                "price": price,
            }
        )
    return out
