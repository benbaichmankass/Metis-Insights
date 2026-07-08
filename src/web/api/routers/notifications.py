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

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.utils.paths import runtime_logs_dir, trade_journal_db_path

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


def _warning_window_min() -> int:
    try:
        n = int(os.environ.get("NOTIF_WARNING_WINDOW_MIN", "60"))
        return n if n > 0 else 60
    except (TypeError, ValueError):
        return 60


_WARN_MAX_BANNERS = 3
_WARN_LEVELS = {"WARNING", "ERROR", "CRITICAL"}


def _tail_lines(path: Any, max_bytes: int = 65536) -> List[str]:
    """Return the trailing lines of a text file (last ``max_bytes``)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            data = fh.read()
        return data.decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _recent_warning_banners() -> List[Dict[str, Any]]:
    """Recent operator WARN+ outcomes as banners.

    The "certainly anything that's a warning" half of the operator's banner ask
    (2026-07-08): surface the last hour's persisted ``outcomes.jsonl`` rows at
    level WARNING/ERROR/CRITICAL (the same feed that Telegrams ERROR/CRITICAL) —
    so a live operational condition like a stuck position-close ("Position CLOSE
    failing — won't flatten") shows on the app banner, not only in Telegram.
    CRITICAL/ERROR → ``alert``, WARNING → ``warning``. Deduped (a per-tick
    repeat like a close-retry collapses to one banner), newest-first, capped.
    Best-effort — a missing/garbled log yields no banners, never raises.
    """
    out: List[Dict[str, Any]] = []
    try:
        path = runtime_logs_dir() / "outcomes.jsonl"
        if not path.is_file():
            return out
        cutoff = datetime.now(timezone.utc).timestamp() - _warning_window_min() * 60
        seen: set = set()
        rows: List[tuple] = []
        for line in _tail_lines(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (ValueError, TypeError):
                continue
            lvl = str(r.get("level") or "").upper()
            if lvl not in _WARN_LEVELS:
                continue
            dt = _parse_ts(r.get("ts"))
            if dt is None or dt.timestamp() < cutoff:
                continue
            reason = str(r.get("reason") or r.get("action") or "").strip()
            if not reason:
                continue
            # Dedup a per-tick repeat: normalise digits (e.g. "failures: 3/4/5").
            key = (lvl, re.sub(r"\d+", "#", reason)[:80])
            if key in seen:
                continue
            seen.add(key)
            rows.append((dt, lvl, reason, r.get("action"), r.get("status")))
        rows.sort(key=lambda x: x[0], reverse=True)
        for dt, lvl, reason, action, status in rows[:_WARN_MAX_BANNERS]:
            sev = "alert" if lvl in ("ERROR", "CRITICAL") else "warning"
            msg = reason if len(reason) <= 160 else reason[:157] + "…"
            detail = None
            if action:
                detail = f"{action}" + (f" · {status}" if status else "")
            out.append({
                "severity": sev,
                "kind": "operator_warning",
                "message": msg,
                "detail": detail,
                "since": dt.isoformat(),
            })
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: operator-warning banners failed: %s", exc)
    return out


def _operator_alert_banners() -> List[Dict[str, Any]]:
    """Recent durable operator alerts as banners.

    The trader's ``execution_diagnostics.enqueue_*`` alerts (stuck close, orphan
    flag, failed dispatch, …) Telegram via transient pending-ping files that the
    sender consumes + deletes — so they can't back this banner. Every such alert
    now ALSO appends a structured row to ``runtime_logs/operator_alerts.jsonl``
    (a bounded ring); this reads its recent tail so a live operational condition
    — e.g. the ``alpaca_paper`` QQQ "Position CLOSE failing — won't flatten" —
    surfaces on the Overview banner, not only in Telegram. ``priority=="critical"``
    → ``alert``, anything else → ``warning``. Deduped by (kind, digit-normalised
    first line) so a per-tick close-retry collapses to one banner; newest-first,
    capped. Best-effort — a missing/garbled log yields no banners, never raises.
    """
    out: List[Dict[str, Any]] = []
    try:
        path = runtime_logs_dir() / "operator_alerts.jsonl"
        if not path.is_file():
            return out
        cutoff = datetime.now(timezone.utc).timestamp() - _warning_window_min() * 60
        seen: set = set()
        rows: List[tuple] = []
        for line in _tail_lines(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (ValueError, TypeError):
                continue
            dt = _parse_ts(r.get("ts"))
            if dt is None or dt.timestamp() < cutoff:
                continue
            body = str(r.get("body") or "").strip()
            if not body:
                continue
            kind = str(r.get("kind") or "operator_alert").strip() or "operator_alert"
            body_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            head = body_lines[0] if body_lines else body
            key = (kind, re.sub(r"\d+", "#", head)[:80])
            if key in seen:
                continue
            seen.add(key)
            prio = str(r.get("priority") or "high").lower()
            rows.append((dt, kind, prio, head, body_lines[1:]))
        rows.sort(key=lambda x: x[0], reverse=True)
        for dt, kind, prio, head, rest in rows[:_WARN_MAX_BANNERS]:
            sev = "alert" if prio == "critical" else "warning"
            msg = head if len(head) <= 160 else head[:157] + "…"
            detail = " · ".join(rest[:4]) or None
            if detail and len(detail) > 300:
                detail = detail[:297] + "…"
            out.append({
                "severity": sev,
                "kind": kind,
                "message": msg,
                "detail": detail,
                "since": dt.isoformat(),
            })
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: operator-alert banners failed: %s", exc)
    return out


@router.get("/notifications")
def get_notifications() -> Dict[str, Any]:
    """Aggregate the active banner-worthy conditions (Tier 1, best-effort)."""
    banners: List[Dict[str, Any]] = []

    tb = _trainer_banner()
    if tb:
        banners.append(tb)
    banners.extend(_account_down_banners())
    banners.extend(_operator_alert_banners())
    banners.extend(_recent_warning_banners())
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
