"""Evidence horizon — how far a leg is from GRADEABLE, and how long until it would be.

WHY THIS MODULE EXISTS
----------------------
The M7 strategy-review gate forces HOLD below ``MIN_CLOSED_FOR_ACTION`` closed
trades (PB-20260630-004). That floor is correct: a real-money KILL off 1-5
trades is noise, not evidence. But on the 2026-09-01 run — population: all 52
enabled strategies, window 7 days, the committed
``comms/strategy_reviews/2026-09-01/INDEX.json`` — ``n_closed`` was **0 for 34
legs, 1-4 for 14, 5-19 for 4, and never above 8**. So 52/52 sat under the floor
and the run **could not have proposed an action whatever the PnL**, including
13 losing legs carrying -35,446.19 of provenance-trusted pnl between them
(that figure is the index's own 52 rows; the 52 committed packets, generated 48
minutes earlier, sum to -35,233.67 over 12 legs — see the run-boundary note in
``scripts/ml/evidence_floor_report.py``).

``actionable: 0`` and ``below_floor: 52`` say *that* this happened. Neither says
**what would have to change**, and the two candidate answers are not the same
kind of fact:

* a leg closing 8 trades a week reaches n=20 in a few more weeks — a WINDOW
  problem, and the window is a dial;
* a leg that closed nothing has **no measurable close rate at all**, so no
  window can be projected for it from this evidence;
* a leg in ``execution: shadow`` does not fill by design, so it accumulates
  **zero closed-trade evidence at any window whatsoever** — not a slow leg, a
  leg this gate structurally cannot grade, needing a different disposition
  mechanism entirely.

Collapsing those into one "below the floor" count is the failure this module
exists to undo. ⚠️ **It does not lower the floor and must never be used to.**
Widening the window until something finally clears n>=20 fires a KILL off an
evidence base assembled precisely to make a KILL fireable — the same low-n
hazard the floor exists to prevent, one level up. The module's output is an
input to an OPERATOR decision (docs/design/evidence-floor-horizon-PROPOSAL.md),
never an automatic one.

THE STATISTICS, AND WHY THERE ARE THREE NUMBERS NOT ONE
-------------------------------------------------------
The naive projection is ``days = floor * window_days / n_closed``. It is a
point estimate off a Poisson count that is itself tiny — projecting from
``n_closed=1`` is a one-sample estimate, and quoting its 140 days as though it
were measured is the low-n error moved from the grade to the forecast. So every
projection is published as an INTERVAL:

* ``days_to_floor_optimistic``   — from the 95% UPPER confidence limit on the
  close rate. The FEWEST days consistent with what was observed. Defined even
  at ``n_closed == 0`` (the rule of three: observing zero events in W days puts
  the rate below ~3/W at 95%), which is what turns "unknown" into a defensible
  *lower bound* instead of a shrug.
* ``days_to_floor_point``        — the point estimate. ``None`` when
  ``n_closed == 0``, because there is no rate to project from.
* ``days_to_floor_conservative`` — from the 95% LOWER confidence limit. The
  MOST days consistent with what was observed. ``None`` when ``n_closed == 0``
  (the lower limit is 0, so the horizon is unbounded) — ``None`` here means
  *unbounded*, and the ``horizon_class`` says which ``None`` this is.

Worked, at ``floor=20``, ``window_days=7`` (verified in
``tests/test_evidence_horizon.py``): ``n_closed=1`` projects to 140 days, but
the interval consistent with that single close runs **~30 days to ~7.5 years**.
A reader shown only "140 days" would set a 5-month window and believe they had
bought gradeability. That is the number this module refuses to publish alone.

⚠️ ``observed_close_rate_per_day`` is ``None``, NEVER ``0.0``, when
``n_closed == 0``. Zero closes in a window is not a measured rate of zero; it
is an absence of measurement that bounds the rate from above. Writing 0.0 there
would make "we did not see it close" arithmetically indistinguishable from "it
closes at a rate of zero" — the collapse this repo has a guard family for.

Stdlib only, deliberately: the producer is a Tier-1 script and the consumer is
a web route, and neither should grow a numerics dependency to read a horizon.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# The horizon contract. FIVE states, registered with `collapsed-state-guard`
# as `strategy_reviews.horizon_class`. Each names a DIFFERENT remedy, which is
# the whole reason they are not one "below_floor" flag.
# ---------------------------------------------------------------------------
#: The leg already has enough closed trades; the floor is not what holds it.
GRADEABLE_NOW = "gradeable_now"
#: Below the floor, but closing at a measurable rate — a finite projection
#: exists. THE ONLY CLASS A WIDER WINDOW ACTUALLY REACHES.
REACHABLE = "reachable"
#: Below the floor with ZERO closes, so no rate could be measured and no finite
#: projection exists. ⚠️ NOT "unreachable" and NOT "rate zero" — the leg may
#: close tomorrow. It means *this window produced no evidence to project from*,
#: and `days_to_floor_optimistic` is the only defensible number for it.
UNBOUNDED_NO_CLOSES = "unbounded_no_closes"
#: The leg cannot produce closed trades at ANY window under its current
#: configuration, so the floor is unreachable by waiting. `structural_reason`
#: names which mechanism. This is the class that needs a different disposition
#: mechanism rather than a bigger number.
STRUCTURALLY_UNGRADEABLE = "structurally_ungradeable"
#: An input was missing — we could not look. Never folded into any of the four
#: above; in particular never into `unbounded_no_closes`, which would turn "we
#: did not read n_closed" into "we read it and it was zero".
UNKNOWN = "unknown"

HORIZON_CLASSES = (
    GRADEABLE_NOW,
    REACHABLE,
    UNBOUNDED_NO_CLOSES,
    STRUCTURALLY_UNGRADEABLE,
    UNKNOWN,
)

# ---------------------------------------------------------------------------
# The funnel. WHY a leg has no closes, from the generator's own counts. The
# four stages are ordered and mutually exclusive; a leg sits at the FIRST one
# it fails. Descriptive — the remedy lives in `horizon_class` — but it is what
# makes "23 legs produced no decision at all" separable from "5 legs opened
# positions that had not closed yet", which are opposite problems.
# ---------------------------------------------------------------------------
STAGE_CLOSING = "closing"
STAGE_FILLED_NOT_CLOSED = "filled_not_closed"
STAGE_DECIDED_NOT_FILLED = "decided_not_filled"
STAGE_NO_DECISIONS = "no_decisions"
STAGE_UNKNOWN = "unknown"

FUNNEL_STAGES = (
    STAGE_CLOSING,
    STAGE_FILLED_NOT_CLOSED,
    STAGE_DECIDED_NOT_FILLED,
    STAGE_NO_DECISIONS,
    STAGE_UNKNOWN,
)

#: One-sided confidence level for both limits. 0.95 rather than 0.99 because
#: the interval is decision support, not a test; at 0.99 the conservative end
#: of an n=1 leg runs past a human planning horizon and stops informing anyone.
DEFAULT_CONFIDENCE = 0.95

#: Bracket ceiling for the confidence-limit bisection, in events per day. Any
#: solution at the ceiling means the bisection did not converge inside the
#: bracket, and the limit it would return is not a limit — see `_days_for_rate`.
_RATE_BRACKET_CEILING = 1e7


def _poisson_cdf(k: int, mean: float) -> float:
    """P(X <= k) for X ~ Poisson(mean). Log-space terms, so no overflow at large mean."""
    if mean <= 0.0:
        return 1.0
    total = 0.0
    log_mean = math.log(mean)
    for i in range(k + 1):
        total += math.exp(-mean + i * log_mean - math.lgamma(i + 1))
    return min(1.0, total)


def _bisect(fn, lo: float, hi: float, target: float, *, decreasing: bool) -> float:
    """Solve ``fn(m) == target`` on ``[lo, hi]`` for a monotone ``fn``.

    ⚠️ ``decreasing`` is REQUIRED rather than inferred. The two confidence
    limits use functions of opposite slope — ``P(X <= k | m)`` falls with the
    mean, ``P(X >= k | m)`` rises with it — and running the decreasing branch on
    the increasing one does not fail: it walks the bracket to its far end and
    returns a plausible-looking limit that is wrong by orders of magnitude
    (measured while writing this module: the n=1 lower limit came back as the
    bracket ceiling 1e7, which `_MAX_RATE` then swallowed into a `None` reading
    as "unbounded"). A silently wrong horizon is worse than a missing one.
    """
    for _ in range(200):
        mid = (lo + hi) / 2.0
        above = fn(mid) > target
        if above == decreasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def poisson_rate_upper_bound(
    events: int, exposure_days: float, confidence: float = DEFAULT_CONFIDENCE
) -> Optional[float]:
    """One-sided UPPER confidence limit on a Poisson rate, per day.

    Solves ``P(X <= events | mean) = 1 - confidence`` for the mean, then divides
    by the exposure. At ``events == 0`` this reduces to the rule of three:
    ``-ln(1 - confidence) / exposure`` (~3/W at 95%), which is what lets a leg
    that closed NOTHING still carry a defensible *lower bound* on how long it
    would need — rather than a `None` that reads as "no information".

    ``None`` when the exposure is missing or non-positive: a rate per day is
    not defined without days, and returning a number there would invent one.
    """
    if exposure_days is None or exposure_days <= 0 or events < 0:
        return None
    alpha = 1.0 - confidence
    if events == 0:
        mean = -math.log(alpha)
    else:
        mean = _bisect(lambda m: _poisson_cdf(events, m), 0.0, _RATE_BRACKET_CEILING, alpha, decreasing=True)
    return mean / float(exposure_days)


def poisson_rate_lower_bound(
    events: int, exposure_days: float, confidence: float = DEFAULT_CONFIDENCE
) -> Optional[float]:
    """One-sided LOWER confidence limit on a Poisson rate, per day.

    Solves ``P(X >= events | mean) = 1 - confidence``. ``None`` at
    ``events == 0`` — the limit there is exactly 0, and a rate of 0 projects to
    an INFINITE horizon. Callers must render that as *unbounded*, not as a very
    large number and not as a missing value.
    """
    if exposure_days is None or exposure_days <= 0 or events <= 0:
        return None
    alpha = 1.0 - confidence
    # P(X >= k) = 1 - P(X <= k-1); increasing in mean, so bisect the complement.
    mean = _bisect(lambda m: 1.0 - _poisson_cdf(events - 1, m), 0.0, _RATE_BRACKET_CEILING, alpha, decreasing=False)
    return mean / float(exposure_days)


def classify_funnel_stage(
    n_decisions: Optional[int], n_filled: Optional[int], n_closed: Optional[int]
) -> str:
    """Where the leg stops. See FUNNEL_STAGES.

    ``unknown`` whenever ``n_closed`` could not be read — the stage below it is
    inferred from counts that are then unanchored.
    """
    if n_closed is None:
        return STAGE_UNKNOWN
    if n_closed > 0:
        return STAGE_CLOSING
    if n_filled is None or n_decisions is None:
        # We know it closed nothing but not why. Saying `no_decisions` here
        # would assert a silent strategy on evidence we do not have.
        return STAGE_UNKNOWN
    if n_filled > 0:
        return STAGE_FILLED_NOT_CLOSED
    if n_decisions > 0:
        return STAGE_DECIDED_NOT_FILLED
    return STAGE_NO_DECISIONS


def _days_for_rate(floor: int, rate_per_day: Optional[float]) -> Optional[float]:
    """Days of exposure needed to accumulate ``floor`` events at ``rate_per_day``.

    ``None`` for a missing or non-positive rate — an unbounded horizon, which
    the caller renders through ``horizon_class`` rather than as a number.

    ⚠️ A rate at the bisection's bracket ceiling is NOT returned as a very short
    horizon. It means the solver ran to the end of its bracket, so the number is
    an artefact of the bracket, not a limit — and the failure direction matters:
    a bogus huge rate would render as "gradeable in hours", the most optimistic
    possible reading of a computation that did not converge.
    """
    if rate_per_day is None or rate_per_day <= 0:
        return None
    if rate_per_day >= _RATE_BRACKET_CEILING:
        return None
    return floor / rate_per_day


def evidence_horizon(
    *,
    floor: int,
    n_closed: Optional[int],
    window_days: Optional[float],
    n_decisions: Optional[int] = None,
    n_filled: Optional[int] = None,
    execution: Optional[str] = None,
    confidence: float = DEFAULT_CONFIDENCE,
) -> Dict[str, Any]:
    """How far this leg is from gradeable, and how long until it would be.

    ``floor`` is passed IN, never re-derived here: ``MIN_CLOSED_FOR_ACTION`` is
    owned by ``scripts/ml/strategy_review_packet.py`` and a second module
    holding its own copy is how the two eventually disagree.

    Every returned number is accompanied by the class that says how to read it.
    Read ``horizon_class`` FIRST — a ``None`` day-count means *unbounded* under
    ``unbounded_no_closes``, *not applicable* under ``structurally_ungradeable``
    and *we could not look* under ``unknown``, and those are three facts.
    """
    stage = classify_funnel_stage(n_decisions, n_filled, n_closed)
    out: Dict[str, Any] = {
        "floor": floor,
        "n_closed": n_closed,
        "window_days": window_days,
        "funnel_stage": stage,
        "confidence": confidence,
        "shortfall": None,
        "observed_close_rate_per_day": None,
        "days_to_floor_optimistic": None,
        "days_to_floor_point": None,
        "days_to_floor_conservative": None,
        "structural_reason": None,
    }

    if n_closed is None or window_days is None or window_days <= 0:
        out["horizon_class"] = UNKNOWN
        out["basis"] = (
            "we could not look: "
            + ("n_closed is absent" if n_closed is None else "window_days is absent or non-positive")
            + " — this is NOT a reading of zero evidence."
        )
        return out

    out["shortfall"] = max(0, floor - n_closed)

    if n_closed >= floor:
        out["horizon_class"] = GRADEABLE_NOW
        out["observed_close_rate_per_day"] = round(n_closed / window_days, 6)
        out["days_to_floor_point"] = 0.0
        out["basis"] = (
            f"n_closed={n_closed} >= floor={floor} over {window_days:g}d — the "
            "evidence floor is not what is holding this leg."
        )
        return out

    # --- Structural: no window reaches this leg under its current config. ---
    # A shadow leg does not place orders, so it cannot fill, so it cannot
    # close. Its closed-trade count stays 0 for ever and the projection below
    # would report a horizon in days for something days do not fix. Scoped to
    # shadow legs with NO fills in the window: a shadow leg that IS filling is
    # a pipeline anomaly the generator's own execution-mismatch override
    # already catches, and it must not be quietly reclassified here.
    if (execution or "").strip().lower() == "shadow" and (n_filled or 0) == 0:
        out["horizon_class"] = STRUCTURALLY_UNGRADEABLE
        out["structural_reason"] = "shadow_execution_no_fills"
        out["basis"] = (
            f"execution=shadow with n_filled=0 over {window_days:g}d — a shadow "
            "leg does not reach the order path by design, so it accumulates NO "
            "closed-trade evidence at any window. Waiting cannot grade it; a "
            "different disposition mechanism is required."
        )
        return out

    hi_rate = poisson_rate_upper_bound(n_closed, window_days, confidence)
    lo_rate = poisson_rate_lower_bound(n_closed, window_days, confidence)
    optimistic = _days_for_rate(floor, hi_rate)
    conservative = _days_for_rate(floor, lo_rate)
    out["days_to_floor_optimistic"] = None if optimistic is None else round(optimistic, 1)
    out["days_to_floor_conservative"] = None if conservative is None else round(conservative, 1)

    if n_closed == 0:
        # ⚠️ The rate is left None on purpose. See the module docstring.
        out["horizon_class"] = UNBOUNDED_NO_CLOSES
        out["basis"] = (
            f"0 closes over {window_days:g}d, so NO close rate was measured and "
            "no finite horizon can be projected from this window. The one "
            "defensible number is the optimistic bound: at "
            f"{confidence:.0%} confidence the rate is below "
            f"{(hi_rate or 0.0):.4f}/day, so reaching n={floor} takes AT LEAST "
            f"{out['days_to_floor_optimistic']}d. Zero observed is not a rate of zero."
        )
        return out

    out["horizon_class"] = REACHABLE
    out["observed_close_rate_per_day"] = round(n_closed / window_days, 6)
    out["days_to_floor_point"] = round(floor * window_days / n_closed, 1)
    out["basis"] = (
        f"{n_closed} closes over {window_days:g}d projects to "
        f"{out['days_to_floor_point']}d for n={floor} — but that point estimate "
        f"rests on {n_closed} event(s); the interval consistent with it runs "
        f"{out['days_to_floor_optimistic']}d to "
        + (
            f"{out['days_to_floor_conservative']}d"
            if out["days_to_floor_conservative"] is not None
            else "unbounded"
        )
        + f" at {confidence:.0%}. Do not quote the point estimate alone."
    )
    return out


def summarize_horizons(horizons: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Fleet-level roll-up of per-leg horizons — the shape the index publishes.

    ⚠️ ``by_class`` carries a key for EVERY declared class including the zeroes,
    so a reader can tell "no leg is structurally ungradeable" from "this
    summary predates the class". An absent key would read as the first while
    meaning the second.
    """
    by_class: Dict[str, int] = {c: 0 for c in HORIZON_CLASSES}
    by_stage: Dict[str, int] = {s: 0 for s in FUNNEL_STAGES}
    reachable_days: List[float] = []
    for h in horizons:
        cls = h.get("horizon_class")
        by_class[cls if cls in by_class else UNKNOWN] += 1
        stage = h.get("funnel_stage")
        by_stage[stage if stage in by_stage else STAGE_UNKNOWN] += 1
        if cls == REACHABLE and h.get("days_to_floor_point") is not None:
            reachable_days.append(float(h["days_to_floor_point"]))
    reachable_days.sort()
    return {
        "n_legs": len(horizons),
        "by_horizon_class": by_class,
        "by_funnel_stage": by_stage,
        # The window a wider setting would have to reach to grade EVERY leg
        # that has a finite projection at all. ⚠️ It grades only those legs —
        # `by_horizon_class` says how many are left out, and reading this
        # number without that count is the unstated-denominator error the
        # whole block exists to prevent.
        "reachable_legs": len(reachable_days),
        "days_to_grade_all_reachable_point": reachable_days[-1] if reachable_days else None,
        "days_to_grade_median_reachable_point": (
            reachable_days[len(reachable_days) // 2] if reachable_days else None
        ),
    }
