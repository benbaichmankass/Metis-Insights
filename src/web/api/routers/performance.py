"""GET /api/bot/performance — windowed aggregate performance stats.

Tier-1 read endpoint backing the Android Performance tab (and any other
consumer that wants headline trade analytics over a selectable window).

Why this exists: the consumers previously pulled ``/api/bot/trades/closed``
(capped at 200 rows) and aggregated client-side. With more than 200 closed
trades that made the headline "Trades" count freeze at 200 and skewed every
derived metric (win rate, expectancy, equity curve) to the most recent 200
fills only. This endpoint computes the aggregates in SQL over the **full**
trade history within the requested window — no row cap — so the numbers are
correct regardless of how many trades the bot has taken.

Window (``?window=``):
  - ``24h`` — trades closed in the last 24 hours.
  - ``7d``  — last 7 days.
  - ``30d`` — last 30 days.
  - ``all`` — all closed trades (default).

The close-time basis is the canonical ``trades.closed_at`` column (P1-B),
falling back to ``COALESCE(t.closed_at, op.updated_at, t.timestamp)`` for rows predating
that column / its backfill — i.e. ``COALESCE(t.closed_at, op.updated_at,
t.timestamp)``. So ``window=24h`` is a true rolling-24h window keyed on real
close time. Backtest + paper rows are excluded from the top-level figures so
they reflect live money, exactly like ``/api/bot/stats``.

Wire shape (camelCase):

    {
      "window": "7d",
      "since": "2026-05-22T09:00:00+00:00" | null,
      "totalTrades": 412,
      "wins": 250,
      "losses": 150,
      "winRate": 60.7,                  # percent, winners / closed × 100
      "totalPnl": 1234.56,
      "expectancy": 3.0,                # totalPnl / totalTrades
      "perStrategy": [
        {"name": "vwap", "trades": 120, "wins": 70, "winRate": 58.3,
         "totalPnl": 540.2, "expectancy": 4.5}
      ],
      "equity": [{"t": "2026-05-22T09:01:00+00:00", "cum": 12.5}]  # oldest→newest
    }

Best-effort: returns a zeroed envelope on a missing/locked DB so the consumer
keeps the tab usable. Tier 1 — no auth, no secrets in the response.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.utils.paths import trade_journal_db_path
from src.web.api._asset_class import CLASS_ORDER, asset_class_for_symbol
from src.runtime.local_pnl import contract_value_usd_for
from src.web.api._clean_trades import (
    exclude_reconciler_predicate,
    exclude_reset_flat_predicate,
    exclude_superseded_predicate,
    not_paper_predicate,
    paper_predicate,
    r_multiple,
)
from src.web.api._closed_at import close_time_sql

logger = logging.getLogger(__name__)

# Canonical close-time basis, epoch-ms-aware. The reconciler-filled close path
# writes ``trades.closed_at`` as a raw epoch-milliseconds string; an unguarded
# ``datetime(closed_at)`` returns NULL in SQLite and silently drops those rows
# from the window (the "/performance shows 0 closed trades" bug). This mirrors
# the basis ``/api/bot/trades/closed`` already uses — see src/web/api/_closed_at.py.
_CLOSE_TIME_SQL = close_time_sql("t.closed_at", "op.updated_at", "t.timestamp")

router = APIRouter(prefix="/api/bot", tags=["bot"])

_DB_PATH = Path(trade_journal_db_path())

# window token → lookback timedelta. ``all`` maps to None (no since filter).
_WINDOWS: Dict[str, Optional[timedelta]] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}

# Cap on equity-curve points returned. The aggregates are uncapped, but the
# point-by-point equity series is only for sparkline rendering — a few hundred
# points is plenty and keeps the mobile payload small. When the window holds
# more closed trades than this we down-sample evenly (keep newest exact).
_MAX_EQUITY_POINTS = 500


def _window_since(window: str) -> Optional[str]:
    """ISO-8601 UTC cutoff for *window*, or None for the all-time window."""
    delta = _WINDOWS.get(window)
    if delta is None:
        return None
    return (datetime.now(timezone.utc) - delta).isoformat()


def _empty(window: str, since: Optional[str], error: bool = False) -> Dict[str, Any]:
    """Zeroed aggregate. ``error`` distinguishes a genuine *no-trades* window
    (``error=False``) from a DB read failure (``error=True``) so a consumer
    never renders a fabricated ``$0.00`` over an outage — it can show "—"
    instead. ``profitFactor`` / ``maxDrawdown`` are null (not 0) when there is
    nothing to compute them from."""
    return {
        "window": window,
        "since": since,
        "error": error,
        "totalTrades": 0,
        "wins": 0,
        "losses": 0,
        "winRate": 0.0,
        "totalPnl": 0.0,
        "expectancy": 0.0,
        "totalR": None,
        "expectancyR": None,
        "rTradeCount": 0,
        "rCoverage": 0.0,
        "profitFactor": None,
        "maxDrawdown": None,
        "perStrategy": [],
        "perAssetClass": [],
        "perSymbol": [],
        "equity": [],
    }


# "Paper" / "not paper" SQL predicates + reconciler-artifact exclusion, from
# the canonical src.web.api._clean_trades helper (single source of truth — see
# that module's docstring). Joined ``trades`` alias is ``t``.
_PAPER_PREDICATE = paper_predicate("t.")
_NOT_PAPER_PREDICATE = not_paper_predicate("t.")
# Drop reconciler ``orphan_adopt`` rows from the strategy-performance aggregates
# — they are a recovery/bookkeeping state, not a strategy's trade.
_EXCLUDE_RECONCILER = exclude_reconciler_predicate("t.")
# Drop superseded phantom orphan-flap duplicates (void-flagged by the
# historical reconciliation pass, orphan-flap hardening #5) from the aggregates.
_EXCLUDE_SUPERSEDED = exclude_superseded_predicate("t.") + exclude_reset_flat_predicate("t.")


def _query(
    db_path: Path,
    since: Optional[str],
    demo: bool = False,
    account_ids: Optional[List[str]] = None,
) -> List[sqlite3.Row]:
    """Closed (non-backtest) trades within *since*, oldest→newest.

    ``demo=False`` (default) → real-money rows only.
    ``demo=True``            → paper-account rows only.
    ``account_ids`` (optional) → additionally restrict to those account ids
    (used for the ``paperPortfolio`` sub-block — the live-portfolio-mirror
    paper books, S-PAPER-PORTFOLIO 2026-07-16). Empty/None → no restriction.

    Rows with ``pnl IS NULL`` are excluded — the reconciler fallback path
    in ``order_monitor.py`` closes trades with a NULL pnl when the broker
    close-pnl lookup fails (``exit_reason='reconciler_incomplete'``).
    Including them in the aggregates either as zeros or as wins/losses
    distorts win-rate / expectancy / equity curve in misleading ways
    (the "0-pnl closed trade" complaint, 2026-06-04).

    Oldest-first ordering lets the caller build the cumulative equity curve in a
    single pass without re-sorting.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # De-dup the order_packages join to exactly one row per trade. A raw
        # `LEFT JOIN order_packages ON linked_trade_id = t.id` FANS OUT when a
        # trade has >1 linked order package (entry + protective re-arm, retries,
        # etc.) — that trade's pnl/win would then be counted N times, inflating
        # totalTrades / winRate / totalPnl. Pre-aggregating to one updated_at
        # per linked_trade_id keeps the join 1:1 so each trade contributes
        # exactly once — the canonical "one row per closed trade" basis the rest
        # of the API uses, and the reason /performance could disagree with
        # /stats on real-money totals (single source of truth).
        # R-multiple inputs (entry/stop/size) are OPTIONAL: select them only when
        # the trades table actually has the columns, so a minimal/legacy schema
        # makes R degrade to None (rCoverage 0) instead of erroring the endpoint.
        avail = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        r_select = "".join(
            f"\n                   t.{col} AS {alias},"
            for col, alias in (
                ("entry_price", "entry_price"),
                ("stop_loss", "stop_loss"),
                ("position_size", "qty"),
            )
            if col in avail
        )
        sql = f"""
            SELECT t.strategy_name,
                   t.symbol AS symbol,
                   t.pnl AS pnl,{r_select}
                   {_CLOSE_TIME_SQL} AS closed_at
            FROM trades t
            LEFT JOIN (
                SELECT linked_trade_id, MIN(updated_at) AS updated_at
                FROM order_packages
                WHERE linked_trade_id IS NOT NULL
                GROUP BY linked_trade_id
            ) op ON op.linked_trade_id = t.id
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
              AND t.pnl IS NOT NULL
        """
        sql += _PAPER_PREDICATE if demo else _NOT_PAPER_PREDICATE
        sql += _EXCLUDE_RECONCILER
        sql += _EXCLUDE_SUPERSEDED
        params: List[Any] = []
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            sql += f" AND t.account_id IN ({placeholders})"
            params.extend(account_ids)
        if since:
            sql += f" AND {_CLOSE_TIME_SQL} >= datetime(?)"
            params.append(since)
        sql += f" ORDER BY {_CLOSE_TIME_SQL} ASC"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _downsample(points: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """Evenly thin *points* to at most *cap*, always keeping the last point."""
    n = len(points)
    if n <= cap:
        return points
    step = n / cap
    out = [points[int(i * step)] for i in range(cap)]
    if out[-1] is not points[-1]:
        out[-1] = points[-1]
    return out


def _rget(row: sqlite3.Row, key: str) -> Any:
    """Row value, or ``None`` when the column wasn't selected (the optional
    R-multiple inputs degrade gracefully on a minimal/legacy trades table)."""
    return row[key] if key in row.keys() else None


def _aggregate(rows: List[sqlite3.Row], window: str, since: Optional[str]) -> Dict[str, Any]:
    total = len(rows)
    if total == 0:
        return _empty(window, since)

    wins = 0
    gross_profit = 0.0   # sum of winning-trade pnl (for profit factor)
    gross_loss = 0.0     # abs sum of losing-trade pnl
    total_pnl = 0.0
    total_r = 0.0          # sum of per-trade R over R-measurable trades only
    r_count = 0            # # trades with a computable R (entry+stop+size known)
    per: Dict[str, Dict[str, float]] = {}
    per_class: Dict[str, Dict[str, float]] = {}
    per_symbol: Dict[str, Dict[str, Any]] = {}
    equity: List[Dict[str, Any]] = []
    cum = 0.0
    peak = 0.0           # running equity peak for max-drawdown
    max_dd = 0.0         # most negative (peak - trough) seen, <= 0
    for r in rows:
        pnl = float(r["pnl"] or 0.0)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += -pnl
        # R-multiple: pnl normalised by the trade's own risk so a micro crypto
        # trade and a futures contract compare on one axis. None when risk is
        # unknown (missing stop/size); then it counts in NEITHER R numerator nor
        # denominator — never a raw-pnl fallback (the blending bug).
        rr = r_multiple(
            r["pnl"], _rget(r, "entry_price"), _rget(r, "stop_loss"),
            _rget(r, "qty"), contract_value_usd_for(r["symbol"]),
        )
        if rr is not None:
            total_r += rr
            r_count += 1
        name = r["strategy_name"] or "(unknown)"
        bucket = per.setdefault(
            name, {"trades": 0.0, "wins": 0.0, "pnl": 0.0, "r": 0.0, "rc": 0.0}
        )
        bucket["trades"] += 1
        if pnl > 0:
            bucket["wins"] += 1
        bucket["pnl"] += pnl
        if rr is not None:
            bucket["r"] += rr
            bucket["rc"] += 1
        # asset-class breakdown (crypto / index / commodity / equity / fx)
        cls = asset_class_for_symbol(r["symbol"])
        cbucket = per_class.setdefault(
            cls, {"trades": 0.0, "wins": 0.0, "pnl": 0.0, "r": 0.0, "rc": 0.0}
        )
        cbucket["trades"] += 1
        if pnl > 0:
            cbucket["wins"] += 1
        cbucket["pnl"] += pnl
        if rr is not None:
            cbucket["r"] += rr
            cbucket["rc"] += 1
        # per-symbol breakdown (drives the dashboard's symbol-stacked asset bar).
        # Computed in this SAME loop over the SAME windowed rows as the class
        # total, so a class that reports a total can never render an empty
        # per-symbol split — the drift that left the client-side `/trades/closed`
        # aggregation blank (a recently-closed trade with a null closedAt that
        # `/performance` still counts).
        sym = str(r["symbol"] or "unknown")
        sbucket = per_symbol.setdefault(
            sym, {"assetClass": cls, "trades": 0.0, "wins": 0.0, "pnl": 0.0}
        )
        sbucket["trades"] += 1
        if pnl > 0:
            sbucket["wins"] += 1
        sbucket["pnl"] += pnl
        cum += pnl
        if cum > peak:
            peak = cum
        drawdown = cum - peak  # <= 0
        if drawdown < max_dd:
            max_dd = drawdown
        equity.append({"t": r["closed_at"], "cum": round(cum, 4)})

    losses = total - wins
    per_strategy = [
        {
            "name": name,
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            "expectancy": round(b["pnl"] / b["trades"], 4) if b["trades"] else 0.0,
            # R-normalised (cross-instrument-comparable). None when no trade in
            # the bucket had a measurable risk; rTradeCount says how many did.
            "totalR": round(b["r"], 4) if b["rc"] else None,
            "expectancyR": round(b["r"] / b["rc"], 4) if b["rc"] else None,
            "rTradeCount": int(b["rc"]),
        }
        for name, b in per.items()
    ]
    per_strategy.sort(key=lambda s: s["totalPnl"], reverse=True)

    per_asset_class = [
        {
            "assetClass": cls,
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            "expectancy": round(b["pnl"] / b["trades"], 4) if b["trades"] else 0.0,
            "totalR": round(b["r"], 4) if b["rc"] else None,
            "expectancyR": round(b["r"] / b["rc"], 4) if b["rc"] else None,
            "rTradeCount": int(b["rc"]),
        }
        for cls, b in per_class.items()
    ]
    # stable, business-readable ordering (crypto, index, commodity, equity, fx…)
    per_asset_class.sort(key=lambda c: (CLASS_ORDER.index(c["assetClass"])
                                        if c["assetClass"] in CLASS_ORDER else 99))

    # Per-symbol breakdown — each symbol tagged with its asset class so the
    # consumer can subdivide an asset-class bar by its constituent symbols.
    per_symbol_list = [
        {
            "symbol": sym,
            "assetClass": b["assetClass"],
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            "expectancy": round(b["pnl"] / b["trades"], 4) if b["trades"] else 0.0,
        }
        for sym, b in per_symbol.items()
    ]
    # biggest movers first (by |P&L|) — stable palette in the consumer.
    per_symbol_list.sort(key=lambda s: abs(s["totalPnl"]), reverse=True)

    # Profit factor: gross profit / gross loss. None when there are no losing
    # trades (undefined / infinite) or no trades — never a fabricated 0.
    profit_factor: Optional[float] = (
        round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    )
    # Max drawdown is <= 0; None when there were no trades.
    max_drawdown: Optional[float] = round(max_dd, 4) if total else None

    return {
        "window": window,
        "since": since,
        "error": False,
        "totalTrades": total,
        "wins": wins,
        "losses": losses,
        "winRate": round(wins / total * 100.0, 1) if total else 0.0,
        "totalPnl": round(total_pnl, 4),
        "expectancy": round(total_pnl / total, 4) if total else 0.0,
        # R-normalised headline — the cross-instrument-comparable axis. None when
        # NO trade in the window had a measurable risk; rTradeCount / rCoverage
        # report how much of the window is R-measured (transparency, never a
        # raw-pnl fallback). Resolves the cross-notional USD blending in totalPnl.
        "totalR": round(total_r, 4) if r_count else None,
        "expectancyR": round(total_r / r_count, 4) if r_count else None,
        "rTradeCount": r_count,
        "rCoverage": round(r_count / total, 4) if total else 0.0,
        "profitFactor": profit_factor,
        "maxDrawdown": max_drawdown,
        "perStrategy": per_strategy,
        "perAssetClass": per_asset_class,
        "perSymbol": per_symbol_list,
        "equity": _downsample(equity, _MAX_EQUITY_POINTS),
    }


def _strip_envelope(agg: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the ``window`` / ``since`` / ``error`` envelope keys from an
    aggregate so the demo/paper sub-block doesn't carry duplicate metadata
    (``error`` is an envelope-level signal, not per-sub-block)."""
    return {k: v for k, v in agg.items() if k not in ("window", "since", "error")}


def _portfolio_paper_account_ids() -> List[str]:
    """Account-ids of PAPER accounts flagged ``paper_role: portfolio``.

    S-PAPER-PORTFOLIO (2026-07-16): the live-portfolio-mirror paper books
    (``bybit_portfolio`` / ``alpaca_portfolio``). The ``paperPortfolio``
    sub-block below is computed over just these so a consumer's "Paper" view
    can scope to the real-portfolio mirror instead of the full soak roster.
    Empty list → no portfolio accounts declared (an older config); the caller
    then falls the ``paperPortfolio`` block back to the all-paper ``paper``
    block so the field is always present and never misleadingly empty.

    Best-effort + connection-free: any load error → ``[]`` (fall back).
    """
    try:
        from src.config.accounts_loader import load_accounts_dict
        accounts_yaml = Path(__file__).resolve().parents[4] / "config" / "accounts.yaml"
        accounts = load_accounts_dict(accounts_yaml)
    except Exception:  # noqa: BLE001 - best-effort; missing/garbled config → no scoping
        return []
    out: List[str] = []
    for aid, cfg in (accounts or {}).items():
        if not isinstance(cfg, dict):
            continue
        if (
            str(cfg.get("account_class") or "").lower() == "paper"
            and str(cfg.get("paper_role") or "").lower() == "portfolio"
        ):
            out.append(str(aid))
    return out


@router.get("/performance")
def get_performance(
    window: str = Query("all", max_length=8),
) -> Dict[str, Any]:
    """Aggregate trade performance for the requested *window*.

    The top-level fields (``totalTrades`` / ``wins`` / ``perStrategy`` / etc.)
    are **real-money** aggregates — this preserves the existing consumer
    contract. The 2026-06-04 reporting-cleanup additively returns a
    ``demo`` sub-block carrying the same shape computed over paper-account
    rows so a consumer can render Real and Paper as separate sections
    without a second request. A ``paper`` sub-block carries the identical
    payload under the clearer name (account_class convention, 2026-06-15);
    ``demo`` is retained as a back-compat alias for the Android app.

    Trades with ``pnl IS NULL`` are excluded from both — see ``_query`` for
    why ("0-pnl closed trade" complaint, reconciler fallback path).

    Returns a zeroed envelope (HTTP 200) on an unknown window token or a
    DB read error so the consumer's tab stays usable instead of erroring.
    """
    window = window if window in _WINDOWS else "all"
    since = _window_since(window)
    if not _DB_PATH.exists():
        env = _empty(window, since)
        empty_sub = _strip_envelope(_empty(window, since))
        env["demo"] = empty_sub
        env["paper"] = empty_sub
        env["paperPortfolio"] = empty_sub
        return env
    try:
        live_rows = _query(_DB_PATH, since, demo=False)
        live = _aggregate(live_rows, window, since)
        paper_rows = _query(_DB_PATH, since, demo=True)
        paper = _strip_envelope(_aggregate(paper_rows, window, since))
        live["demo"] = paper   # back-compat alias
        live["paper"] = paper
        # paperPortfolio (S-PAPER-PORTFOLIO 2026-07-16): the same shape computed
        # over ONLY the live-portfolio-mirror paper accounts (paper_role:
        # portfolio), so a consumer's "Paper" view can scope to the real
        # portfolio instead of the full soak roster. Falls back to the all-paper
        # block when no portfolio accounts are declared, so the field is always
        # present (never a misleadingly-empty block on an older config).
        portfolio_ids = _portfolio_paper_account_ids()
        if portfolio_ids:
            pp_rows = _query(_DB_PATH, since, demo=True, account_ids=portfolio_ids)
            live["paperPortfolio"] = _strip_envelope(_aggregate(pp_rows, window, since))
        else:
            live["paperPortfolio"] = paper
        return live
    except sqlite3.Error:  # allow-silent: logged (logger.exception) + best-effort zeroed envelope so the Performance tab stays usable on a DB read failure
        logger.exception("performance: sqlite read failed")
        env = _empty(window, since, error=True)
        empty_sub = _strip_envelope(_empty(window, since))
        env["demo"] = empty_sub
        env["paper"] = empty_sub
        env["paperPortfolio"] = empty_sub
        return env
    except Exception:  # noqa: BLE001  # allow-silent: logged (logger.exception) + best-effort zeroed envelope; never raise a 5xx for this Tier-1 read
        logger.exception("performance: unexpected error")
        env = _empty(window, since, error=True)
        empty_sub = _strip_envelope(_empty(window, since))
        env["demo"] = empty_sub
        env["paper"] = empty_sub
        env["paperPortfolio"] = empty_sub
        return env
