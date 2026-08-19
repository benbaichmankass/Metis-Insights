"""Is holding this position better than closing it and holding CASH?

WHY THIS EXISTS (operator directive, 2026-08-18, asked twice).

    "There's always more risk in holding a position as opposed to liquidating
    it ... if there's not enough upside to holding the crypto and [holding]
    cash, then we should liquidate anyway. I need to come up with some sort of
    proxy for the value of liquidating versus the value of holding."

And, on why the previous answers kept coming back the same:

    "It doesn't feel like we've really thoroughly pushed this as far as it
    should go ... I don't wanna keep trying different Band-Aids and then none
    of them works, so we just say we should hold it instead."

`portfolio_conflicts.py` says WHICH positions conflict. Nothing said WHICH to
drop. This is the ranking step.

THE BIAS THIS MODULE IS BUILT TO AVOID
--------------------------------------
Every exit lever swept so far returns "hold", and there is a structural reason:
each asks *"is the forward expected return positive?"*, and for a leg with any
positive expectancy the answer is usually yes. That test is wrong because it
compares holding against NOTHING. The real alternative is CASH — which returns
zero and, decisively, carries **zero variance**. A position does not have to be
expected to lose money to be worse than cash; it only has to be paying too
little for the risk it is carrying.

So the question is never "is this trade good?" but "does this trade still earn
the risk it occupies?"

HOW RISK IS PRICED, WITHOUT INVENTING A PROBABILITY
---------------------------------------------------
The position's own geometry already states the asymmetry. From here, it can
reach its target (gaining `r_to_target`) or its stop (losing `r_to_stop`).
Holding breaks even only if

    p* = r_to_stop / (r_to_target + r_to_stop)

That is the **hit rate holding REQUIRES**, and it is the whole risk pricing:
as give-back risk grows relative to remaining upside, `p*` rises automatically.
No volatility model, no forecast — it is arithmetic over levels the trade is
already carrying. On the motivating XRP trade (`rr_from_here` 0.71) it is
**0.585**: holding is only worth it if the target is reached more often than
58.5% of the time from a comparable state.

`p*` alone decides nothing. It is compared against `observed_p` — the leg's
MEASURED historical hit rate from a comparable state — which the caller
SUPPLIES. This module never estimates it, never defaults it, and never falls
back to a leg-agnostic prior. An unsupplied `observed_p` yields `unmeasured`.

**`unmeasured` IS NOT `hold`.** That equation is the exact bias above: the
cheapest way to keep every position open is to be unable to grade it. An
unmeasured position is an instruction to go measure, not a decision.

TIME IS PART OF THE PRICE
-------------------------
    "We have all these trades getting to seventy-five percent in a few days and
    then grinding on three more weeks just to get the extra twenty-five."

R is not the unit a capital decision is made in; **R per day** is. A position
earning +0.03 R/day is worse than cash plus a redeployment that earns more,
even while its total R climbs. `r_per_day` is reported beside the verdict, and
`decays_below` names the redeployment rate it no longer clears — supplied by
the caller for the same reason `observed_p` is.

FIVE STATES, NEVER COLLAPSED
----------------------------
  ``hold``          — measured, and the position clears its own required rate.
  ``liquidate``     — measured, and it does not.
  ``unmeasured``    — a required input was absent. We did not look. NOT `hold`.
  ``ungradeable``   — the geometry itself is unusable (no stop, stop already
                      crossed, target behind price). Distinct from `unmeasured`:
                      one is a missing input, the other is a position whose
                      levels cannot express a risk/reward at all.
  ``not_applicable``— nothing at risk to decide about (flat/zero qty).

Observe-only. Pure functions. No DB, no socket, no order path. A caller that
READS this to close a position is a Tier-3 change and needs its own evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

STATE_HOLD = "hold"
STATE_LIQUIDATE = "liquidate"
STATE_UNMEASURED = "unmeasured"
STATE_UNGRADEABLE = "ungradeable"
STATE_NOT_APPLICABLE = "not_applicable"


def _num(value: Any) -> Optional[float]:
    """Finite float, or `None` for anything unreadable. Never coerces to 0.0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def breakeven_p(*, r_to_target: float, r_to_stop: float) -> Optional[float]:
    """The hit rate holding REQUIRES: `r_to_stop / (r_to_target + r_to_stop)`.

    `None` when the denominator is non-positive — which is not a rounding
    concern but a real state: a position whose remaining upside and downside
    both round to nothing has no risk/reward to express, and returning 0.0
    would read as "holding is free".
    """
    denom = r_to_target + r_to_stop
    if denom <= 0:
        return None
    return r_to_stop / denom


@dataclass(frozen=True)
class HoldVerdict:
    """Why this position is or is not still worth the capital it occupies."""

    state: str
    reason: str
    breakeven_p: Optional[float] = None
    observed_p: Optional[float] = None
    edge_p: Optional[float] = None
    r_to_target: Optional[float] = None
    r_to_stop: Optional[float] = None
    rr_from_here: Optional[float] = None
    r_per_day: Optional[float] = None
    decays_below: Optional[float] = None
    inputs: Dict[str, Any] = field(default_factory=dict)

    @property
    def should_liquidate(self) -> bool:
        """True ONLY on a measured `liquidate`. Never on an absent input."""
        return self.state == STATE_LIQUIDATE


