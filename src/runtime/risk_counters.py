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
            # Surfaced via outcomes so a flaky exchange API doesn't
            # silently disable the MAX_OPEN_POSITIONS guard.
            try:
                from src.runtime.outcomes import Level, report
                report(
                    "risk_counters",
                    "positions_fetch_failed",
                    level=Level.WARN,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            except Exception:  # noqa: BLE001
                pass  # outcomes shouldn't crash the tick

    # ---- daily loss: trade journal DB (closed live trades only) ----
    # NOTE: risk_counters intentionally engages the journal ONLY when one is
    # EXPLICITLY configured (settings or the TRADE_JOURNAL_DB env the live
    # systemd unit sets) — not the canonical-resolver default. "No journal
    # configured → leave settings unchanged" is a load-bearing contract
    # (tests: test_runtime_risk_injection / test_per_strategy_risk). This
    # file is therefore allowlisted in scripts/check_canonical_db_resolver.py.
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
            # Safety-relevant: a failed read here means
            # MAX_DAILY_LOSS_USD silently won't be enforced.
            try:
                from src.runtime.outcomes import Level, report
                report(
                    "risk_counters",
                    "daily_loss_fetch_failed",
                    level=Level.WARN,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            except Exception:  # noqa: BLE001
                pass

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

    # Explicit-config only (see the daily-loss note above + allowlist).
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

            # Overtrading throttle counters (cross-zero P2a). Feed the
            # optional MAX_TRADES_PER_STRATEGY_PER_DAY / MIN_TRADE_SPACING_MINUTES
            # guards in safe_place_order. Both default-permissive: a missing
            # counter leaves the guard skipped (never blocks a trade). The
            # spacing is computed in SQL so orders.py stays a pure numeric
            # comparison (no timestamp parsing) — same shape as the other
            # per-strategy guards. Counts open+closed (every trade pays fees).
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM trades "
                    "WHERE strategy_name = ? AND is_backtest = 0 "
                    "AND DATE(timestamp) = DATE('now')",
                    (strategy_name,),
                )
                s["STRATEGY_TRADES_TODAY"] = str(int(cur.fetchone()[0] or 0))
            except sqlite3.OperationalError:
                s["STRATEGY_TRADES_TODAY"] = "0"

            try:
                # Minutes since this strategy's most recent trade. NULL (no
                # prior trade) → counter omitted so the spacing guard is
                # skipped (the first trade is always allowed).
                cur.execute(
                    "SELECT (julianday('now') - julianday(MAX(timestamp))) * 1440.0 "
                    "FROM trades WHERE strategy_name = ? AND is_backtest = 0",
                    (strategy_name,),
                )
                _mins = cur.fetchone()[0]
                if _mins is not None:
                    s["STRATEGY_MINUTES_SINCE_LAST_TRADE"] = str(float(_mins))
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("inject_per_strategy_counters: DB error — %s", exc)
        try:
            from src.runtime.outcomes import Level, report
            report(
                "risk_counters",
                "per_strategy_fetch_failed",
                level=Level.WARN,
                reason=f"{type(exc).__name__}: {exc}",
                strategy=strategy_name,
            )
        except Exception:  # noqa: BLE001
            pass

    return s
