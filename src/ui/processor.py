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


def get_hourly_report() -> str:
    """Return the structured hourly report string, or a clear error.

    Single source of truth so the bot's ``/hourly`` and the webapp's
    "summary" widget render identical text.
    """
    try:
        from src.runtime.hourly_report import build_hourly_report
        return build_hourly_report()
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_hourly_report: build failed")
        return f"⚠️ hourly report unavailable: {type(exc).__name__}: {exc}"
