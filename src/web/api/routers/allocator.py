"""`/api/bot/allocator/soak` — Tier-1 read surface for the M18 allocator soak.

Surfaces the portfolio-capital-allocator shadow-soak log
(`runtime_logs/allocator_soak.jsonl`) so the dashboard can show, per tick where a
genuine choice exists (≥ 2 actionable candidates), what a capital allocator
**would** pick (the top-ranked candidate of the full opportunity set) next to
what the system **actually** routed (the aggregator's winner), and the **regret**
between them — the evidence we watch before graduating the allocator to actually
select the subset (the backtest-gated M18 P2+).

Read-only, newest-first, with optional `symbol` and `regret` (disagreement-only)
filters plus a small aggregate `summary` (disagreement % + mean regret).
`present:false` until the first multi-candidate tick writes a row. Nothing here
changes routing.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from src.runtime.allocator_soak import read_soak_records

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/allocator/soak")
def allocator_soak(
    limit: int = Query(100, ge=1, le=500),
    symbol: str | None = Query(None),
    regret: bool = Query(False, description="only rows where the allocator would pick a different candidate"),
) -> dict:
    """Newest-first tail of the allocator soak + disagreement/regret summary."""
    return read_soak_records(limit=limit, symbol=symbol, only_regret=regret)
