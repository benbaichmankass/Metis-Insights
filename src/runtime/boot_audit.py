"""Boot-time open-package observability ping and journal/exchange reconcile.

``report_open_packages_on_boot`` (Sprint S-021, PR 2 of 3):
  Log + Telegram-ping the open linked-package count per strategy on every
  trader startup.  Addresses the BUG-048 gap: trade #24 hid for 8 hours
  after a VM restart because nothing told the operator the bot was carrying
  an open position.

``reconcile_journal_vs_exchange_on_boot`` (Sprint A-3):
  Immediately after the package count ping, compare the trade journal's
  open rows against live Bybit positions per account.  Ghost rows (journal
  says open, Bybit says flat) are highlighted immediately so the operator
  can investigate before the first trading tick.  Untracked positions
  (Bybit open, no journal row) are logged but not alerted — they may be
  manual positions.

Design constraints (both functions)
------------------------------------
- Best-effort only.  Neither function may raise — a DB outage or creds
  failure must never prevent the trader from starting.
- Plain-text Telegram body (no ``parse_mode``).
- Silent on clean state (0 ghosts → log only, no Telegram noise).
- Reconciler only runs on LIVE accounts — dry accounts have no exchange
  positions by definition, and querying creds that are intentionally absent
  would surface misleading warnings.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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


def report_open_packages_on_boot() -> dict[str, Optional[int]]:
    """Log (and optionally Telegram-ping) open linked packages per strategy.

    Returns a ``{strategy_name: open_count_or_None}`` dict for testability.
    A value of ``None`` signals "query failed" (vs ``0`` = "no open
    packages"). Never raises — all exceptions are caught and logged.
    """
    try:
        db = _resolve_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_audit: DB unavailable: %s", exc)
        return {}

    strategies = _load_strategy_names()
    counts: dict[str, Optional[int]] = {}

    for strategy in strategies:
        try:
            rows = db.get_order_packages_by_strategy(
                strategy, status="open", linked_only=True,
            )
            counts[strategy] = len(rows) if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("boot_audit: query failed for strategy=%s: %s", strategy, exc)
            counts[strategy] = None

    total = sum(n for n in counts.values() if n is not None)
    failed = [s for s, n in counts.items() if n is None]
    counts_str = ", ".join(
        f"{s}=(query failed)" if n is None else f"{s}={n}"
        for s, n in counts.items()
    ) or "no strategies"
    logger.info(
        "boot_audit: %d open package(s), %d query failure(s) on boot — %s",
        total, len(failed), counts_str,
    )

    if total > 0 or failed:
        _send_boot_ping(counts, total)

    return counts


def _send_boot_ping(counts: dict[str, Optional[int]], total: int) -> None:
    lines = ["Trader restart — resuming monitoring"]
    failed: list[str] = []
    for strategy, n in counts.items():
        if n is None:
            lines.append(f"{strategy}: (query failed)")
            failed.append(strategy)
        else:
            lines.append(f"{strategy}: {n} open package(s)")
    if failed:
        lines.append(
            f"WARNING: per-strategy query failed for {len(failed)} strategy "
            f"({', '.join(failed)}) — check bot.log for details."
        )
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


# ---------------------------------------------------------------------------
# Sprint A-3: journal vs exchange reconciliation on boot
# ---------------------------------------------------------------------------

def _load_account_cfgs() -> Dict[str, Dict[str, Any]]:
    """Return {account_id: cfg_dict} for enabled accounts.

    Replicates the minimal shape that ``account_open_positions`` needs:
    ``account_id``, ``exchange``, ``api_key_env``, ``api_secret_env``,
    ``mode``, ``market_type``.
    """
    try:
        from src.config.accounts_loader import load_accounts_dict
        raw = load_accounts_dict()
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort config read; reconciler runs as a no-op on failure
        logger.warning("boot_reconcile: could not load accounts config: %s", exc)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in (raw or {}).items():
        if cfg.get("enabled") is False:
            continue
        out[str(name)] = {
            "account_id": str(name),
            "exchange": cfg.get("exchange", "bybit"),
            "api_key_env": cfg.get("api_key_env"),
            "api_secret_env": cfg.get("api_secret_env"),
            "mode": cfg.get("mode") or "live",
            "market_type": cfg.get("market_type") or "spot",
        }
    return out


def _open_journal_trades(account_id: str, db_path: str) -> List[Dict[str, Any]]:
    """Return open non-backtest trade rows for *account_id* from the journal."""
    if not os.path.exists(db_path):
        return []
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, symbol, side FROM trades "
                "WHERE account_id = ? AND status = 'open' "
                "AND COALESCE(is_backtest, 0) = 0",
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort journal read; empty list lets reconciler skip account safely
        logger.warning(
            "boot_reconcile: journal query failed for account=%s: %s",
            account_id, exc,
        )
        return []


def reconcile_journal_vs_exchange_on_boot() -> Dict[str, Any]:
    """Boot-time read-only check: journal open rows vs live exchange positions.

    For each live (non-dry) Bybit linear account:
      * Reads open non-backtest trades from the journal.
      * Queries Bybit for open positions.
      * Ghost rows (journal open, Bybit flat) → Telegram alert + WARNING log.
      * Untracked positions (Bybit open, journal flat) → INFO log only.

    Ghost rows indicate a fill that happened without a journal update (e.g.
    crash between exchange close and DB write).  The operator should
    investigate and close or clean up the ghost rows before normal trading
    resumes.

    This check fires ONCE on startup, before the first tick.  The per-tick
    reconciler in ``order_monitor.py`` handles ongoing drift detection.

    Returns a summary dict for testability:
        ``{checked_accounts, ghost_trades, untracked_positions, errors}``
    Never raises.
    """
    summary: Dict[str, Any] = {
        "checked_accounts": 0,
        "ghost_trades": 0,
        "untracked_positions": 0,
        "errors": 0,
    }

    db_path = os.environ.get("TRADE_JOURNAL_DB") or str(_REPO_ROOT / "trade_journal.db")

    try:
        cfgs = _load_account_cfgs()
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort; config load failure is a no-op not a crash
        logger.warning("boot_reconcile: could not load account configs: %s", exc)
        return summary

    try:
        from src.units.accounts.clients import account_open_positions
    except Exception as exc:  # noqa: BLE001  # allow-silent: import failure means exchange client unavailable; reconciler skips cleanly
        logger.warning("boot_reconcile: could not import account_open_positions: %s", exc)
        return summary

    ghost_alerts: List[str] = []

    for account_id, cfg in cfgs.items():
        # Skip dry/paper accounts — they have no exchange-side positions.
        if str(cfg.get("mode") or "live").lower() in {"dry", "dry_run", "dry-run", "paper"}:
            continue
        # Skip non-bybit accounts — reconciler uses Bybit position API.
        if str(cfg.get("exchange") or "bybit").lower() != "bybit":
            continue

        summary["checked_accounts"] += 1

        # --- journal open trades for this account -------------------------
        try:
            journal_trades = _open_journal_trades(account_id, db_path)
        except Exception as exc:  # noqa: BLE001  # allow-silent: per-account read failure skips that account; never-raise contract
            logger.warning(
                "boot_reconcile: journal query error for account=%s: %s",
                account_id, exc,
            )
            summary["errors"] += 1
            continue

        # --- live positions from Bybit -------------------------------------
        try:
            positions = account_open_positions(cfg)
        except Exception as exc:  # noqa: BLE001  # allow-silent: exchange call failure skips account; never-raise contract
            logger.warning(
                "boot_reconcile: exchange query error for account=%s: %s",
                account_id, exc,
            )
            summary["errors"] += 1
            continue

        if positions is None:
            # Creds unavailable or network error — skip rather than false-alarm.
            logger.warning(
                "boot_reconcile: account=%s positions query returned None "
                "(creds/network issue?) — skipping this account",
                account_id,
            )
            summary["errors"] += 1
            continue

        # Normalise: set of symbols with a live position on exchange.
        exchange_symbols = {
            str(p.get("symbol", "")).upper()
            for p in positions
            if (p.get("size") or 0) > 0
        }

        # --- ghost detection (journal open, exchange flat) ----------------
        for trade in journal_trades:
            symbol = str(trade.get("symbol") or "").upper()
            trade_id = trade.get("id")
            if symbol not in exchange_symbols:
                summary["ghost_trades"] += 1
                msg = (
                    f"account={account_id} trade_id={trade_id} "
                    f"symbol={symbol} — journal=open exchange=flat"
                )
                logger.warning("boot_reconcile: GHOST TRADE: %s", msg)
                ghost_alerts.append(
                    f"trade #{trade_id} {symbol} on {account_id} "
                    f"(journal=open, Bybit=flat)"
                )

        # --- untracked positions (exchange open, no journal row) ----------
        journal_symbols = {
            str(t.get("symbol") or "").upper() for t in journal_trades
        }
        for symbol in exchange_symbols - journal_symbols:
            summary["untracked_positions"] += 1
            logger.info(
                "boot_reconcile: untracked position: account=%s symbol=%s "
                "(exchange=open, journal=no open row) — may be a manual position",
                account_id, symbol,
            )

    logger.info(
        "boot_reconcile: checked_accounts=%d ghost_trades=%d "
        "untracked_positions=%d errors=%d",
        summary["checked_accounts"],
        summary["ghost_trades"],
        summary["untracked_positions"],
        summary["errors"],
    )

    if ghost_alerts:
        _send_ghost_alert(ghost_alerts)

    return summary


def _send_ghost_alert(ghosts: List[str]) -> None:
    n = len(ghosts)
    lines = [
        f"WARNING: {n} ghost trade(s) detected on startup",
        "Journal says 'open' but Bybit reports no position.",
        "Likely cause: bot crashed after exchange close but before journal update.",
        "Action: review and close/mark each row before trading resumes.",
        "",
    ]
    lines.extend(f"  {g}" for g in ghosts)
    message = "\n".join(lines)
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None)
    except Exception as exc:  # noqa: BLE001  # allow-silent: Telegram send failure must never suppress the log entries already written
        logger.warning("boot_reconcile: Telegram ghost alert failed: %s", exc)
