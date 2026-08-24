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

AND SO IS THE FOURTH (2026-08-24, ``BL-20260824-SILENT-REFUSAL-CANNOT-SEE-A-DECLARED-DRY-RUN``).
"refused because the account is BROKEN" and "refused because the account is
deliberately SWITCHED OFF" are opposite facts that produce byte-identical
``trades.status`` rows. A ``mode: dry_run`` account refusing every order is the
execution gate working exactly as designed — the repo already said so, in
``execution_diagnostics.EXPECTED_DISPATCH_SKIP_REASONS``, by operator directive
2026-07-15, *after shelving ``alpaca_live`` to dry_run made every tick fire both
banners*. ``silent_refusal_alert`` shipped in August without consulting that
classifier and re-created the alarm the operator had already had suppressed: it
latched ``alerting: true`` on ``alpaca_live`` from 2026-08-21 and held it for
three days, on an account whose refusals were correct, wearing an
``account_class: real_money`` label that reads maximally alarming. That is the
desensitized-alarm P1 this repo names as its own worst failure mode, self-
inflicted on its newest detector.

So a refusal whose reason is a declared policy skip lands in ``policy_skipped``
and gets its OWN verdict, ``refusing_by_declaration``. The predicate is NOT
re-derived here — it is imported from ``execution_diagnostics``, because a
second copy of "is this refusal deliberate?" is free to drift from the first and
the two would then disagree about whether to wake the operator.

FAIL-SAFE, opposite polarity to ``account_side_filter``. That module is fail-
PERMISSIVE because it gates an order. This one gates an ALARM, so an
unrecognised reason stays a real refusal and still alerts: the failure we refuse
is a genuine outage silenced by a classifier that could not read it.
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


def bucket_for(status: Any, reason: Any = None) -> str:
    """``"placed"`` / ``"refused"`` / ``"policy_skipped"`` / ``"other"``.

    *reason* is the row's ``entry_reason``. It is consulted ONLY to separate a
    deliberate policy skip from a real refusal; omitting it preserves the
    pre-2026-08-24 three-bucket behaviour exactly, so every existing caller is
    unaffected until it opts in by passing the reason.
    """
    if status in PLACED_STATUSES:
        return "placed"
    if status in REFUSED_STATUSES:
        if reason is not None and _is_declared_policy_skip(reason):
            return "policy_skipped"
        return "refused"
    return "other"


def _is_declared_policy_skip(reason: Any) -> bool:
    """True when *reason* is a declared, expected policy skip.

    Delegates to ``execution_diagnostics.is_expected_dispatch_skip`` — the one
    module that owns "is this refusal deliberate?". Imported lazily so this
    module stays cheap for the live tick, and fail-SAFE: if the predicate cannot
    be loaded the row counts as a REAL refusal, because silencing a genuine
    outage is the worse error.
    """
    try:
        from src.runtime.execution_diagnostics import is_expected_dispatch_skip
    except Exception:  # noqa: BLE001
        return False
    try:
        return bool(is_expected_dispatch_skip(reason))
    except Exception:  # noqa: BLE001
        return False


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
    skipped = counts.get("policy_skipped", 0)
    if placed == 0 and refused == 0 and skipped > 0:
        # Every refusal was a DECLARED skip. The account is off, not broken.
        return "refusing_by_declaration"
    if placed == 0 and refused > 0:
        return "signalled_never_placed"
    if placed == 0 and other > 0:
        return "no_placed_rows_unrecognised_status_only"
    if placed > 0 and refused == 0:
        return "healthy"
    return "partially_refused"


__all__ = ["PLACED_STATUSES", "REFUSED_STATUSES", "bucket_for", "verdict_for"]
