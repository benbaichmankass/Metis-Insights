"""System notifications banner (operator-requested 2026-07-08).

`GET /api/bot/notifications` — one Tier-1, read-only, connection-free surface
the dashboard and Android app poll to render a **banner at the top of the
Overview page** for the important, can't-miss conditions (not routine pings):

  * **trainer_down** (severity ``alert``) — the trainer VM's 2-min mirror
    heartbeat has gone stale (SSH-dead / OOM-hung). From
    ``trainer_reachability_alert.status()``.
  * **account_down** (severity ``alert``) — a declared-live broker account is
    reading unreachable (IB gateway logged out, exchange API 401-ing). One
    banner per latched-down account, from
    ``account_reachability_alert.down_accounts()``.
  * **trade_open** (severity ``info``) — a compact "recently opened trades"
    notice (best-effort, last ``TRADE_OPEN_BANNER_WINDOW_MIN`` minutes), so a
    fresh entry surfaces on the banner too. Never fails the endpoint.

Response::

    {
      "generated_at": "<iso>",
      "count": <int>,
      "has_alerts": <bool>,           # any severity=="alert" present
      "banners": [
        {"severity": "alert|warning|info", "kind": "...",
         "message": "<short>", "detail": "<longer|null>", "since": "<iso|null>"}
      ]
    }

Severity ordering in ``banners``: alert first, then warning, then info — so a
consumer can render the top-most as the prominent banner. Best-effort: any
source failure is swallowed and simply omits that kind's banner; the endpoint
never 5xxs. See ``docs/api-tier-policy.md`` Tier 1.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.utils.paths import trade_journal_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["notifications"])

_SEVERITY_RANK = {"alert": 0, "warning": 1, "info": 2}


def _trainer_banner() -> Optional[Dict[str, Any]]:
    try:
        from src.runtime.trainer_reachability_alert import status as trainer_status
        st = trainer_status()
        if not st.get("down"):
            return None
        age = st.get("age_seconds")
        if age is None:
            detail = "No trainer_status.json in the mirror — the trainer has not published (or the mirror is missing)."
        else:
            detail = f"Trainer mirror stale ~{int(age // 60)}m (the 2-min publish heartbeat has stopped)."
        return {
            "severity": "alert",
            "kind": "trainer_down",
            "message": "Trainer VM is DOWN — ML training has stalled.",
            "detail": detail
            + " Live shadow/advisory inference is unaffected; probable OCI-console reboot needed.",
            "since": st.get("since"),
        }
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: trainer banner failed: %s", exc)
        return None


def _account_down_banners() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from src.runtime.account_reachability_alert import down_accounts
        for aid, st in (down_accounts() or {}).items():
            out.append({
                "severity": "alert",
                "kind": "account_down",
                "message": f"Broker account DOWN: {aid}",
                "detail": "Reading unreachable — trades on this account may be unprotected or going dark.",
                "since": (st or {}).get("last_change"),
            })
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: account-down banners failed: %s", exc)
    return out


def _trade_open_window_min() -> int:
    try:
        n = int(os.environ.get("TRADE_OPEN_BANNER_WINDOW_MIN", "30"))
        return n if n > 0 else 30
    except (TypeError, ValueError):
        return 30


def _recent_trade_open_banner() -> Optional[Dict[str, Any]]:
    """Best-effort: a compact 'recently opened trades' info banner.

    Reads real-money + paper open, non-backtest rows opened within the window.
    Connection-free (read-only DB). Any failure → no banner (never raises).
    """
    try:
        window_min = _trade_open_window_min()
        db = trade_journal_db_path()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        try:
            con.row_factory = sqlite3.Row
            cols = {r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()}
            if "status" not in cols:
                return None
            open_ts_col = next(
                (c for c in ("timestamp", "created_at", "opened_at") if c in cols),
                None,
            )
            sym_col = "symbol" if "symbol" in cols else None
            strat_col = next(
                (c for c in ("setup_type", "strategy", "pattern") if c in cols), None
            )
            if not open_ts_col or not sym_col:
                return None
            bt = "AND COALESCE(is_backtest, 0) = 0" if "is_backtest" in cols else ""
            rows = con.execute(
                f"SELECT {sym_col} AS symbol, {open_ts_col} AS opened_at"
                f"{(', ' + strat_col + ' AS strategy') if strat_col else ''} "
                f"FROM trades WHERE status = 'open' {bt} "
                f"ORDER BY {open_ts_col} DESC LIMIT 25"
            ).fetchall()
        finally:
            con.close()

        now = datetime.now(timezone.utc)
        recent: List[sqlite3.Row] = []
        for r in rows:
            dt = _parse_ts(r["opened_at"])
            if dt is not None and (now - dt).total_seconds() <= window_min * 60:
                recent.append(r)
        if not recent:
            return None
        syms = []
        for r in recent:
            s = r["symbol"]
            if s and s not in syms:
                syms.append(s)
        n = len(recent)
        head = f"{n} trade{'s' if n != 1 else ''} opened in the last {window_min}m"
        return {
            "severity": "info",
            "kind": "trade_open",
            "message": f"{head}: {', '.join(syms[:6])}" + (" …" if len(syms) > 6 else ""),
            "detail": None,
            "since": None,
        }
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: recent-trade-open banner failed: %s", exc)
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO or epoch-ms/epoch-s trade timestamp to aware UTC. None on failure."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            secs = float(value)
            if secs > 1e11:  # epoch ms
                secs /= 1000.0
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        s = str(value).strip()
        if not s:
            return None
        if s.isdigit():
            secs = float(s)
            if secs > 1e11:
                secs /= 1000.0
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001  # allow-silent: best-effort ts parse — unparseable timestamp yields no banner, never raises
        return None


@router.get("/notifications")
def get_notifications() -> Dict[str, Any]:
    """Aggregate the active banner-worthy conditions (Tier 1, best-effort)."""
    banners: List[Dict[str, Any]] = []

    tb = _trainer_banner()
    if tb:
        banners.append(tb)
    banners.extend(_account_down_banners())
    ob = _recent_trade_open_banner()
    if ob:
        banners.append(ob)

    banners.sort(key=lambda b: _SEVERITY_RANK.get(b.get("severity"), 9))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(banners),
        "has_alerts": any(b.get("severity") == "alert" for b in banners),
        "banners": banners,
    }
