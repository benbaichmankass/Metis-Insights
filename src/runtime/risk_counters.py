"""
Live risk counter injection for the pipeline.

Fetches CURRENT_OPEN_POSITIONS and CURRENT_DAILY_LOSS_USD at order time
so that safe_place_order's hard risk guards have real values to check.

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
