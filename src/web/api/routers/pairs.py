"""/api/bot/pairs/soak — observe-only read of the market-neutral pairs sleeve soak
log (M22 D2). Thin wrapper over src.runtime.pairs_soak.read_soak_records; mirrors
the allocator/exit-ladder soak routers. Tier 1, read-only."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from src.runtime.pairs_soak import read_soak_records

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/pairs/soak")
def pairs_soak(
    limit: int = Query(100, ge=1, le=1000),
    pair: Optional[str] = Query(None, description="filter to one pair label, e.g. SOLUSDT/BTCUSDT"),
    event: Optional[str] = Query(None, description="filter to one event kind"),
) -> dict:
    return read_soak_records(limit=limit, pair=pair, event=event)
