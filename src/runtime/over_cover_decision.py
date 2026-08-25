"""WHICH protective leg should be cancelled when a position is over-covered?

WHY THIS EXISTS — the selection, not the mechanism
==================================================
`ib_paper` MHG rests **200% stop cover across two disjoint OCA groups**
(measured 2026-08-25, `/api/diag/ib_open_orders`: a 29-lot position against
`oca-protect-416` and `oca-protect-432`, each carrying a 29-lot STP and a 29-lot
LMT — n=1 account/symbol, one point-in-time read, not a rate). OCA cancels only
WITHIN a group, so one stop firing flattens the position and leaves the other
group resting to sell 29 more into a **naked SHORT**.

The condition has been detect-only, and the reason given for that is the thing
this module refuses to accept: **one** auto-remediation, on 2026-08-20, cancelled
the stop that MATCHED the journal and kept the stray
(`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`) — MES
order 375 at 7533.75 was removed against a declared `stop_loss` of
7533.69642857, and stray 338 at 7516.50 survived, leaving the position protected
69 ticks low, $1,289.73 on 15 contracts.

⚠️ THAT IS A REASON TO DISTRUST THE **SELECTION**, NOT THE MECHANISM.
`cancel-ib-order` and `attach-ib-target` are documented, allowlisted
system-actions; the wire has existed all along. What was missing is a decision
anybody could argue with before it touched a live position — and this repo had
already solved exactly that, once, in `src/runtime/protection_reassert.py`:
make the decision a PURE FUNCTION with non-collapsed states *"so the policy is
arguable in tests rather than against a live position"*. Nobody applied that
pattern here. "We automated it once and got it wrong, so a human owns it
forever" is how a detect-only design becomes a permanent operator tax.

So: this module decides. It opens no socket, reads no DB, cancels nothing, and
is tested against the recorded 2026-08-20 failure — see
`tests/test_over_cover_decision.py::test_reproduces_the_2026_08_20_failure`,
which asserts the leg that matched the journal is the one KEPT.

THE INVARIANT, stated once
==========================
**The journal decides, and the journal is `trades.stop_loss` / `take_profit_1`.**
A leg is cancelled because it matches NOTHING we declared — never because of its
order id, its age, its OCA group name, or a level supplied by a caller or an
operator. That is `BL-20260820`'s criterion 2 in as many words, for the reason
its own title records.

⚠️ WHOLE GROUPS, NEVER SINGLE LEGS. Each OCA group here carries a stop AND a
target. Cancelling the stray STOP alone leaves the stray TARGET resting, which
is the same hazard on the other side. The unit of action is the group.

STATES, never collapsed
=======================
`cancel_group`        over-covered; exactly one group matches the journal on
                      the sides it carries, and the others match nothing. Cancel
                      the others.
`no_over_cover`       stop coverage is within tolerance of the position. Nothing
                      to do — NOT a refusal.
`ambiguous_no_action` over-covered, but the journal does not single one group
                      out: two groups match, or groups match on DIFFERENT sides
                      (one holds the declared stop, another the declared
                      target). ⚠️ This is the 2026-08-20 shape and the refusal is
                      the point — a half-fix here strips a leg that was right.
`no_journal_match`    over-covered and NO resting group matches the declaration.
                      Cancelling any of them is a guess, so we do not.
`no_declared_stop`    the journal declares no stop, so there is nothing to match
                      against. Distinct from `no_journal_match`: there we looked
                      and found no match, here there was no question to ask.
`not_graded`          prices or tick size unreadable — **we did not look**. Never
                      reported as `no_over_cover`.
`position_absent`     no position, or its size is unreadable.

`not_graded` and `no_declared_stop` are deliberately not folded into
`no_journal_match`: all three end in "do nothing", and only one of them means
the venue was actually asked and answered.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.runtime.protection_price import (
    PRICE_AGREES,
    PRICE_DIVERGES,
    grade_protection_price,
)

__all__ = [
    "STATE_CANCEL_GROUP", "STATE_NO_OVER_COVER", "STATE_AMBIGUOUS",
    "STATE_NO_JOURNAL_MATCH", "STATE_NO_DECLARED_STOP", "STATE_NOT_GRADED",
    "STATE_POSITION_ABSENT", "ALL_STATES",
    "DEFAULT_OVER_COVER_FACTOR", "decide_over_cover",
]

STATE_CANCEL_GROUP = "cancel_group"
STATE_NO_OVER_COVER = "no_over_cover"
STATE_AMBIGUOUS = "ambiguous_no_action"
STATE_NO_JOURNAL_MATCH = "no_journal_match"
STATE_NO_DECLARED_STOP = "no_declared_stop"
STATE_NOT_GRADED = "not_graded"
STATE_POSITION_ABSENT = "position_absent"

ALL_STATES = (
    STATE_CANCEL_GROUP, STATE_NO_OVER_COVER, STATE_AMBIGUOUS,
    STATE_NO_JOURNAL_MATCH, STATE_NO_DECLARED_STOP, STATE_NOT_GRADED,
    STATE_POSITION_ABSENT,
)

#: Resting stop qty above this multiple of the position is over-cover. Mirrors
#: `order_monitor._IB_OVERCOVER_FACTOR`'s intent: a small margin so a rounding
#: artefact is not a finding, far below the 2.00x this module was written
#: against.
DEFAULT_OVER_COVER_FACTOR = 1.5

def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _leg_side(leg: Dict[str, Any]) -> Optional[str]:
    """Read the caller's classification. This module does NOT classify.

    ⚠️ DELIBERATELY NOT A THIRD COPY of "is this leg a stop or a target".
    `IBClient._protective_leg_side` is the enforcing definition and
    `scripts/ops/broker_bracket_reconcile.protective_leg_side` is its
    deliberate, tested mirror — whose own docstring says a second definition
    free to drift from the enforcing one *"would be its own defect"*. A third
    would be worse, and the ordering it turns on is subtle enough to get wrong:
    ``"STP LMT"`` contains ``"LMT"``, so a target-first test files every
    stop-limit as a take-profit and MANUFACTURES target coverage.

    So the caller classifies with one of those and passes ``side`` on each leg.
    A leg arriving without a readable one is `not_graded` — never guessed, and
    never silently dropped from the coverage sum.
    """
    side = str(leg.get("side") or "").strip().lower()
    return side if side in ("stop", "target") else None


def _leg_price(leg: Dict[str, Any], side: str) -> Optional[float]:
    if side == "stop":
        return _f(leg.get("aux_price")) or _f(leg.get("trigger_price"))
    return _f(leg.get("lmt_price")) or _f(leg.get("price"))


def decide_over_cover(
    *,
    position_qty: Any,
    direction: Any,
    declared_stop: Any,
    declared_target: Any,
    legs: Optional[Sequence[Dict[str, Any]]],
    tick_size: Any = None,
    over_cover_factor: float = DEFAULT_OVER_COVER_FACTOR,
    tick_tolerance: float = 1.0,
) -> Dict[str, Any]:
    """Decide which OCA group(s) to cancel. Pure — decides, never acts.

    `legs` is the resting protective orders for ONE (account, symbol) as
    `/api/diag/ib_open_orders` returns them, each carrying a ``side`` of
    ``stop``/``target`` that the CALLER classified (see `_leg_side`).
    ``None`` means the caller could not read them (`not_graded`); an empty
    list means the venue was asked and nothing rests, which is a naked
    finding owned by the coverage grader, not an over-cover one.
    """
    out: Dict[str, Any] = {
        "state": STATE_NOT_GRADED,
        "cancel_order_ids": [],
        "cancel_groups": [],
        "keep_groups": [],
        "position_qty": None,
        "stop_qty": None,
        "over_cover_pct": None,
        "groups": {},
        "reason": None,
    }

    qty = _f(position_qty)
    if qty is None:
        out["state"] = STATE_POSITION_ABSENT
        out["reason"] = "position size is absent or unreadable"
        return out
    out["position_qty"] = qty

    if legs is None:
        out["reason"] = "the resting legs could not be read — we did not look"
        return out

    # --- bucket the legs by OCA group ------------------------------------
    groups: Dict[str, Dict[str, Any]] = {}
    unreadable = 0
    for leg in legs:
        side = _leg_side(leg)
        if side is None:
            # NOT skipped. An unclassified leg may be a stop, so dropping it
            # would under-count coverage and could turn a real over-cover into
            # a clean `no_over_cover` — the reassuring value, fabricated.
            unreadable += 1
            continue
        group = str(leg.get("oca_group") or "").strip() or f"__ungrouped_{leg.get('order_id')}"
        entry = groups.setdefault(
            group, {"stop": [], "target": [], "order_ids": [], "stop_qty": 0.0})
        price = _leg_price(leg, side)
        entry[side].append(price)
        entry["order_ids"].append(leg.get("order_id"))
        if side == "stop":
            entry["stop_qty"] += _f(leg.get("total_quantity")) or 0.0
    out["groups"] = {
        name: {"stop": entry["stop"], "target": entry["target"],
               "order_ids": entry["order_ids"], "stop_qty": entry["stop_qty"]}
        for name, entry in groups.items()
    }

    if unreadable:
        out["reason"] = (
            f"{unreadable} of {len(legs)} leg(s) arrived with no readable "
            f"'side' — classify them with "
            f"broker_bracket_reconcile.protective_leg_side before calling. A "
            f"leg of unknown side may be a stop, so grading around it would "
            f"under-count coverage.")
        return out

    if not groups:
        out["reason"] = (
            "no protective leg was supplied — we did not look. An empty list "
            "is a naked finding and belongs to the coverage grader, not here.")
        return out

    stop_qty = sum(entry["stop_qty"] for entry in groups.values())
    out["stop_qty"] = stop_qty
    out["over_cover_pct"] = (100.0 * stop_qty / qty) if qty else None

    if stop_qty <= qty * over_cover_factor:
        out["state"] = STATE_NO_OVER_COVER
        out["reason"] = (
            f"resting stop qty {stop_qty} is within {over_cover_factor}x the "
            f"position {qty} — not an over-cover")
        return out

    declared_stop_f = _f(declared_stop)
    if declared_stop_f is None:
        out["state"] = STATE_NO_DECLARED_STOP
        out["reason"] = (
            "the journal declares no stop_loss, so there is nothing to match a "
            "resting leg against — selecting one would be a guess")
        return out

    if _f(tick_size) is None:
        out["reason"] = (
            "no tick size for this symbol, so 'matches the declared level' has "
            "no grid to be judged on — we did not look")
        return out

    # --- grade each group against the journal ----------------------------
    matches: List[str] = []
    matches_nothing: List[str] = []
    partial: List[str] = []
    ungradeable: List[str] = []
    declared_target_f = _f(declared_target)

    for name, entry in groups.items():
        stop_verdict = grade_protection_price(
            declared=declared_stop_f,
            resting_prices=entry["stop"] or [],
            direction=direction,
            side="stop",
            tick_size=tick_size,
            tick_tolerance=tick_tolerance,
        )
        stop_ok = stop_verdict["state"] == PRICE_AGREES
        stop_readable = stop_verdict["state"] in (PRICE_AGREES, PRICE_DIVERGES)

        if entry["target"] and declared_target_f is not None:
            target_verdict = grade_protection_price(
                declared=declared_target_f,
                resting_prices=entry["target"],
                direction=direction,
                side="target",
                tick_size=tick_size,
                tick_tolerance=tick_tolerance,
            )
            target_ok = target_verdict["state"] == PRICE_AGREES
            target_readable = target_verdict["state"] in (PRICE_AGREES, PRICE_DIVERGES)
        else:
            # No target leg here, or nothing declared to compare it to. That is
            # not a mismatch; the group is judged on the side it carries.
            target_ok = None
            target_readable = True
            target_verdict = None

        out["groups"][name]["stop_state"] = stop_verdict["state"]
        out["groups"][name]["target_state"] = (
            target_verdict["state"] if target_verdict else None)

        if not stop_readable or not target_readable:
            ungradeable.append(name)
        elif stop_ok and (target_ok is None or target_ok):
            matches.append(name)
        elif not stop_ok and (target_ok is None or not target_ok):
            matches_nothing.append(name)
        else:
            # Matches on one side and not the other — a group holding the
            # declared TARGET but a stray stop, or vice versa. Cancelling it
            # strips a leg that was right; keeping it leaves the over-cover.
            partial.append(name)

    if ungradeable:
        out["reason"] = (
            f"group(s) {sorted(ungradeable)} carry legs whose price could not be "
            f"read — we did not look, and a partial read must not drive a cancel")
        return out

    if partial:
        out["state"] = STATE_AMBIGUOUS
        out["reason"] = (
            f"group(s) {sorted(partial)} match the journal on ONE side only "
            f"(the declared stop or the declared target, not both). Cancelling "
            f"such a group strips a leg that was correct — the 2026-08-20 "
            f"failure shape. Refusing; this needs a human eye on which side is "
            f"authoritative.")
        return out

    if not matches:
        out["state"] = STATE_NO_JOURNAL_MATCH
        out["reason"] = (
            f"over-covered at {out['over_cover_pct']:.0f}% but NO resting group "
            f"matches the declared stop {declared_stop_f} — every candidate is a "
            f"stray, so cancelling one would be a guess about which the position "
            f"should keep. Refusing.")
        return out

    if len(matches) > 1:
        out["state"] = STATE_AMBIGUOUS
        out["reason"] = (
            f"over-covered at {out['over_cover_pct']:.0f}% and {len(matches)} "
            f"groups {sorted(matches)} ALL match the declared stop "
            f"{declared_stop_f} — the journal does not single one out, so this "
            f"module will not. Refusing.")
        return out

    keep = matches[0]
    cancel = sorted(matches_nothing)
    out["state"] = STATE_CANCEL_GROUP
    out["keep_groups"] = [keep]
    out["cancel_groups"] = cancel
    out["cancel_order_ids"] = [
        order_id for name in cancel for order_id in groups[name]["order_ids"]
        if order_id is not None
    ]
    out["reason"] = (
        f"over-covered at {out['over_cover_pct']:.0f}% ({stop_qty} of stop "
        f"against {qty} of position). Group {keep!r} matches the journal's "
        f"declared stop {declared_stop_f}; group(s) {cancel} match nothing "
        f"declared. Cancel the latter, WHOLE — a group's stop and target go "
        f"together.")
    return out
