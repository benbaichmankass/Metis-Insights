"""GET /api/bot/pnl/exchange — exchange-truth P&L attribution surface.

S-067 follow-up #6. Reads from the exchange-fills sqlite store
(``runtime_state/exchange_fills.sqlite``) populated by
``scripts/pull_exchange_fills.py``. Insulates performance reads from
local schema/state bugs in ``trade_journal.db``.

Phase-1 surfaces fee + flow aggregates (see
``src/runtime/exchange_fills_store.py::aggregate_by_symbol`` for
why true P&L attribution is deferred to phase-2). The shape is
deliberately additive — phase-2 can add ``realized_pnl`` /
``unrealized_pnl`` fields without breaking existing dashboard
readers.

Wire-shape:

    {
      "summary": {"fill_count": 12, "total_fees": 0.4321,
                  "symbol_count": 2, "window_days": 7},
      "by_symbol": [
        {"symbol": "BTC/USDT:USDT", "fill_count": 8,
         "gross_qty": 0.024, "gross_notional": 1480.5,
         "total_fees": 0.32, "first_exec_time": "2026-05-04T10:00:00+00:00",
         "last_exec_time": "2026-05-08T16:00:00+00:00"},
        ...
      ]
    }

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
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

DEFAULT_DAYS = 7
MAX_DAYS = 90


@router.get("/pnl/exchange")
async def get_exchange_pnl(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
) -> dict[str, Any]:
    """Per-symbol fee + flow aggregates over the last ``days``."""
    return {
        "summary": aggregate_summary(days),
        "by_symbol": aggregate_by_symbol(days),
    }
