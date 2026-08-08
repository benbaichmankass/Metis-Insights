"""GET /api/bot/pnl/exchange — exchange-truth P&L attribution surface.

S-067 follow-up #6. Reads from the exchange-fills sqlite store
(``runtime_state/exchange_fills.sqlite``) populated by
``scripts/pull_exchange_fills.py``. Insulates performance reads from
local schema/state bugs in ``trade_journal.db``.

Phase-1 (PR #652) shipped fee + flow aggregates only. **Phase-2
(this PR — S-067 follow-up C)** adds true P&L attribution via FIFO
buy/sell lot pairing over the fills stream. The additions are
strictly additive — existing dashboard readers see the same fields
they did before, plus the new FIFO ones.

Wire-shape:

    {
      "summary": {
        "fill_count": 12,
        "total_fees": 0.4321,
        "symbol_count": 2,
        "window_days": 7,
        "total_realized_pnl": 12.34,           # NEW (Phase-2)
        "total_unrealized_pnl": -1.50          # NEW (Phase-2)
      },
      "by_symbol": [
        {"symbol": "BTC/USDT:USDT",
         "fill_count": 8,
         "gross_qty": 0.024,
         "gross_notional": 1480.5,
         "total_fees": 0.32,
         "first_exec_time": "2026-05-04T10:00:00+00:00",
         "last_exec_time": "2026-05-08T16:00:00+00:00",
         "realized_pnl": 8.10,                 # NEW (Phase-2)
         "unrealized_pnl": 0.50,               # NEW (Phase-2)
         "open_qty_signed": 0.001,             # NEW (Phase-2)
         "last_price": 60200.0},               # NEW (Phase-2)
        ...
      ]
    }

P&L semantics — see ``src/runtime/exchange_fills_store.py::_fifo_match``
for the canonical engine. Realised PnL = matched buy/sell lot PnL
minus all fees in the window. Unrealised PnL marks remaining open
lots against the most recent fill price for the symbol (a
defensible mark-price proxy; a real mark feed is out of scope).

Tier 1 — public read; same auth surface as ``/api/bot/stats`` etc.
``[]`` / zero aggregates when the fills store doesn't exist (e.g.
the puller has never run yet).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query

from src.runtime.exchange_fills_store import (
    aggregate_by_symbol,
    aggregate_summary,
    fifo_pnl_by_symbol,
    list_fills,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

DEFAULT_DAYS = 7
MAX_DAYS = 90


@router.get("/pnl/exchange")
def get_exchange_pnl(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    account_id: Optional[str] = Query(
        None,
        description=(
            "Scope every aggregate to one account (e.g. 'bybit_2'). Omitted = "
            "ALL accounts pooled, which mixes real-money and paper books — that "
            "pooled figure is not attributable to any one book and must not be "
            "quoted as a real-money result."
        ),
    ),
) -> dict[str, Any]:
    """Per-symbol fee + flow aggregates plus FIFO realised/unrealised P&L.

    ALWAYS STATE THE POPULATION: the response echoes ``account_id`` (null when
    pooled) so a consumer can never render the number without its scope.
    """
    summary = aggregate_summary(days, account_id=account_id)
    by_symbol = aggregate_by_symbol(days, account_id=account_id)
    fifo = fifo_pnl_by_symbol(days, account_id=account_id)

    # Merge FIFO fields into each by_symbol row (additive — existing
    # callers see the same Phase-1 keys).
    fifo_by_sym = {row["symbol"]: row for row in fifo}
    for row in by_symbol:
        f = fifo_by_sym.get(row["symbol"])
        row["realized_pnl"] = f["realized_pnl"] if f else 0.0
        row["unrealized_pnl"] = f["unrealized_pnl"] if f else 0.0
        row["open_qty_signed"] = f["open_qty_signed"] if f else 0.0
        row["last_price"] = f["last_price"] if f else 0.0

    summary["total_realized_pnl"] = sum(r["realized_pnl"] for r in fifo)
    summary["total_unrealized_pnl"] = sum(r["unrealized_pnl"] for r in fifo)
    # The scope travels with the number (null = all accounts pooled).
    summary["account_id"] = account_id

    return {"summary": summary, "by_symbol": by_symbol}


@router.get("/pnl/exchange/fills")
def get_exchange_fills(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    account_id: Optional[str] = Query(
        None,
        description=(
            "Scope to one account (e.g. 'bybit_1'). Omitted = ALL accounts, "
            "which mixes real-money and paper books."
        ),
    ),
    symbol: Optional[str] = Query(
        None,
        description=(
            "Exact stored symbol in VENUE form (e.g. 'AVAX/USDT:USDT', not "
            "'AVAXUSDT'). Bound, never interpolated."
        ),
    ),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """The individual exchange fill ROWS, newest-first — exchange truth, unaggregated.

    Every sibling on this router returns a SUM. Sums are the right default and
    the wrong tool for "WHICH trade does this discrepancy belong to": when
    several strategies trade one symbol on one account, a per-symbol aggregate
    cannot separate them. Measured 2026-08-07: the journal recorded -$9,669.41
    across three AVAXUSDT/bybit_1 trades where exchange truth was -$5,403.09, a
    $4,266.32 gap that could not be attributed from any existing surface
    (BL-20260807-EXCHANGE-TRUTH-PER-STRATEGY-UNREACHABLE). That population was
    SIX fills.

    This returns rows and attributes NOTHING. That restraint is deliberate: a
    broker SL/TP exit fills under an order id the bot never sees, so an
    ``order_id -> strategy`` map covers entries only and a per-strategy
    aggregate built on it would bucket every exit as ``unattributed`` and still
    print a confident split. Rows are the substrate an attributor gets checked
    AGAINST, so they come first.

    ALWAYS STATE THE POPULATION: the response echoes every filter applied plus
    ``truncated``, so a caller can tell a complete population from a capped one
    rather than reading a short list as the whole story. Tier 1, read-only.
    """
    rows = list_fills(
        days, account_id=account_id, symbol=symbol, limit=limit,
    )
    return {
        "fills": rows,
        "count": len(rows),
        "days": days,
        "account_id": account_id,
        "symbol": symbol,
        "limit": limit,
        # A page that exactly hit the cap may be hiding older rows. Say so
        # rather than letting the caller assume completeness.
        "truncated": len(rows) >= limit,
    }
