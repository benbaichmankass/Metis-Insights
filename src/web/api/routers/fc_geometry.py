"""`/api/bot/fc-geometry/soak` — Tier-1 read surface for the M19 D1 soak.

Surfaces the fc-geometry shadow-soak log (`runtime_logs/fc_geometry_soak.jsonl`)
so the dashboard / a review session can watch, per opening order, the SL/TP
**actually placed** next to the decision-time **quantile-forecast snapshot**
(the `fc_*` row `forecast_live` served) — the raw material the trainer-side
resolver scores into placed-vs-fc-scaled outcomes (with explicit censoring).

Read-only, newest-first, with optional `symbol`, `account_id`, and `fc_only`
filters plus a small aggregate `summary` (accrual + fc-coverage split).
`present:false` until the first live opening order writes a row. Nothing here
changes an exit.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from src.runtime.fc_geometry_soak import read_soak_records

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/fc-geometry/soak")
def fc_geometry_soak(
    limit: int = Query(100, ge=1, le=500),
    symbol: str | None = Query(None),
    account_id: str | None = Query(None),
    fc_only: bool = Query(False, description="only rows with a live fc snapshot present"),
) -> dict:
    """Newest-first tail of the fc-geometry soak + coverage summary."""
    return read_soak_records(
        limit=limit, symbol=symbol, account_id=account_id, fc_only=fc_only,
    )
