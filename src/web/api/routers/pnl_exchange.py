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
from typing import Any

from fastapi import APIRouter, Query

from src.runtime.exchange_fills_store import (
    aggregate_by_symbol,
    aggregate_summary,
    fifo_pnl_by_symbol,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

DEFAULT_DAYS = 7
MAX_DAYS = 90


@router.get("/pnl/exchange")
def get_exchange_pnl(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
) -> dict[str, Any]:
    """Per-symbol fee + flow aggregates plus FIFO realised/unrealised P&L."""
    summary = aggregate_summary(days)
    by_symbol = aggregate_by_symbol(days)
    fifo = fifo_pnl_by_symbol(days)

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

    return {"summary": summary, "by_symbol": by_symbol}
