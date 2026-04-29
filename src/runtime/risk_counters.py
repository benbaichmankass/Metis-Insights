"""
Live risk counter injection for the pipeline.

Fetches CURRENT_OPEN_POSITIONS and CURRENT_DAILY_LOSS_USD at order time
so that safe_place_order's hard risk guards have real values to check.

Also provides inject_per_strategy_counters (S-005 M2) which injects
STRATEGY_OPEN_POSITIONS and STRATEGY_DAILY_PNL for a named strategy.

Kept in a standalone module (stdlib-only imports) so it can be unit-tested
without pulling in pandas or any exchange dependency.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def inject_runtime_counters(settings: dict, exchange_client: Any) -> dict:
    """Return a copy of *settings* augmented with live risk counters.

    CURRENT_OPEN_POSITIONS  — from exchange_client.get_positions() if the
                              method is available (Bybit/Binance connectors).
    CURRENT_DAILY_LOSS_USD  — from the trade journal DB: sum of pnl for
                              closed, non-backtest trades dated today.
                              Formula: abs(min(0, sum_pnl)) so a positive
                              PnL day yields 0 loss, not a negative value.

    Both fetches swallow all exceptions.  Any error leaves the counter
    absent from the returned dict so the corresponding safe_place_order
    guard is silently skipped rather than blocking a tick on a flaky
    exchange API or DB.
    """
    s = dict(settings)

    # ---- open positions: exchange ground truth ----
    if exchange_client is not None and hasattr(exchange_client, "get_positions"):
        try:
            positions = exchange_client.get_positions()
            s["CURRENT_OPEN_POSITIONS"] = str(len(positions))
        except Exception as exc:
            logger.warning("inject_runtime_counters: exchange positions error — %s", exc)

    # ---- daily loss: trade journal DB (closed live trades only) ----
    db_path = settings.get("TRADE_JOURNAL_DB") or os.environ.get("TRADE_JOURNAL_DB")
    if db_path:
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades "
                "WHERE is_backtest = 0 AND status = 'closed' "
                "AND DATE(timestamp) = DATE('now')"
            )
            row = cur.fetchone()
            conn.close()
            sum_pnl = float(row[0] or 0.0)
            # Positive PnL → 0 loss. Negative PnL → abs value as USD loss.
            s["CURRENT_DAILY_LOSS_USD"] = str(abs(min(0.0, sum_pnl)))
        except Exception as exc:
            logger.warning("inject_runtime_counters: DB error — %s", exc)

    return s


def inject_per_strategy_counters(
    settings: dict,
    strategy_name: str,
    db_path: str | None = None,
) -> dict:
    """Return a copy of *settings* with per-strategy risk counters injected.

    Keys injected:
      STRATEGY_OPEN_POSITIONS  — count of open, non-backtest trades for
                                  *strategy_name* today.
      STRATEGY_DAILY_PNL       — sum of today's closed, non-backtest pnl
                                  for *strategy_name* (may be negative).

    Both counters default to "0" / "0.0" when the DB has no
    ``strategy_name`` column (graceful forward-compat), the DB is absent,
    or any error occurs.  Missing counters never block order placement.
    """
    s = dict(settings)

    if not strategy_name:
        return s

    _db = db_path or settings.get("TRADE_JOURNAL_DB") or os.environ.get("TRADE_JOURNAL_DB")
    if not _db:
        return s

    try:
        conn = sqlite3.connect(_db)
        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM trades "
                    "WHERE strategy_name = ? AND status = 'open' AND is_backtest = 0",
                    (strategy_name,),
                )
                s["STRATEGY_OPEN_POSITIONS"] = str(int(cur.fetchone()[0] or 0))
            except sqlite3.OperationalError:
                s["STRATEGY_OPEN_POSITIONS"] = "0"

            try:
                cur.execute(
                    "SELECT COALESCE(SUM(pnl), 0) FROM trades "
                    "WHERE strategy_name = ? AND is_backtest = 0 AND status = 'closed' "
                    "AND DATE(timestamp) = DATE('now')",
                    (strategy_name,),
                )
                s["STRATEGY_DAILY_PNL"] = str(float(cur.fetchone()[0] or 0.0))
            except sqlite3.OperationalError:
                s["STRATEGY_DAILY_PNL"] = "0.0"
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("inject_per_strategy_counters: DB error — %s", exc)

    return s
