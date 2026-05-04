"""Boot-time open-package observability ping (Sprint S-021, PR 2 of 3).

On every trader startup, log + Telegram-ping the operator with the count of
open (linked) packages per strategy that the bot is about to resume
monitoring.  Addresses the BUG-048 gap: trade #24 hid for 8 hours after a
VM restart because nothing told the operator "I have an open position and
will resume watching it."

Design constraints
------------------
- Best-effort only.  ``report_open_packages_on_boot`` MUST NOT raise — a
  DB outage or import failure must never prevent the trader from starting.
- Plain-text Telegram body (no ``parse_mode``). Per CLAUDE.md § "Always do",
  dynamic content in legacy Markdown blows up on unbalanced delimiters
  (BUG-009, BUG-030, BUG-031).
- Silent on clean restart (0 open packages → log only, no Telegram noise).
- Scopes query to *linked* packages (``linked_trade_id IS NOT NULL``) — the
  same ``linked_only=True`` contract the BUG-046 gate uses. Unlinked packages
  were never placed at the broker and are handled by the reconciler sweep.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_db():
    from src.units.db.database import Database
    path = os.environ.get("TRADE_JOURNAL_DB") or str(_REPO_ROOT / "trade_journal.db")
    return Database(db_path=path)


def _load_strategy_names() -> list[str]:
    try:
        from src.runtime.order_monitor import _load_strategies
        return _load_strategies(None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_audit: could not load strategy list: %s", exc)
        return []


def report_open_packages_on_boot() -> dict[str, int]:
    """Log (and optionally Telegram-ping) open linked packages per strategy.

    Returns a ``{strategy_name: open_count}`` dict for testability.
    Never raises — all exceptions are caught and logged.
    """
    try:
        db = _resolve_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_audit: DB unavailable: %s", exc)
        return {}

    strategies = _load_strategy_names()
    counts: dict[str, int] = {}

    for strategy in strategies:
        try:
            rows = db.get_order_packages_by_strategy(
                strategy, status="open", linked_only=True,
            )
            counts[strategy] = len(rows) if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("boot_audit: query failed for strategy=%s: %s", strategy, exc)
            counts[strategy] = 0

    total = sum(counts.values())
    logger.info(
        "boot_audit: %d open package(s) on boot — %s",
        total,
        ", ".join(f"{s}={n}" for s, n in counts.items()) or "no strategies",
    )

    if total > 0:
        _send_boot_ping(counts, total)

    return counts


def _send_boot_ping(counts: dict[str, int], total: int) -> None:
    lines = ["Trader restart — resuming monitoring"]
    for strategy, n in counts.items():
        lines.append(f"{strategy}: {n} open package(s)")
    lines.append(f"Total: {total} open package(s) carried forward.")
    lines.append(
        "Bybit holds SL/TP at the broker for every open position; "
        "the monitor loop will re-attach within one tick."
    )
    message = "\n".join(lines)
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_audit: Telegram ping failed: %s", exc)
