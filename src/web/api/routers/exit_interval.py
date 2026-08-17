"""/api/bot/exit-interval/soak — the CROSS-PROCESS exit-evaluation interval.

Thin wrapper over ``src.runtime.exit_interval_soak.read_soak_records``; mirrors
the exposure / pairs / allocator / exit-ladder soak routers. Tier 1, read-only.

**This route should have shipped in #9627 with the writer, and did not.** That
PR registered the log on ``/api/diag/log_file?name=exit_interval_soak``, which
returns a raw TAIL of lines — never the ``summary``. So
``summary.max_interval_ms``, the cross-process maximum that is the entire reason
the module exists, was **computed on every read and reachable by nobody**
(BL-20260816-EXIT-INTERVAL-SOAK-SUMMARY-HAS-NO-READ-SURFACE).

The sibling router this one is modelled on states the rule it was written to
prevent, in its own docstring: *"a signal written and never read is worse than a
missing one, because reviewers see the field and assume something acts on it. A
soak log with no read surface repeats that exactly."* #9627 repeated it anyway,
in a PR whose stated verification criterion was *reading
``summary.max_interval_ms``* — a read that could not be performed. A tail of
lines is not a substitute: reconstructing the max means paging the whole file
and re-implementing the aggregation client-side, which is precisely the
reader-side windowing bias ``read_soak_records`` computes over the whole file to
avoid.

**Read ``summary.max_interval_ms`` beside ``intervals_measured`` AND
``processes_seen``.** The max is the statistic the operator's 60 s requirement is
written against — a mean that looks fine while a single interval breaches is the
failure this whole line of work exists to catch — and a max over one process is
the biased measurement the durable log replaced. ``processes_seen == 1`` means
you are reading the per-process number again, wearing a cross-process label.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from src.runtime.exit_interval_soak import read_soak_records

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/exit-interval/soak")
def exit_interval_soak(
    limit: int = Query(200, ge=1, le=2000),
    breached_only: bool = Query(
        False,
        description=(
            "return only intervals that exceeded the requirement. NOTE the "
            "summary is always computed over ALL rows on disk, so "
            "max_interval_ms / intervals_measured / processes_seen stay honest "
            "denominators regardless of this filter or of `limit`."
        ),
    ),
):
    """Observe-only. Never touches the exit loop; reads a file the loop appends."""
    return read_soak_records(limit=limit, breached_only=breached_only)
