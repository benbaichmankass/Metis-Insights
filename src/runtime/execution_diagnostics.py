"""Per-account execution-failure diagnostic ping.

When ``Coordinator.multi_account_execute`` fails to route a strategy's
order package to a live account, the operator needs an immediate
human-readable description of *which* account refused, *what* package
was dropped, and *why*. The previous wiring buried the failure inside
the audit log; this module surfaces it via the existing pending-pings
inbox (``runtime_logs/pending_pings/``) — the same channel the
``ict-telegram-bot`` job-queue tick drains every ~5 s.

Design rules:

- **Asynchronous.** Producers drop a JSON file via ``os.replace`` and
  return; nothing in the order path waits on Telegram. A failed
  enqueue only logs a warning — the order-routing failure is already
  surfaced via the result dict + pipeline audit log, so the diagnostic
  ping is best-effort.
- **No secrets.** The body is plain text limited to fields the operator
  already sees in ``/accounts_status`` (account name, strategy, symbol,
  side, qty) and a short failure reason. No API keys, no balance
  values, no SDK exception payloads beyond ``type(exc).__name__``.
- **Idempotent enough.** Each ping gets a unique filename via
  ``uuid.uuid4`` so duplicates from a flapping pipeline tick don't
  collide. The bot's drainer deletes after send; nothing here needs a
  retry queue.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

PENDING_PINGS_DIR = runtime_logs_dir() / "pending_pings"


def enqueue_execution_failure(
    *,
    account: str,
    strategy: str,
    symbol: str,
    side: str,
    qty: Optional[float],
    reason: str,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for a per-account execution failure.

    Returns the path of the queued file on success, ``None`` when the
    enqueue itself fails (e.g. read-only filesystem in a sandboxed
    test). Failure to enqueue is logged at WARN — never raises.
    """
    try:
        body = (
            "⚠️ Order execution failed\n"
            f"Account: {account}\n"
            f"Strategy: {strategy}\n"
            f"Symbol: {symbol} | Side: {side} | Qty: {qty if qty is not None else '?'}\n"
            f"Reason: {reason}"
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-execfail.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: enqueue failed for account=%s reason=%r: %s",
            account, reason[:80], exc,
        )
        return None


