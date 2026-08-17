"""What counts as an order that REACHED THE EXCHANGE — the one definition.

`scripts/ops/dead_leg_audit.py` (the offline report) and
`src/runtime/silent_refusal_alert.py` (the live latched alert) both have to
answer "did this order become a position, or die before it?", and they answer
it over the same `trades.status` column. Two copies of that vocabulary is how
they start disagreeing — the report grading a leg healthy while the alert calls
it dead, or a new status the reconciler starts writing quietly changing one and
not the other. So the vocabulary lives here and both import it.

Deliberately NOT in `scripts/`: the live trader tick imports this, and `src/`
depending on `scripts/` inverts the layering. The script imports down into
`src/`, which is the direction everything else already runs.

THE THIRD BUCKET IS LOAD-BEARING. An unrecognised status is neither placed nor
refused; it lands in `other` and gets its own verdict. Folding an unknown into
either bucket would let a status nobody has seen before silently change every
leg's grade — and it would do so in the direction of a confident answer, which
is the worst direction for a signal that exists to be believed.
"""
from __future__ import annotations

from typing import Any, Dict

#: Terminal statuses meaning THE ORDER REACHED THE EXCHANGE AND BECAME A
#: POSITION. This is the numerator that matters: not "did we try", but "did a
#: position exist". `orphaned` counts as PLACED — an orphan is a position the
#: journal lost track of, which is a different (also bad) problem, and folding
#: it into "never placed" would blame order construction for a reconciler fault.
PLACED_STATUSES = ("open", "closed", "orphaned")

#: Statuses meaning the order DIED BEFORE BECOMING A POSITION. Kept as distinct
#: buckets rather than one "failed" count, because they route to different
#: owners: a venue rejection is an order-construction bug, a risk refusal is a
#: sizing/limits question, and conflating them is how a fix gets aimed wrong.
REFUSED_STATUSES = (
    "rejected",              # refused before submission (risk / intent layer)
    "exchange_rejected",     # the venue bounced it
    "rejected_too_small",    # sub-minimum qty
)


def bucket_for(status: Any) -> str:
    """``"placed"`` / ``"refused"`` / ``"other"`` for one `trades.status`."""
    if status in PLACED_STATUSES:
        return "placed"
    if status in REFUSED_STATUSES:
        return "refused"
    return "other"


def verdict_for(counts: Dict[str, int]) -> str:
    """Grade one leg or account from its ``{placed, refused, other}`` counts.

    ``signalled_never_placed`` is the finding this whole family exists to
    surface: rows EXIST and not one of them reached the exchange. It is
    strictly distinct from having no rows at all, which is not a verdict here
    because it is not observable from counts — a caller with zero rows has
    observed nothing and must say so rather than grade it.
    """
    placed, refused, other = (
        counts.get("placed", 0), counts.get("refused", 0), counts.get("other", 0))
    if placed == 0 and refused > 0:
        return "signalled_never_placed"
    if placed == 0 and other > 0:
        return "no_placed_rows_unrecognised_status_only"
    if placed > 0 and refused == 0:
        return "healthy"
    return "partially_refused"


__all__ = ["PLACED_STATUSES", "REFUSED_STATUSES", "bucket_for", "verdict_for"]
