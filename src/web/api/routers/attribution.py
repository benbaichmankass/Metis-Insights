"""S11 (M11) — dashboard transparency: net positions + strategy attribution.

Two Tier-1 read-only endpoints consumed by the Streamlit dashboard:

  GET /api/bot/positions/net
      Returns the current signed net qty per symbol aggregated across all
      live accounts. Reads from ``net_positions_by_symbol()`` which queries
      the trade journal for open, non-backtest rows.

  GET /api/bot/strategy/attribution
      Returns per-strategy lifetime trade statistics (closed-trade aggregate:
      trade count, win/loss split, win rate, cumulative PnL) plus a count of
      currently-open trades per strategy. Reads ``trade_journal.db::trades``.

Both endpoints are unauthenticated GET — same tier policy as
``/api/bot/positions`` and ``/api/bot/strategies``. See
``docs/api-tier-policy.md`` Tier 1.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.runtime.positions import net_positions_by_symbol
from src.utils.paths import trade_journal_db_path
from src.web.api._clean_trades import (
    exclude_reconciler_predicate,
    exclude_reset_flat_predicate,
    exclude_superseded_predicate,
    not_paper_predicate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["attribution"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(trade_journal_db_path())


# ---------------------------------------------------------------------------
# GET /api/bot/positions/net
# ---------------------------------------------------------------------------


@router.get("/positions/net")
def get_net_positions(
    db_path: Optional[str] = Query(
        default=None,
        description="Override path to trade_journal.db (testing / staging only).",
        include_in_schema=False,
    ),
) -> Dict[str, Any]:
    """Current signed net qty per symbol across all live accounts.

    Reads ``trade_journal.db`` for open, non-backtest rows. Symbols with
    a net qty of 0 are excluded from the response.

    Returns
    -------
    ``{positions: [{symbol, net_qty}], count: int}``
    """
    resolved_path = db_path or str(_DB_PATH)
    try:
        raw = net_positions_by_symbol(db_path=resolved_path)
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort read; missing/corrupt DB must never 5xx the endpoint
        logger.warning("get_net_positions: read failed: %s", exc)
        raw = {}

    positions = [
        {"symbol": sym, "net_qty": round(qty, 8)}
        for sym, qty in sorted(raw.items())
        if qty != 0.0
    ]
    return {"positions": positions, "count": len(positions)}


# ---------------------------------------------------------------------------
# GET /api/bot/strategy/attribution
# ---------------------------------------------------------------------------


def _query_attribution(db_path: Path) -> List[Dict[str, Any]]:
    """Aggregate closed-trade stats and open-trade counts per strategy."""
    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Real-money only (account_class authoritative, is_demo fallback;
            # excludes paper AND prop) — the canonical filter the rest of the
            # API uses. attribution previously applied NO paper filter, so paper
            # /prop trades leaked into the "real" lifetime per-strategy stats
            # ("real and paper never blended" contract). And win/loss/total are
            # restricted to resolved trades (pnl IS NOT NULL) so the win-rate
            # denominator matches /performance — a reconciler-incomplete NULL-pnl
            # row was previously counted as a loss, deflating the rate.
            # Canonical predicates (src.web.api._clean_trades). ``_excl`` drops
            # reconciler ``orphan_adopt`` artifacts from the per-strategy stats.
            _not_paper = not_paper_predicate("")
            _excl = (
                exclude_reconciler_predicate("")
                + exclude_superseded_predicate("")
                + exclude_reset_flat_predicate("")
            )
            closed_rows = conn.execute(
                f"""
                SELECT
                    COALESCE(strategy_name, 'unknown') AS strategy,
                    COUNT(*) AS closed_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS winning,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS losing,
                    SUM(pnl) AS total_pnl
                FROM trades
                WHERE status = 'closed'
                  AND COALESCE(is_backtest, 0) = 0
                  AND pnl IS NOT NULL
                  {_not_paper}
                  {_excl}
                GROUP BY strategy
                ORDER BY total_pnl DESC
                """
            ).fetchall()

            open_rows = conn.execute(
                f"""
                SELECT
                    COALESCE(strategy_name, 'unknown') AS strategy,
                    COUNT(*) AS open_trades
                FROM trades
                WHERE status = 'open'
                  AND COALESCE(is_backtest, 0) = 0
                  {_not_paper}
                  {_excl}
                GROUP BY strategy
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:  # allow-silent: best-effort read; DB errors must never 5xx the attribution endpoint
        logger.warning("_query_attribution: db error: %s", exc)
        return []

    open_by_strategy: dict[str, int] = {
        row["strategy"]: row["open_trades"] for row in open_rows
    }

    out: List[Dict[str, Any]] = []
    for row in closed_rows:
        strategy = row["strategy"]
        closed = int(row["closed_trades"])
        winning = int(row["winning"])
        win_rate = round(winning / closed * 100, 1) if closed > 0 else 0.0
        out.append(
            {
                "strategy": strategy,
                "open_trades": open_by_strategy.get(strategy, 0),
                "closed_trades": closed,
                "winning_trades": winning,
                "losing_trades": int(row["losing"]),
                "win_rate": win_rate,
                "total_pnl": round(float(row["total_pnl"] or 0.0), 4),
            }
        )

    # Append strategies that have open trades but no closed trades yet
    for strategy, open_count in open_by_strategy.items():
        if not any(r["strategy"] == strategy for r in out):
            out.append(
                {
                    "strategy": strategy,
                    "open_trades": open_count,
                    "closed_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate": 0.0,
                    "total_pnl": 0.0,
                }
            )

    return out


@router.get("/strategy/attribution")
def get_strategy_attribution(
    db_path: Optional[str] = Query(
        default=None,
        description="Override path to trade_journal.db (testing / staging only).",
        include_in_schema=False,
    ),
) -> Dict[str, Any]:
    """Per-strategy lifetime trade statistics.

    Aggregates closed trades from ``trade_journal.db`` (non-backtest only)
    by ``strategy_name``. Includes a count of currently-open trades so the
    dashboard can show live exposure per strategy alongside historical stats.

    Returns
    -------
    ``{strategies: [{strategy, open_trades, closed_trades, winning_trades,
    losing_trades, win_rate, total_pnl}], generated_at: ISO-8601}``
    """
    resolved_path = Path(db_path) if db_path else _DB_PATH
    strategies = _query_attribution(resolved_path)
    return {
        "strategies": strategies,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