def enqueue_orphan_reconciliation(
    *,
    account: str,
    symbol: str,
    side: str,
    db_trade_id: Any,
    linked_package_id: Optional[str],
    reason: str = "reconciler",
    classification: Optional[str] = None,
    classification_note: Optional[str] = None,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for a monitor-loop orphan match.

    Mirrors :func:`enqueue_execution_failure`'s shape so the bot's
    drainer treats both pings the same way. Fired by
    ``order_monitor._reconcile_open_trades`` whenever the DB shows a
    trade as ``status='open'`` but the exchange's open-positions list
    does not include the matching ``(symbol, side)`` row — meaning the
    exchange independently closed the position without the trader
    seeing the close, and the DB row has been re-tagged
    ``status='orphaned'`` with ``exit_reason='reconciler'``.

    *classification* is an optional tag distinguishing "this trade
    can ONLY have been closed by an external action" (spot-margin —
    no exchange-side SL/TP path exists) from "could be either SL/TP
    or operator close" (derivatives). Surfaced in the body so the
    operator knows whether to investigate or just acknowledge.

    The body is operator-actionable (`/last5` will show the linked
    trade) and intentionally lean — no SDK exception payloads, no
    balance values, just identifiers.
    """
    try:
        lines = [
            "🧹 Monitor reconciler — orphaned trade swept",
            f"Account: {account}",
            f"Symbol: {symbol} | Side: {side}",
            f"DB trade id: {db_trade_id}",
            f"Package: {linked_package_id or '(unlinked)'}",
            f"Reason: {reason}",
        ]
        if classification:
            lines.append(f"Classification: {classification}")
        if classification_note:
            lines.append(f"Note: {classification_note}")
        body = "\n".join(lines)[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-reconciler.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan-ping enqueue failed for "
            "account=%s symbol=%s db_trade_id=%s: %s",
            account, symbol, db_trade_id, exc,
        )
        return None


def enqueue_exchange_orphan_adoption(
    *,
    account: str,
    symbol: str,
    side: str,
    size: float,
    entry_price: float,
    db_trade_id: Optional[int],
    policy: str,
    note: Optional[str] = None,
    priority: str = "high",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for an EXCHANGE-SIDE orphan
    adoption — the reverse direction of :func:`enqueue_orphan_reconciliation`.

    Forward orphan (existing): DB shows a trade open, exchange doesn't.
    Reverse orphan (this one):  Exchange shows a position, DB doesn't.

    Fired by ``order_monitor._reconcile_orphan_exchange_positions``
    when ``account_open_positions`` reports a Bybit position for which
    there is no matching ``trades`` row with ``status='open'``. The
    2026-05-11 incident (BTCUSDT bybit_2 vwap LONG opened at 07:17:27Z,
    journal row vanished, position remained live on Bybit) is the
    motivating case: without this ping the operator finds out only by
    coincidence that the bot has stopped tracking a real position.

    *policy* is the resolved ORPHAN_POSITION_POLICY (``detect_only`` /
    ``adopt`` / ``close``) so the alert text matches what actually
    happened — e.g. an ``adopt`` ping confirms a new trade row was
    inserted, while ``detect_only`` makes clear that the operator
    must decide.
    """
    try:
        icon = {"adopt": "🪝", "close": "🛑", "detect_only": "👁"}.get(
            policy, "❓"
        )
        lines = [
            f"{icon} Exchange-side orphan position — policy={policy}",
            f"Account: {account}",
            f"Symbol: {symbol} | Side: {side} | Size: {size}",
            f"Entry (Bybit avgPrice): {entry_price}",
        ]
        if db_trade_id is not None:
            lines.append(f"DB trade id (adopted): {db_trade_id}")
        if note:
            lines.append(f"Note: {note}")
        body = "\n".join(lines)[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-exch-orphan.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: exchange-orphan ping enqueue failed for "
            "account=%s symbol=%s side=%s: %s",
            account, symbol, side, exc,
        )
        return None


def enqueue_all_accounts_failed_dispatch(
    *,
    strategy: str,
    symbol: str,
    side: str,
    results: list,
    priority: str = "high",
) -> Optional[Path]:
    """Aggregate ping for "tried to dispatch this signal, NOTHING landed".

    Background — when a strategy fires a signal and every account in
    ``multi_account_execute`` errors (or is below balance / refused
    by the risk gate), the operator sees N per-account pings. If the
    bot is consistently in this state (e.g. after a Bybit ErrCode
    170131 cascade — trade 875 / 876, 2026-05-08), the per-account
    spam mixes with normal noise and the "trader is silent" signal
    is missed.

    This helper emits one high-priority roll-up after each fully-
    failed dispatch round, summarising the failure reasons inline
    so the operator can see at a glance whether it's a transient
    creds issue, a market-wide rejection, or a balance-floor
    exhaustion.

    *results* is the list returned by ``multi_account_execute``.
    Each entry has ``name``, ``error``, ``trade_id`` keys.

    Returns the queued path on success, ``None`` on enqueue failure.
    Never raises — the dispatch round already returned its results.
    """
    try:
        if not results:
            return None
        attempted = len(results)
        placed = sum(1 for r in results if r.get("trade_id") is not None)

        # Summarise reasons with the account name. Cap to 5 lines so
        # the body stays under Telegram's 4096-char limit even with
        # very long SDK exception messages.
        lines = []
        for r in results[:5]:
            name = str(r.get("name") or "?")
            err = str(r.get("error") or "no_trade_placed")
            # Trim long reason strings — operator will see the full
            # detail in the per-account ping if needed.
            err_short = err[:120] + ("…" if len(err) > 120 else "")
            lines.append(f"  • {name}: {err_short}")
        suppressed = attempted - len(lines)
        if suppressed > 0:
            lines.append(f"  • … and {suppressed} more")

        body = (
            "🚨 ALL accounts failed to dispatch\n"
            f"Strategy: {strategy} | Symbol: {symbol} | Side: {side}\n"
            f"Accounts attempted: {attempted} | Trades placed: {placed}\n"
            "Failures:\n" + "\n".join(lines)
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-allfail.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: all-accounts-failed enqueue failed for "
            "strategy=%s symbol=%s: %s",
            strategy, symbol, exc,
        )
        return None


