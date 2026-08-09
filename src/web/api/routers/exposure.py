"""/api/bot/exposure/soak — observe-only read of the gross-exposure soak log.

Thin wrapper over ``src.runtime.exposure_soak.read_soak_records``; mirrors the
pairs / allocator / exit-ladder soak routers. Tier 1, read-only.

This route is shipped **in the same PR as the writer**, deliberately. #8665 added
`report()["exposure"]` with no reader anywhere and the number it existed to
reveal was unreadable until #8678 — a signal written and never read is worse
than a missing one, because reviewers see the field and assume something acts on
it. A soak log with no read surface repeats that exactly.

Read ``summary.by_account[*].max_multiple`` beside ``measured_n`` and ``rows``:
the max is the statistic a ceiling has to clear (§ 6 requires the ceiling ABOVE
normal operation), and a max over two samples is not the same claim as a max
over two hundred. ``summary.venue_sessions`` says how much of the window was
`rth` vs `closed` — a soak that is mostly `closed` has not observed normal
operation at all.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from src.runtime.exposure_soak import read_soak_records

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/exposure/soak")
def exposure_soak(
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None, description="filter to one account id"),
    measured_only: bool = Query(
        False,
        description=(
            "drop rows where the exposure could not be measured. NOTE the "
            "summary is always computed over ALL rows, so measured_n/rows stay "
            "an honest denominator regardless of this filter."
        ),
    ),
) -> dict:
    return read_soak_records(
        limit=limit, account_id=account_id, measured_only=measured_only
    )
