"""Open-position helpers — read the live net position for an account+symbol.

The trade journal (``trade_journal.db::trades``) is the authoritative
record of every live order the executor has placed. Rows transition from
``status='open'`` to ``'closed'`` via the monitor loop; the open subset
sums (signed by direction) to the current net position for an account
and symbol.

This helper exists so the intent-aware dispatch path in
``Coordinator.multi_account_execute`` can pick "are we already at the
desired target?" / "how much delta do we still need?" decisions from a
single read of the journal instead of duplicating the SELECT in
``_has_open_position`` style scattered across modules.

Schema reference
----------------
``CREATE TABLE trades`` columns relevant here (from
``src/units/db/database.py``):

  - id              INTEGER (PK)
  - direction       TEXT      ``"long" | "short"``
  - position_size   REAL      qty in base-coin units (BTC for BTCUSDT)
  - status          TEXT      ``"open"`` for live unfilled positions
  - is_backtest     BOOLEAN   filter to ``0`` (or NULL) for live rows
  - account_id      TEXT
  - symbol          TEXT

Best-effort: a journal-read failure returns ``0.0`` (treated as flat) and
logs a warning. The dispatcher then falls back to risk-manager sized
qty for the order, which is the same behaviour as today — the delta
path is an optimisation on top of the existing safe default.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time as _time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _trade_journal_path() -> str:
    from src.utils.paths import trade_journal_db_path
    return trade_journal_db_path()


# ---------------------------------------------------------------------------
# Position-netting / per-trade=per-position guard (BL-20260608-DEMOPNL)
# ---------------------------------------------------------------------------
#
# Option A from docs/audits/position-netting-sltp-2026-06-08.md. Two halves:
#
#   1. Monocle (coordinator intent path): suppress a same-direction
#      re-entry (delta action ``open`` / ``increase``) for a
#      ``(strategy, account, symbol)`` while it already holds an open
#      trade — so a netted ADD can't be created, restoring the
#      per-trade = per-position invariant. No pyramiding/scale-in.
#   2. Reconciler (order_monitor close path): require the position to read
#      net-flat across TWO observations (an extra grace tick) before
#      closing a filled trade, so reduce/flip churn and open-positions
#      index lag can't prematurely close a row and free the monocle.
#
# **BASELINE / unconditional** (2026-06-17). This was previously gated by a
# default-OFF ``POSITION_NETTING_GUARD_ENABLED`` flag during its soak. The soak
# + walk-forward validation passed and it ran live on both Bybit accounts from
# 2026-06-08 — until the 2026-06-14 Ampere migration dropped the .env vars and
# the fix silently regressed (paper netting artifacts reappeared on 2026-06-15;
# real-money bybit_2 was exposed via the same gate). Per-trade=per-position is
# the canonical model the whole system assumes (pyramiding was never a feature)
# and the reconciler close-confirm is pure safety, so the guard is now
# unconditional — no env flag to drop. This mirrors how NAKED_POSITION_AUTOPROTECT
# and the MONITOR_RECONCILE_ENABLED gate were removed: a required correctness
# capability must not sit behind a default-off flag (Prime Directive).
# ``RECONCILER_CLOSE_CONFIRM_SECONDS`` remains as the close-confirm tuning knob.


def position_netting_guard_active_for(account_id: Optional[str]) -> bool:
    """Return True — the position-netting / per-trade=per-position guard is
    BASELINE (unconditional) as of 2026-06-17.

    Kept as the single predicate both halves of the guard (monocle +
    reconciler) consult, so the call sites are unchanged; it no longer reads
    any env flag. ``account_id`` is retained for signature stability (the guard
    applies to every account — it is a no-op where it can't apply, e.g.
    brokers that attach SL/TP atomically and never net same-direction adds).
    """
    return True


def has_open_trade_for_strategy(
    account_id: str,
    symbol: str,
    strategy_name: Optional[str],
    *,
    db_path: Optional[str] = None,
) -> bool:
    """Return True if ``(strategy_name, account_id, symbol)`` already has an
    open live trade in the journal.

    Strategy-scoped sibling of ``_has_open_position`` (which is
    account+symbol scoped). Used by the netting guard's monocle gate to
    block a same-direction re-entry while the strategy still holds an
    open position — independent of the order_packages status the legacy
    strategy-monocle gate keys on (so a prematurely-closed package can't
    free the gate while the position is genuinely still open).

    Best-effort: a missing journal or read failure returns ``False``
    (i.e. "no open trade known" → don't block) — fail-permissive, matching
    every other guard helper so a transient SQLite hiccup never strands a
    live signal.
    """
    if not strategy_name:
        return False
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return False
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE account_id = ? AND symbol = ? AND strategy_name = ? "
                "AND status = 'open' AND COALESCE(is_backtest, 0) = 0",
                (account_id, symbol, strategy_name),
            ).fetchone()
        return bool(row and row[0] > 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "has_open_trade_for_strategy: read failed for account=%s "
            "symbol=%s strategy=%s: %s (treating as no-open-trade)",
            account_id, symbol, strategy_name, exc,
        )
        return False


def current_net_position_qty(
    account_id: str,
    symbol: str,
    *,
    db_path: Optional[str] = None,
) -> float:
    """Return the signed net position qty for an ``(account, symbol)``.

    Sum of ``position_size`` across rows with ``status='open'`` AND
    ``is_backtest=0`` (or NULL), signed by direction:

      - ``direction='long'``  → contributes ``+position_size``
      - ``direction='short'`` → contributes ``-position_size``

    Rows with any other direction value are ignored (defensive — the
    schema constrains direction but legacy data may contain stragglers).

    Parameters
    ----------
    account_id : str
        Per-account key in ``config/accounts.yaml`` (e.g. ``"bybit_2"``).
    symbol : str
        Trading symbol (e.g. ``"BTCUSDT"``).
    db_path : str, optional
        Override the default ``trade_journal.db`` path. ``TRADE_JOURNAL_DB``
        env var takes precedence when this is None.

    Returns
    -------
    float
        Signed net qty. ``0.0`` when:
          * no open rows match, OR
          * the trade journal file does not exist (fresh deploy), OR
          * the SELECT fails.

    Notes
    -----
    This intentionally reads the trade journal, not the exchange. The
    journal is what the per-account RiskManager / dispatcher already
    treat as the source of truth (cf. ``_has_open_position`` in
    ``src/core/coordinator.py``). Reconciliation with exchange-side
    state lives elsewhere (``src/runtime/order_monitor.py``) and is
    out of scope for this helper.
    """
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return 0.0

    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT direction, position_size "
                "FROM trades "
                "WHERE account_id = ? AND symbol = ? "
                "AND status = 'open' AND COALESCE(is_backtest, 0) = 0",
                (account_id, symbol),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "current_net_position_qty: read failed for account=%s symbol=%s: %s "
            "(treating as flat)",
            account_id, symbol, exc,
        )
        return 0.0

    net = 0.0
    for direction, qty in rows:
        if qty is None:
            continue
        try:
            q = float(qty)
        except (TypeError, ValueError):
            continue
        d = (direction or "").lower()
        if d == "long":
            net += q
        elif d == "short":
            net -= q
        # Any other direction value (legacy data) is silently skipped —
        # logging here would be noisy on every dispatch tick.
    return net


def net_positions_by_symbol(*, db_path: Optional[str] = None) -> dict[str, float]:
    """Return signed net qty per symbol aggregated across all live accounts.

    Queries all open, non-backtest trades in the trade journal, summing
    signed qty (long +, short −) per symbol irrespective of account.

    Parameters
    ----------
    db_path : str, optional
        Override the default ``trade_journal.db`` path.

    Returns
    -------
    dict[str, float]
        ``{symbol: net_qty}``.  Only symbols with non-zero net are included.
        Returns an empty dict when the journal doesn't exist or the read fails.
    """
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return {}

    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT symbol, direction, position_size "
                "FROM trades "
                "WHERE status = 'open' AND COALESCE(is_backtest, 0) = 0",
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "net_positions_by_symbol: read failed: %s (treating all as flat)", exc
        )
        return {}

    acc: dict[str, float] = {}
    for symbol, direction, qty in rows:
        if symbol is None or qty is None:
            continue
        try:
            q = float(qty)
        except (TypeError, ValueError):
            continue
        d = (direction or "").lower()
        if d == "long":
            acc[symbol] = acc.get(symbol, 0.0) + q
        elif d == "short":
            acc[symbol] = acc.get(symbol, 0.0) - q
    return acc


def get_existing_position_info(
    account_id: str,
    symbol: str,
    *,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return confidence, age, entry, strategy + OP id for the most-recent open trade.

    Used by the confidence-weighted flip override (to decide whether a newer
    signal is strong enough to supersede the hold policy) AND by the
    ``FLIP_POLICY=selective`` displaced-intent record (Unit A § 7.2 — the held
    trend's order-package id is where the record is persisted, and its entry is
    the re-entry zone anchor).

    Returns a dict ``{"confidence": float|None, "age_hours": float|None,
    "entry": float|None, "strategy": str|None, "order_package_id": str|None}``
    for the most recent open trade, or ``None`` on journal miss / read failure
    (fail-permissive — a read error never blocks a signal). The
    ``order_package_id`` is the *string* ``order_packages.order_package_id``
    (what ``update_order_package`` keys on), resolved via the canonical
    ``trades.order_package_id`` → ``order_packages.id`` join.
    """
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return None
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT t.created_at, op.confidence, op.entry, "
                "       t.strategy_name, op.order_package_id "
                "FROM trades t "
                "LEFT JOIN order_packages op ON t.order_package_id = op.id "
                "WHERE t.account_id = ? AND t.symbol = ? "
                "  AND t.status = 'open' AND COALESCE(t.is_backtest, 0) = 0 "
                "ORDER BY t.id DESC LIMIT 1",
                (account_id, symbol),
            ).fetchone()
        if not row:
            return None
        created_at_raw, confidence, entry, strategy_name, op_id = row
        age_hours: Optional[float] = None
        if created_at_raw:
            try:
                try:
                    ts = float(created_at_raw) / 1000.0
                except (TypeError, ValueError):
                    import datetime as _dt
                    s = str(created_at_raw).replace("Z", "+00:00")
                    ts = _dt.datetime.fromisoformat(s).timestamp()
                age_hours = (_time.time() - ts) / 3600.0
            except Exception:  # noqa: BLE001
                pass
        return {
            "confidence": float(confidence) if confidence is not None else None,
            "age_hours": age_hours,
            "entry": float(entry) if entry is not None else None,
            "strategy": str(strategy_name) if strategy_name else None,
            "order_package_id": str(op_id) if op_id else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_existing_position_info: read failed for account=%s symbol=%s: %s",
            account_id, symbol, exc,
        )
        return None
