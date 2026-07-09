"""`/api/bot/exit-ladder/soak` — Tier-1 read surface for the ExitPlan soak (P3).

Surfaces the exit-ladder shadow-soak log (`runtime_logs/exit_ladder_soak.jsonl`)
so the dashboard can show, per executed order, the **laddered exit that would be
used** (the materialized ExitPlan, sized to the order's real qty) next to the
**single SL/TP target actually placed** — the evidence we watch before
graduating the ladder to the real exit (the backtest-gated P4).

Read-only, newest-first, with optional `venue` (`api`/`prop`), `account_id`, and
`differing` filters plus a small aggregate `summary`. `present:false` until the
first live opening order writes a row. Nothing here changes an exit.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from src.runtime.exit_ladder_soak import read_soak_records

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/exit-ladder/soak")
def exit_ladder_soak(
    limit: int = Query(100, ge=1, le=500),
    venue: str | None = Query(None, description="filter: api | prop"),
    account_id: str | None = Query(None),
    differing: bool = Query(False, description="only rows where the ladder differs from the single target"),
) -> dict:
    """Newest-first tail of the exit-ladder soak + per-venue summary."""
    return read_soak_records(
        limit=limit, venue=venue, account_id=account_id, only_differing=differing,
    )