def enqueue_stuck_strategy_alert(
    *,
    strategy: str,
    symbol: str,
    order_package_id: str,
    db_trade_id: Any,
    stuck_minutes: int,
    auto_cleared: bool,
    priority: str = "high",
) -> Optional[Path]:
    """High-priority watchdog ping when the strategy-monocle gate has
    been blocked by a single package for too long.

    This is the last line of defence after the orphan reconciler,
    `_sweep_stuck_linked_packages`, and the strategy's own monitor()
    loop have all had a chance to clear the package and didn't. By
    the time this fires, something has gone meaningfully sideways —
    the operator must investigate.

    *auto_cleared* is True when the watchdog also force-closed the
    package + cascaded the linked trade row in the same tick. False
    when alerting was idempotency-only (the package was already
    flagged on a previous tick).
    """
    try:
        verb = "force-cleared" if auto_cleared else "still stuck"
        body = (
            "🚨 Stuck-strategy watchdog\n"
            f"Strategy: {strategy} | Symbol: {symbol}\n"
            f"Package: {order_package_id}\n"
            f"DB trade id: {db_trade_id}\n"
            f"Stuck for: {stuck_minutes} min\n"
            f"Action: {verb}\n"
            "Investigate: the orphan reconciler + stuck-linked sweep "
            "did NOT catch this — possible exchange-side stale "
            "position or reconciler skip path."
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-stuckstrat.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: stuck-strategy enqueue failed for "
            "strategy=%s pkg=%s: %s",
            strategy, order_package_id, exc,
        )
        return None


def enqueue_naked_position_alert(
    *,
    trade_id: Any,
    account: str,
    symbol: str,
    side: str,
    sl: Optional[float],
    tp: Optional[float],
    priority: str = "critical",
) -> Optional[Path]:
    """Drop a Telegram-ready JSON ping for an open trade without valid SL/TP.

    Fired once per trade by ``_check_naked_positions`` in the monitor loop.
    Priority is critical — a live position without SL/TP is unacceptable.
    """
    try:
        sl_str = f"{sl:.4f}" if isinstance(sl, (int, float)) else "NULL"
        tp_str = f"{tp:.4f}" if isinstance(tp, (int, float)) else "NULL"
        body = (
            "🚨 NAKED POSITION — open trade has no valid SL/TP\n"
            f"Trade id: {trade_id}\n"
            f"Account: {account}\n"
            f"Symbol: {symbol} | Side: {side}\n"
            f"stop_loss={sl_str}  take_profit_1={tp_str}\n"
            "Action: check trade on exchange and set SL/TP manually."
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-naked-position.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: naked-position ping enqueue failed for "
            "trade_id=%s symbol=%s: %s",
            trade_id, symbol, exc,
        )
        return None


def enqueue_orphan_rollup(
    *,
    suppressed_count: int,
    priority: str = "high",
) -> Optional[Path]:
    """One roll-up ping summarising orphans the per-orphan cap dropped.

    The reconciler caps individual orphan pings per tick to avoid
    flooding the operator when a long-stale DB has accumulated dozens
    of ghosts. Anything past the cap is summarised here.
    """
    try:
        body = (
            "🧹 Monitor reconciler — additional orphans not individually pinged\n"
            f"Suppressed: {suppressed_count} more orphan(s) this tick. "
            f"See /last5 / /packages for the full list."
        )[:1024]
        payload = {"priority": priority, "body": body}
        PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{int(uuid.uuid4().int % 10**12):012d}-reconciler-rollup.json"
        path = PENDING_PINGS_DIR / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution_diagnostics: orphan-rollup enqueue failed "
            "(suppressed=%d): %s",
            suppressed_count, exc,
        )
        return None