def evaluate(
    *,
    r_to_target: Any,
    r_to_stop: Any,
    observed_p: Any = None,
    open_r: Any = None,
    bars_held: Any = None,
    bars_per_day: Any = None,
    redeploy_r_per_day: Any = None,
    qty: Any = None,
) -> HoldVerdict:
    """Grade one open position against the cash alternative.

    `r_to_target` / `r_to_stop` are the position's remaining upside and
    downside in R, as the trade's OWN levels currently stand (from
    `position_telemetry`, not recomputed here — the same
    supply-the-reading discipline `thesis_decay` holds to, and for the same
    reason: a locally re-derived level is a second definition free to drift
    from the one the monitor acts on).

    `observed_p` is the leg's measured hit rate from a comparable state. It is
    NEVER defaulted. `redeploy_r_per_day` is the rate freed capital would earn
    elsewhere — also supplied, also never defaulted; absent, the time axis is
    reported but not used to decide.
    """
    q = _num(qty)
    if q is not None and q == 0:
        return HoldVerdict(STATE_NOT_APPLICABLE, "flat_position")

    tgt, stop = _num(r_to_target), _num(r_to_stop)
    inputs = {"r_to_target": tgt, "r_to_stop": stop, "open_r": _num(open_r),
              "bars_held": _num(bars_held), "bars_per_day": _num(bars_per_day)}

    if tgt is None or stop is None:
        return HoldVerdict(STATE_UNMEASURED, "geometry_unreadable", inputs=inputs)
    if stop <= 0:
        # No downside left to price means the stop is at or through price. That
        # is a real position state, not a missing reading, and it is NOT a free
        # hold: it is a position whose risk/reward cannot be expressed.
        return HoldVerdict(STATE_UNGRADEABLE, "stop_at_or_through_price",
                           r_to_target=tgt, r_to_stop=stop, inputs=inputs)
    if tgt <= 0:
        return HoldVerdict(STATE_UNGRADEABLE, "target_behind_price",
                           r_to_target=tgt, r_to_stop=stop, inputs=inputs)

    p_star = breakeven_p(r_to_target=tgt, r_to_stop=stop)
    rr = tgt / stop

    # Time axis — reported whether or not it can decide, because the operator's
    # question is about capital-days, not only about R.
    r_day = None
    o_r, bars, bpd = _num(open_r), _num(bars_held), _num(bars_per_day)
    if o_r is not None and bars is not None and bpd is not None and bars > 0 and bpd > 0:
        days = bars / bpd
        if days > 0:
            r_day = o_r / days

    obs = _num(observed_p)
    if obs is None:
        return HoldVerdict(
            STATE_UNMEASURED,
            "no_measured_hit_rate_supplied",
            breakeven_p=p_star, r_to_target=tgt, r_to_stop=stop,
            rr_from_here=rr, r_per_day=r_day, inputs=inputs)
    if not (0.0 <= obs <= 1.0):
        return HoldVerdict(
            STATE_UNMEASURED, "hit_rate_out_of_range",
            breakeven_p=p_star, r_to_target=tgt, r_to_stop=stop,
            rr_from_here=rr, r_per_day=r_day, inputs=inputs)

    edge = None if p_star is None else obs - p_star
    redeploy = _num(redeploy_r_per_day)

    if edge is not None and edge <= 0:
        return HoldVerdict(
            STATE_LIQUIDATE,
            f"required_hit_rate_{p_star:.3f}_exceeds_observed_{obs:.3f}",
            breakeven_p=p_star, observed_p=obs, edge_p=edge,
            r_to_target=tgt, r_to_stop=stop, rr_from_here=rr,
            r_per_day=r_day, decays_below=redeploy, inputs=inputs)

    # The probability edge is positive. The capital test is SEPARATE and can
    # still fail it: a trade can be more likely than not to reach its target
    # and still be the wrong place for the money, which is precisely the
    # "grinding three more weeks for the last twenty-five percent" case.
    if redeploy is not None and r_day is not None and r_day < redeploy:
        return HoldVerdict(
            STATE_LIQUIDATE,
            f"earning_{r_day:.4f}_r_per_day_below_redeploy_{redeploy:.4f}",
            breakeven_p=p_star, observed_p=obs, edge_p=edge,
            r_to_target=tgt, r_to_stop=stop, rr_from_here=rr,
            r_per_day=r_day, decays_below=redeploy, inputs=inputs)

    return HoldVerdict(
        STATE_HOLD, "clears_required_hit_rate_and_capital_rate",
        breakeven_p=p_star, observed_p=obs, edge_p=edge,
        r_to_target=tgt, r_to_stop=stop, rr_from_here=rr,
        r_per_day=r_day, decays_below=redeploy, inputs=inputs)
