"""S-014 — Dashboard data feed endpoints.

Exposes four read-only endpoints consumed by the Vercel React dashboard.
No authentication is required for GET requests — all data is operational
telemetry with no secrets. Restrict network-level access via firewall.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/bot", tags=["dashboard"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(os.environ.get("TRADE_JOURNAL_DB", str(_REPO_ROOT / "trade_journal.db")))
_AUDIT_LOG = _REPO_ROOT / "runtime_logs" / "signal_audit.jsonl"
_HEARTBEAT = _REPO_ROOT / "runtime_logs" / "heartbeat.txt"
_BOT_LOG = _REPO_ROOT / "bot.log"
_LOG_TAIL = 100
_SIGNAL_TAIL = 50


def _bot_status() -> str:
    if not _HEARTBEAT.exists():
        return "stopped"
    age = time.time() - _HEARTBEAT.stat().st_mtime
    if age < 120:
        return "running"
    if age < 600:
        return "paused"
    return "stopped"


def _vm_health() -> dict[str, float]:
    try:
        import psutil
        return {
            "cpu": psutil.cpu_percent(interval=0.1),
            "memory": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage("/").percent,
        }
    except Exception:  # noqa: BLE001
        return {"cpu": 0.0, "memory": 0.0, "disk": 0.0}


def _pnl_stats() -> tuple[float, float, int, float]:
    """Returns (pnl24h, totalPnL, openTrades, winRate)."""
    if not _DB_PATH.exists():
        return 0.0, 0.0, 0, 0.0
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            cur = conn.cursor()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN substr(COALESCE(created_at,timestamp),1,10)=? AND status!='open' THEN pnl ELSE 0 END),0),
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
            pnl24h, total_pnl, open_trades, closed, winners = row
            win_rate = (winners / closed * 100.0) if closed else 0.0
            return float(pnl24h), float(total_pnl), int(open_trades), round(win_rate, 1)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0.0, 0.0, 0, 0.0


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    lines: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    return [json.loads(l) for l in lines[-n:] if l.strip()]


def _tail_plain_log(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()
    except OSError:
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
    pnl24h, total_pnl, open_trades, win_rate = _pnl_stats()
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
                    "timestamp": e.get("ts", e.get("timestamp", datetime.now(timezone.utc).isoformat())),
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
                SELECT id, account_id, symbol, side, qty, entry_price,
                       COALESCE(pnl, 0), created_at
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
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": str(r[0]),
            "account": r[1],
            "symbol": r[2],
            "side": r[3],
            "qty": r[4],
            "entryPrice": r[5],
            "unrealizedPnl": round(float(r[6]), 2),
            "openedAt": r[7],
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
        out.append(
            {
                "id": e.get("id", str(uuid.uuid4())),
                "timestamp": e.get("ts", e.get("timestamp", "")),
                "symbol": e.get("symbol", "BTCUSDT"),
                "side": side,
                "pattern": e.get("pattern", e.get("signal_type", "unknown")),
                "confidence": e.get("confidence", e.get("score", 0)),
                "price": e.get("price", e.get("entry_price", 0)),
            }
        )
    return out
