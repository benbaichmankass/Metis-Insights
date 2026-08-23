"""Is protection resting WHERE WE DECLARED, not merely resting at all?

`BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND`. IB protection coverage was
graded on QUANTITY and SIDE and never on PRICE, so a stop resting 69 ticks from
the journal's declared level covers the full size on the correct side and grades
**FULLY COVERED**. Measured live 2026-08-23 on `ib_paper` MES 4350: the journal
declares `7533.696429`, the resting stop sits at `7516.5` — **17.196 points**,
and at MES $5/pt x 15 contracts that is **$1,289.72** of protection the position
does not have. The live sweep had never once flagged it; it took an offline
reconciler run to see it.

⚠️ **THIS GRADES; IT NEVER REPAIRS.** Deciding what to do about a divergence —
amend the leg, cancel and re-arm, or leave it — changes a live protective order
and is Tier-2/3. This module answers only *"do the two levels agree, and if not
by how much and in which direction"*, so the answer is the same wherever it is
asked from: the live sweep, the offline `broker_bracket_reconcile.py`, or a
review session. One definition, not three.

FIVE STATES, NEVER COLLAPSED — the last three are the point
-----------------------------------------------------------
`agrees`            within tolerance of the declared level.
`diverges`          a real, measured disagreement. The finding.
`no_declared_level` the journal declares nothing to compare against — so there
                    is no divergence to find, which is NOT the same as agreeing.
`no_resting_price`  a leg rests but carried no readable price. **We could not
                    look at it**; it is emphatically not "the price is fine".
`no_resting_leg`    nothing rests on that side at all. That is a *naked* finding
                    and belongs to the coverage grader — reporting it as a price
                    divergence would double-count one condition as two.

⚠️ **DIRECTION IS NOT SYMMETRIC AND MUST NOT BE REPORTED AS A MAGNITUDE.** A
long's stop resting BELOW its declared level leaves the position exposed for
the extra distance; one resting ABOVE exits early and is a different problem,
not a safer version of the same one. `side_of_declared` names which, so a
consumer cannot read a signed number as "how bad".

⚠️ **THE TOLERANCE IS A TICK COUNT, NOT A PERCENTAGE.** Protection is placed on
a venue price grid, and one tick is the smallest disagreement that can exist —
a percentage tolerance would be tight on MES and meaningless on MGC. The caller
supplies `tick_size`; when it cannot, the comparison is `no_tick_size` rather
than a guessed grid.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

__all__ = [
    "PRICE_AGREES", "PRICE_DIVERGES", "PRICE_NO_DECLARED", "PRICE_NO_RESTING",
    "PRICE_NO_LEG", "PRICE_NO_TICK_SIZE", "grade_protection_price",
]

PRICE_AGREES = "agrees"
PRICE_DIVERGES = "diverges"
PRICE_NO_DECLARED = "no_declared_level"
PRICE_NO_RESTING = "no_resting_price"
PRICE_NO_LEG = "no_resting_leg"
PRICE_NO_TICK_SIZE = "no_tick_size"

DEFAULT_TICK_TOLERANCE = 1.0


def _f(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def grade_protection_price(
    *,
    declared: Any,
    resting_prices: Optional[List[Any]],
    direction: Any,
    side: str = "stop",
    tick_size: Any = None,
    tick_tolerance: float = DEFAULT_TICK_TOLERANCE,
) -> Dict[str, Any]:
    """Compare a journal-declared level against the resting leg(s). Pure.

    `resting_prices` is the list this side actually rests at — an EMPTY list
    means nothing rests (`no_resting_leg`), and `None` means the caller could
    not read them (`no_resting_price`). The two are different facts and the
    caller must keep them apart before calling.

    The NEAREST resting price is compared, deliberately: with several legs on
    one side the position is protected at the closest one first, so that is the
    level the declared value should be judged against.
    """
    out: Dict[str, Any] = {
        "state": PRICE_NO_DECLARED,
        "side": side,
        "declared": None,
        "nearest_resting": None,
        "diff": None,
        "ticks": None,
        "side_of_declared": None,
        "resting_count": None if resting_prices is None else len(resting_prices),
        # Was the trade's direction readable? `exposure` is None for BOTH an
        # unreadable direction and a non-stop side, so this says which.
        "direction_known": None,
        "exposure": None,
    }
    dec = _f(declared)
    if dec is None:
        return out
    out["declared"] = dec

    if resting_prices is None:
        out["state"] = PRICE_NO_RESTING
        return out
    usable = [p for p in (_f(x) for x in resting_prices) if p is not None]
    if not resting_prices:
        out["state"] = PRICE_NO_LEG
        return out
    if not usable:
        # Legs rest but none carried a readable price — we could not look.
        out["state"] = PRICE_NO_RESTING
        return out

    nearest = min(usable, key=lambda p: abs(p - dec))
    out["nearest_resting"] = nearest
    diff = nearest - dec
    out["diff"] = diff

    # `None` for an UNREADABLE direction, never a silent default to one of
    # them. `exposure` inverts on this value, so defaulting an unknown
    # direction to "short" would publish a confident label that is exactly
    # backwards for half the book -- the diagnostic-provenance sub-class A
    # shape (the accessor does not compute what the label says, and nothing in
    # the output reveals it). An unknown direction earns NO exposure verdict.
    _d = str(direction or "").strip().lower()
    is_long = True if _d in ("long", "buy") else (
        False if _d in ("short", "sell") else None)
    out["direction_known"] = is_long is not None
    if abs(diff) == 0:
        out["side_of_declared"] = "at"
    elif diff < 0:
        out["side_of_declared"] = "below"
    else:
        out["side_of_declared"] = "above"
    # Name the CONSEQUENCE, not just the geometry: for a long a stop below the
    # declared level means more exposure; for a short it is the mirror. The
    # GEOMETRY (`side_of_declared`) is always published because it needs no
    # direction; only the consequence does.
    if (side == "stop" and is_long is not None
            and out["side_of_declared"] in ("below", "above")):
        wider = (out["side_of_declared"] == "below") if is_long else (
            out["side_of_declared"] == "above")
        out["exposure"] = "more_exposed" if wider else "exits_earlier"
    else:
        out["exposure"] = None

    tick = _f(tick_size)
    if tick is None:
        out["state"] = PRICE_NO_TICK_SIZE
        return out
    ticks = abs(diff) / tick
    out["ticks"] = ticks
    out["state"] = PRICE_AGREES if ticks <= tick_tolerance else PRICE_DIVERGES
    return out
