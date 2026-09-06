"""Is a leg's declared take-profit a PREDICTION about where the trade ends?

`docs/design/exit-mechanism-construction-PROCESS.md` § E3.6 states the
falsifier this module implements, and it is the only one of E3.6's
requirements that had **no instrument, no artifact and no cell** before this
module existed (`docs/research/exit-lever-wiring-audit-2026-09-06.md` § Q1):

    "a predictive bracket is a **claim about where the trade will exit**, so
    it is graded against realised exits -- calibration first (does the stated
    expectation match the observed distribution?), P&L second. A bracket that
    improves net R while being systematically wrong about *where* trades exit
    has not met this bar; it has found a different edge and should say so."

`target_expectation.py` grades what a trade DECLARED, from config. This grades
what actually HAPPENED, from the journal, and puts the two side by side. It is
the second half of the same question and deliberately a separate module: the
first is a property of configuration and needs no live data, this one is a
property of realised outcomes and is meaningless without them.

⚠️ **THIS MODULE DECIDES NOTHING AND MOVES NO ORDER.** Pure: no I/O, no
imports beyond stdlib and the two constant owners, never raises.

WHY PERCENT-OF-ENTRY AND NOT R -- this is the load-bearing design choice
-----------------------------------------------------------------------
The obvious basis is R, and it is the wrong one here, for two independent
reasons that happen to point the same way.

1. **The R denominator is contaminated and the contamination is measured.**
   `trades.stop_loss` is the FINAL TRAILED stop, not the risk taken at entry,
   and `order_packages.sl` is overwritten in place by the same
   `_apply_update` path -- so both records erase the level they replaced
   (`EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md` § evidence contamination).
   MI-144 measured the consequence on 2026-09-06: over the whole journal
   (n=1287) **104 rows -- 8.1% -- carry 96.6% of `totalR`**, single-row max
   `+3672.3`, and the live `/api/bot/performance` publishes a sign-inverted
   `expectancyR`. An instrument built on that denominator would inherit it.

2. **The venue clamp is itself a percent of entry**, so percent-of-entry is
   the basis on which "is this target a prediction or an artefact?" is
   directly decidable -- `TP_VENUE_CAP_PCT` is 9.9% *of entry*, full stop.
   Expressed in R the same clamp is a different number for every trade
   (`cap_r = TP_VENUE_CAP_PCT * entry / risk`), which is exactly why
   `tp_venue_cap.py` warns that **no `tp_r` reproduces the clamp**.

Entry price and exit price are the two fields this module needs, and neither
is rewritten by any monitor path. That is the whole reason the basis works.

WHY `take_profit_1` IS A TRUSTWORTHY RECORD OF THE ENTRY-TIME TARGET
--------------------------------------------------------------------
It has exactly **one** writer -- `order_monitor.py:1374`,
`trade_sync["take_profit_1"] = updates["tp"]` -- which fires only when a
strategy's `monitor()` returns a `tp` delta. **No live strategy has ever
produced one** (AST-verified 2026-08-23 across every module in
`src/units/strategies/`, re-confirmed by MI-146 on 2026-09-06: fourteen
`return {"sl": ...}` sites exist, `return {"tp": ...}` exists nowhere). The
one acting producer in the repo rolls `turtle_soup`'s `meta.tp2` forward, and
`turtle_soup` is `execution: shadow`.

⚠️ **So this is an ARGUMENT FROM THE CURRENT STATE OF THE FLEET, not an
invariant, and it expires the moment clause 2 of this milestone ships.** The
grade therefore carries `target_may_have_moved` whenever the row's strategy is
one the caller names as having an acting `tp` producer, rather than silently
assuming the field is pristine forever. Callers that cannot establish the set
pass `None` and get `TARGET_PROVENANCE_UNKNOWN` -- *we did not look*, which is
never folded into "it did not move".

FOUR GRADES, NEVER COLLAPSED
----------------------------
`graded`
    Entry, exit and a positive declared target were all readable.
`no_target_declared`
    The trade rested no take-profit. **This is the sentinel population's
    signature and it is a FINDING, not missing data** -- on a leg carrying
    `tp_r: 50.0` the target is `entry x 1.099`, so a row here means even that
    was not placed.
`unreadable`
    Entry or exit could not be read. *We did not look* -- never "the trade
    missed its target", and never pooled with `no_target_declared`: one is a
    missing INPUT, the other a missing DECLARATION.
`degenerate`
    Entry <= 0, or a target on the wrong side of entry (a "target" below a
    long's entry is not a target). Kept apart from `unreadable` because it is
    a defect in the RECORD rather than an absence of one.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT

__all__ = [
    "GRADE_OK", "GRADE_NO_TARGET", "GRADE_UNREADABLE", "GRADE_DEGENERATE",
    "TARGET_PROVENANCE_UNKNOWN", "TARGET_PROVENANCE_STATIC",
    "TARGET_PROVENANCE_MAY_HAVE_MOVED",
    "CLAMP_TOLERANCE_FRAC", "TP_VENUE_CAP_PCT",
    "grade_trade", "summarise", "quantile",
]

GRADE_OK = "graded"
GRADE_NO_TARGET = "no_target_declared"
GRADE_UNREADABLE = "unreadable"
GRADE_DEGENERATE = "degenerate"

TARGET_PROVENANCE_UNKNOWN = "unknown"
TARGET_PROVENANCE_STATIC = "static_no_acting_producer"
TARGET_PROVENANCE_MAY_HAVE_MOVED = "may_have_moved"

#: How close a declared target must sit to the venue cap, as a fraction of the
#: cap itself, to be called clamp-bound. 2% of 9.9% is ~20bp of entry -- wide
#: enough to absorb tick rounding and the exchange's own price-step snapping,
#: narrow enough that a genuine 9.7% or 10.2% target is not swept up.
#: A RECOGNISER for an artefact, not a tuning knob.
CLAMP_TOLERANCE_FRAC = 0.02


def _f(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _is_long(direction: Any) -> bool:
    return str(direction or "").lower() in ("long", "buy")


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated quantile. ``None`` on an empty sequence.

    Stdlib-only on purpose (this module imports no numpy); ``None`` rather
    than ``0.0`` on empty, because zero is a real quantile of a real
    distribution and "there was nothing to take a quantile of" is not.
    """
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = max(0.0, min(1.0, float(q))) * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def grade_trade(
    row: Any,
    *,
    acting_tp_producer_strategies: Optional[Iterable[str]] = None,
    cap_pct: float = TP_VENUE_CAP_PCT,
) -> Dict[str, Any]:
    """Grade one closed trade's declared target against where it ended. Pure.

    ``acting_tp_producer_strategies`` names the strategies whose ``monitor()``
    can move a resting take-profit. Pass an explicit (possibly empty)
    collection once you have established the set; pass ``None`` when you have
    not, and every row grades ``target_provenance: unknown`` rather than
    claiming the recorded target is the entry-time one.
    """
    get = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))

    out: Dict[str, Any] = {
        "grade": GRADE_UNREADABLE,
        "strategy": get("strategy_name"),
        "symbol": get("symbol"),
        "direction": get("direction"),
        "exit_reason": get("exit_reason"),
        "target_pct": None,
        "exit_pct": None,
        "attainment": None,
        "reached_target": None,
        "clamp_bound": None,
        "target_provenance": TARGET_PROVENANCE_UNKNOWN,
    }

    if acting_tp_producer_strategies is not None:
        acting = {str(s) for s in acting_tp_producer_strategies}
        out["target_provenance"] = (
            TARGET_PROVENANCE_MAY_HAVE_MOVED
            if str(get("strategy_name") or "") in acting
            else TARGET_PROVENANCE_STATIC
        )

    entry = _f(get("entry_price"))
    exit_px = _f(get("exit_price"))
    if entry is None or exit_px is None:
        return out
    if entry <= 0:
        out["grade"] = GRADE_DEGENERATE
        return out

    sign = 1.0 if _is_long(get("direction")) else -1.0
    out["exit_pct"] = sign * (exit_px - entry) / entry

    target = _f(get("take_profit_1"))
    if target is None or target <= 0:
        # The trade rested no target at all. A FINDING, not missing data.
        out["grade"] = GRADE_NO_TARGET
        return out

    target_pct = sign * (target - entry) / entry
    if target_pct <= 0:
        # A "target" on the wrong side of entry is not a target.
        out["grade"] = GRADE_DEGENERATE
        out["target_pct"] = target_pct
        return out

    out["grade"] = GRADE_OK
    out["target_pct"] = target_pct
    out["attainment"] = out["exit_pct"] / target_pct
    out["reached_target"] = out["exit_pct"] >= target_pct
    out["clamp_bound"] = abs(target_pct - cap_pct) <= cap_pct * CLAMP_TOLERANCE_FRAC
    return out


