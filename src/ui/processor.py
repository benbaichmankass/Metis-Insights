"""UI processor — single facade between any UI surface and the data layer.

Background (CP-2026-05-02): the operator flagged that some Telegram
formatting and balance-rendering had grown into the bot module itself,
making it impossible to guarantee that the webapp and the bot would
return the same answer to the same question. The remediation is the
unit specified in CLAUDE.md:

  * The bot and the webapp are both UIs.
  * They MUST go through the same processor.
  * The processor is the only thing that talks to the units (accounts,
    strategies, runtime, journal) and to the DB.

This module is the processor. It exposes plain-Python read APIs that
return shape-stable dicts/lists. Renderers (Telegram strings, JSON for
the webapp) live in their own UI surfaces and consume these dicts.

The first read APIs cover the user-visible bugs reported in this
session:

  * ``get_account_balances()`` — per-account balance, account_id-first
    labelling, includes the resolved API-key fingerprint so duplicate
    keys are visible at the data layer (not buried in the formatter).
  * ``get_recent_signals(limit, strategy=None)`` — pipeline signals
    with explicit ``strategy`` field every consumer can show.
  * ``get_hourly_report()`` — the structured hourly text. Both bot and
    webapp render the same string.

The processor never raises. Failures are encoded as fields in the
return value so renderers can show a sane error to the operator
without try/except in every UI handler.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Account balances
# ---------------------------------------------------------------------------


def _safe_key_fingerprint(account: Dict[str, Any]) -> Optional[str]:
    """Return the last 4 chars of the resolved API key, or None.

    Source of truth lives in ``src.units.accounts.clients.resolve_credentials``
    so the processor doesn't reach into env vars itself.
    """
    try:
        from src.units.accounts.clients import resolve_credentials
        creds = resolve_credentials(account) or {}
        key = creds.get("api_key") or ""
        if not key:
            return None
        return f"…{str(key)[-4:]}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("_safe_key_fingerprint: %s", exc)
        return None


def get_account_balances() -> List[Dict[str, Any]]:
    """Return one row per account.

    Shape:
      [
        {
          "account_id": str,
          "exchange": str,
          "strategies": [str, ...],
          "key_fingerprint": str | None,   # "…xxxx"
          "status": "ok" | "missing_creds" | "api_error" | "unsupported",
          "total_usdt": float | None,
          "raw": dict | None,              # exchange-native payload
          "error": str | None,
        },
        ...
      ]

    Renderers MUST label rows by ``account_id`` first; the strategies
    list is informational. Showing two accounts under the same strategy
    label was the root cause of the operator's "balances wired to
    strategies" complaint.
    """
    try:
        from src.bot.data_loaders import (
            account_balance_with_diagnostic,
            list_accounts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_account_balances: data_loaders import failed: %s", exc)
        return []

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_account_balances: list_accounts failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for acc in accounts:
        try:
            diag = account_balance_with_diagnostic(acc) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_account_balances: %s raised %s",
                (acc or {}).get("account_id", "?"), exc,
            )
            diag = {"status": "api_error", "total_usdt": None, "raw": None,
                    "error": f"{type(exc).__name__}: {exc}"}
        out.append({
            "account_id": acc.get("account_id"),
            "exchange": acc.get("exchange"),
            "strategies": list(acc.get("strategies") or []),
            "key_fingerprint": _safe_key_fingerprint(acc),
            "status": diag.get("status"),
            "total_usdt": diag.get("total_usdt"),
            "raw": diag.get("raw"),
            "error": diag.get("error"),
        })
    return out


# ---------------------------------------------------------------------------
# Recent signals (pipeline audit log)
# ---------------------------------------------------------------------------


def get_recent_signals(
    limit: int = 10,
    strategy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the last ``limit`` records from the pipeline audit log.

    Each row is a flat dict with at least:
      ``logged_at_utc``, ``strategy``, ``symbol``, ``side``, ``qty``,
      ``status``, ``reason``.

    UI surfaces that render this MUST display the ``strategy`` field;
    the operator explicitly asked for it on every row.
    """
    import json
    import os
    from collections import deque

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    audit = os.environ.get("SIGNAL_AUDIT_PATH") or os.path.join(
        repo_root, "runtime_logs", "signal_audit.jsonl",
    )
    if not os.path.exists(audit):
        return []
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 10

    wanted = max(limit * 4, 50)
    try:
        with open(audit, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=wanted)
    except OSError as exc:
        logger.warning("get_recent_signals: read failed: %s", exc)
        return []

    rows: List[Dict[str, Any]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rec.setdefault("strategy", "unknown")
        rows.append(rec)

    if strategy:
        s = strategy.strip().lower()
        rows = [r for r in rows if str(r.get("strategy", "")).lower() == s]
    return rows[-limit:]


# ---------------------------------------------------------------------------
# Hourly report passthrough
# ---------------------------------------------------------------------------


def get_hourly_report(
    *,
    now_utc: Optional[Any] = None,
    tick_interval_s: int = 900,
) -> str:
    """Return the structured hourly report string, or a clear error.

    Single source of truth so the bot's ``/hourly``, the in-process
    scheduler in ``src/main.py``, and the webapp's "summary" widget all
    render identical text.

    ``now_utc`` and ``tick_interval_s`` forward to
    ``src.runtime.hourly_report.build_hourly_report``. Callers that
    want "now / 15-min ticks" can omit both. The bot's ``/hourly``
    handler passes an explicit ``now_utc`` so the report's window
    matches the operator's invocation time exactly.
    """
    try:
        from src.runtime.hourly_report import build_hourly_report
        kwargs: Dict[str, Any] = {"tick_interval_s": tick_interval_s}
        if now_utc is not None:
            kwargs["now_utc"] = now_utc
        return build_hourly_report(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_hourly_report: build failed")
        return f"⚠️ hourly report unavailable: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Status helpers — S-031 PR1 (architecture-audit-2026-05-02 P1-6)
# ---------------------------------------------------------------------------


def get_today_pnl(account_id: Optional[str] = None) -> Dict[str, Any]:
    """Return today's trade count + total realised PnL.

    Pre-PR (BUG-shaped): ``src/bot/telegram_query_bot.py::fetch_today_pnl``
    opened ``trade_journal.db`` directly. Per CLAUDE.md § Architecture
    rules § 5 the bot is a thin shell — DB access belongs to the UI
    unit. Both the Telegram bot and the webapp call this helper.

    Parameters
    ----------
    account_id : str, optional
        Filter to a single account's rows. None → all accounts.

    Returns
    -------
    dict
        ``{"trade_count": int, "total_pnl_usd": float, "as_of_utc_date": str}``.
        Never raises; on a DB failure returns zero counts with the
        same shape so renderers can show ``"N/A"`` without try/except.
    """
    import os
    import sqlite3
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db_path = os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"
    try:
        conn = sqlite3.connect(db_path)
        try:
            if account_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                    "WHERE DATE(timestamp) = ? AND is_backtest = 0 "
                    "AND account_id = ?",
                    (today, account_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                    "WHERE DATE(timestamp) = ? AND is_backtest = 0",
                    (today,),
                ).fetchone()
        finally:
            conn.close()
        return {
            "trade_count": int(row[0] or 0) if row else 0,
            "total_pnl_usd": float(row[1] or 0.0) if row else 0.0,
            "as_of_utc_date": today,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_today_pnl: %s", exc)
        return {
            "trade_count": 0,
            "total_pnl_usd": 0.0,
            "as_of_utc_date": today,
        }


def get_open_positions_count(account_id: Optional[str] = None) -> int:
    """Return the count of open, non-backtest trade rows.

    Pre-PR: ``src/bot/telegram_query_bot.py::fetch_open_positions_count``
    opened ``trade_journal.db`` directly. Now goes through the UI
    unit per CLAUDE.md § Architecture rules § 5.

    Returns 0 on any error (logged) so renderers can show a sane value.
    """
    import os
    import sqlite3

    db_path = os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"
    try:
        conn = sqlite3.connect(db_path)
        try:
            if account_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM trades "
                    "WHERE status = 'open' AND is_backtest = 0 "
                    "AND account_id = ?",
                    (account_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM trades "
                    "WHERE status = 'open' AND is_backtest = 0"
                ).fetchone()
        finally:
            conn.close()
        return int(row[0] or 0) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_open_positions_count: %s", exc)
        return 0