def summarise(grades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Fleet/leg-level calibration read over ``grade_trade`` outputs.

    ⚠️ **Every count carries its own denominator.** ``n_input`` is what was
    handed in; ``n_graded`` is what could be graded; the rates below are over
    ``n_graded`` and are ``None`` when it is zero -- never ``0.0``, which is a
    real rate (every trade missed) and not the same as "nothing to measure".
    """
    gs: List[Dict[str, Any]] = list(grades)
    counts: Dict[str, int] = {}
    for g in gs:
        k = str(g.get("grade"))
        counts[k] = counts.get(k, 0) + 1

    ok = [g for g in gs if g.get("grade") == GRADE_OK]
    n = len(ok)
    exits = [g["exit_pct"] for g in ok if g.get("exit_pct") is not None]
    targets = [g["target_pct"] for g in ok if g.get("target_pct") is not None]
    attain = [g["attainment"] for g in ok if g.get("attainment") is not None]

    out: Dict[str, Any] = {
        "n_input": len(gs),
        "n_graded": n,
        "grade_counts": counts,
        "reach_rate": None,
        "clamp_bound_rate": None,
        "median_target_pct": quantile(targets, 0.5),
        "median_exit_pct": quantile(exits, 0.5),
        "median_attainment": quantile(attain, 0.5),
        # Where the declared target SITS in the realised exit distribution.
        # This is the calibration number: a target at the 0.99 quantile is not
        # a prediction about where trades end, it is out of reach.
        "target_quantile_in_exits": None,
        "exit_pct_p50": quantile(exits, 0.50),
        "exit_pct_p75": quantile(exits, 0.75),
        "exit_pct_p90": quantile(exits, 0.90),
    }
    if n == 0:
        return out

    out["reach_rate"] = sum(1 for g in ok if g.get("reached_target")) / n
    out["clamp_bound_rate"] = sum(1 for g in ok if g.get("clamp_bound")) / n
    med_t = out["median_target_pct"]
    if med_t is not None and exits:
        out["target_quantile_in_exits"] = (
            sum(1 for e in exits if e <= med_t) / len(exits)
        )
    return out
