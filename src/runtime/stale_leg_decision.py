"""WHICH accumulated Bybit Partial-tpsl leg is stale — by OWNERSHIP, never by AGE.

WHY THIS EXISTS — the selection, not the mechanism
==================================================
`scripts/ops/cancel_stale_tpsl_legs.py` is the allowlisted `cancel-stale-tpsl-legs`
system-action, and its selection rule was **"keep the most recently created leg
in each group, cancel every older one"** — sorted on Bybit's own `createdTime`,
with zero journal awareness. Its docstring states the assumption that makes that
rule sound: the legs are *"all sharing the qty of the account's SINGLE real
position"*.

That assumption does not hold, and it was measured failing on 2026-08-26 against
the live book — `bybit_1`/`ETHUSDT`, position 5.59, **two** open journal rows
(4921 qty 1.18 + 4903 qty 4.41 = 5.59 exactly):

    rank  qty    created            owner  status
    0     0.19   2026-08-25 01:05   5003   CLOSED   <- newest, would be KEPT
    5     1.18   2026-08-22 07:05   4921   OPEN     <- would be CANCELLED

Running it would have kept a dead trade's 0.19 leg, cancelled the live trade's
1.18, and taken a **167%-over-covered** position to **3.4% covered — 96.6%
naked**. That is
`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`
reproduced on the other venue: the same class, the same direction, the same
cause — a selection rule that reads a venue field instead of the declaration.

⚠️ AGE IS NOT OWNERSHIP. A leg is old because its trade has been open a long
time, which is the *opposite* of stale. Under one-way netting a symbol is ONE
exchange position holding N journal rows and N qty-scoped legs, so "newest" picks
whichever trade most recently touched its stop — routinely a trade that has since
closed.

THE INVARIANT, stated once
==========================
**A leg is cancelled because the journal row that OWNS it is closed.** Ownership
is `trades.sl_order_id` / `trades.take_profit_1`'s companion `trades.tp_order_id`
— the ids the entry path captured and `modify_open_order` amends in place. Never
age, never qty, never trigger price, never a caller-supplied level.

A leg no row claims is **not** evidence of staleness. It is evidence that we
cannot attribute it, and cancelling an unattributed protective leg on a live
position is the guess this module exists to refuse.

STATES, never collapsed
=======================
`cancel_legs`          every resting leg is attributed; ≥1 is owned by a CLOSED
                       row; the legs owned by OPEN rows still cover the position.
                       Cancel the closed-owned ones.
`no_stale_legs`        every resting leg is owned by an OPEN row. Nothing to do —
                       NOT a refusal.
`no_resting_legs`      the position is live and NOTHING rests. Already naked.
                       Distinct from `no_stale_legs`: there we found legs and all
                       were live, here there are none to find.
`unattributable_legs`  ≥1 resting leg is claimed by no journal row. REFUSE — we
                       cannot say whether it is protecting something.
`would_undercover`     cancelling the closed-owned legs would drop stop coverage
                       BELOW the position. REFUSE: this position needs a top-up
                       (the naked sweep's job), and a cancel-first would open the
                       gap before anything closed it.
`not_graded`           legs or journal rows unreadable — **we did not look**.
                       Never reported as `no_stale_legs`.
`position_absent`      no position, or its size is unreadable.

`not_graded`, `no_resting_legs` and `unattributable_legs` are deliberately three
states and not one: all end in "cancel nothing", and only one of them means the
venue was asked, answered, and the answer was clean.

NOT registered with `collapsed-state-guard`, and the reason is the point
=======================================================================
The guard identifies a consumer by finding the state's LITERAL token in a
non-producer file. `cancel_stale_tpsl_legs.py` branches explicitly on
`cancel_legs` and `no_stale_legs` and routes every other state through
`f"abort_{decision['state']}"` — so it holds no literals for the remaining five
and the guard would report them as unread.

That report would be **wrong about the risk**, not merely inconvenient. A
generic passthrough is the STRONGER shape here: a state added tomorrow gets its
own operator-visible `action` and its own `detail` with no edit, so it cannot be
silently folded into a neighbour — which is the collapse the guard exists to
catch. Adding five literal `elif` arms to satisfy a token search would make the
consumer weaker and the guard greener, and this repo has already recorded that
trade once: `new-table-wiring-guard`'s presence-only marker made the cheapest way
to silence a real finding *naming a table that does not exist*.

So the states are pinned HERE instead, by `test_every_declared_state_is_reachable`
(every declared state is produced by some input) and by the script's own tests
asserting the distinct `abort_*` action per refusal. Register it if and when a
consumer genuinely needs per-state behaviour — not to make a checker green.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "STATE_CANCEL_LEGS", "STATE_NO_STALE_LEGS", "STATE_NO_RESTING_LEGS",
    "STATE_UNATTRIBUTABLE", "STATE_WOULD_UNDERCOVER", "STATE_NOT_GRADED",
    "STATE_POSITION_ABSENT", "ALL_STATES",
    "OPEN_STATUSES", "COVERAGE_TOLERANCE", "decide_stale_legs",
]

STATE_CANCEL_LEGS = "cancel_legs"
STATE_NO_STALE_LEGS = "no_stale_legs"
STATE_NO_RESTING_LEGS = "no_resting_legs"
STATE_UNATTRIBUTABLE = "unattributable_legs"
STATE_WOULD_UNDERCOVER = "would_undercover"
STATE_NOT_GRADED = "not_graded"
STATE_POSITION_ABSENT = "position_absent"

ALL_STATES = (
    STATE_CANCEL_LEGS, STATE_NO_STALE_LEGS, STATE_NO_RESTING_LEGS,
    STATE_UNATTRIBUTABLE, STATE_WOULD_UNDERCOVER, STATE_NOT_GRADED,
    STATE_POSITION_ABSENT,
)

#: A journal row in one of these statuses still holds the position, so its leg
#: is live protection. Anything else — closed, orphaned, superseded, rejected —
#: owns no position and its leg is stale. ⚠️ The membership test is
#: OPEN-side, not closed-side, deliberately: a status nobody anticipated must
#: fall out as "not open" (its leg is a cancel candidate we then have to justify)
#: rather than silently join the keep set, which is the direction that leaves a
#: leak in place rather than the direction that strips protection.
OPEN_STATUSES = frozenset({"open", "filled", "partially_filled"})

#: Relative slack on the coverage comparison so a venue's qty rounding is not
#: read as an under-cover refusal. Deliberately tight — this guards a live
#: position's stop, and a generous tolerance here buys nothing and hides a real
#: shortfall.
COVERAGE_TOLERANCE = 0.01


def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _oid(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _leg_side(leg: Dict[str, Any]) -> Optional[str]:
    """Read the caller's classification. This module does NOT classify.

    ⚠️ DELIBERATELY NOT ANOTHER COPY of "is this leg a stop or a target".
    `order_monitor._SL_LEG_TYPES_MON` is the enforcing definition on this venue;
    a second one free to drift from it would be its own defect. The caller
    classifies and passes ``side``. A leg arriving without a readable one is
    never guessed and never silently dropped — it is counted as unreadable, and
    an unreadable leg makes the whole read `not_graded` rather than shrinking
    the coverage sum toward a reassuring answer.
    """
    side = str(leg.get("side") or "").strip().lower()
    return side if side in ("stop", "target") else None


def _build_ownership(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """order_id -> {trade_id, status, is_open, qty, side}. Last writer wins is
    not a hazard here: two rows claiming one leg would both be reported, and the
    map is only consulted for is_open, which such a pair would have to agree on
    to matter."""
    owners: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        status = str(row.get("status") or "").strip().lower()
        is_open = status in OPEN_STATUSES
        entry_base = {
            "trade_id": row.get("id") if row.get("id") is not None else row.get("trade_id"),
            "status": status or None,
            "is_open": is_open,
            "qty": _f(row.get("position_size")),
        }
        for key, side in (("sl_order_id", "stop"), ("tp_order_id", "target")):
            oid = _oid(row.get(key))
            if oid is None:
                continue
            entry = dict(entry_base)
            entry["side"] = side
            owners[oid] = entry
    return owners


def decide_stale_legs(
    *,
    position_qty: Any,
    legs: Optional[Sequence[Dict[str, Any]]],
    journal_rows: Optional[Sequence[Dict[str, Any]]],
    coverage_tolerance: float = COVERAGE_TOLERANCE,
) -> Dict[str, Any]:
    """Decide which resting legs are stale. Pure — decides, never acts.

    `legs` is the resting protective orders for ONE (account, symbol), each
    carrying ``order_id``, ``qty`` and a ``side`` of ``stop``/``target`` the
    CALLER classified. ``None`` means the caller could not read them
    (`not_graded`); ``[]`` means the venue was asked and nothing rests.

    `journal_rows` is every journal row for that (account, symbol) that carries a
    tracked leg id — open AND closed, because a CLOSED row is exactly what makes
    a leg stale, so filtering to open rows before calling would turn every stale
    leg into an unattributable one. ``None`` means unreadable (`not_graded`).
    """
    out: Dict[str, Any] = {
        "state": STATE_NOT_GRADED,
        "cancel_order_ids": [],
        "cancel_legs": [],
        "keep_legs": [],
        "unattributable_legs": [],
        "position_qty": None,
        "stop_qty_resting": None,
        "stop_qty_kept": None,
        "reason": None,
    }

    qty = _f(position_qty)
    if qty is None or qty <= 0:
        out["state"] = STATE_POSITION_ABSENT
        out["reason"] = "position size is absent, zero or unreadable"
        return out
    out["position_qty"] = qty

    if legs is None:
        out["reason"] = "the resting legs could not be read — we did not look"
        return out
    if journal_rows is None:
        out["reason"] = "the journal rows could not be read — we did not look"
        return out

    if not legs:
        out["state"] = STATE_NO_RESTING_LEGS
        out["reason"] = (
            "the position is live and NOTHING rests against it — already naked. "
            "This is a coverage finding, not a stale-leg one; cancelling is not "
            "the remedy."
        )
        return out

    owners = _build_ownership(journal_rows)

    keep: List[Dict[str, Any]] = []
    cancel: List[Dict[str, Any]] = []
    unattributable: List[Dict[str, Any]] = []
    unreadable_side = 0
    stop_resting = 0.0
    stop_kept = 0.0

    for leg in legs:
        side = _leg_side(leg)
        oid = _oid(leg.get("order_id") or leg.get("orderId"))
        leg_qty = _f(leg.get("qty"))
        if side is None:
            # NOT skipped. An unclassified leg may be a stop, so dropping it
            # would under-count resting coverage and could turn a real shortfall
            # into a clean pass — the reassuring value, fabricated.
            unreadable_side += 1
            continue
        if side == "stop" and leg_qty is not None:
            stop_resting += leg_qty

        owner = owners.get(oid) if oid else None
        record = {
            "order_id": oid,
            "side": side,
            "qty": leg_qty,
            "owner_trade_id": (owner or {}).get("trade_id"),
            "owner_status": (owner or {}).get("status"),
        }
        if owner is None:
            unattributable.append(record)
        elif owner.get("is_open"):
            keep.append(record)
            if side == "stop" and leg_qty is not None:
                stop_kept += leg_qty
        else:
            cancel.append(record)

    out["keep_legs"] = keep
    out["cancel_legs"] = cancel
    out["unattributable_legs"] = unattributable
    out["stop_qty_resting"] = stop_resting
    out["stop_qty_kept"] = stop_kept

    if unreadable_side:
        out["state"] = STATE_NOT_GRADED
        out["reason"] = (
            f"{unreadable_side} resting leg(s) carry no readable side — we cannot "
            "say what they protect, so the coverage sum below them is not a "
            "measurement"
        )
        return out

    if unattributable:
        out["state"] = STATE_UNATTRIBUTABLE
        out["reason"] = (
            f"{len(unattributable)} resting leg(s) are claimed by NO journal row "
            f"(order ids {[r['order_id'] for r in unattributable]}). A leg we "
            "cannot attribute is not evidence of staleness — cancelling it is a "
            "guess against a live position, so nothing is cancelled."
        )
        return out

    if not cancel:
        out["state"] = STATE_NO_STALE_LEGS
        out["reason"] = (
            f"all {len(keep)} resting leg(s) are owned by OPEN journal rows — "
            "there is nothing stale to cancel"
        )
        return out

    if stop_kept < qty * (1.0 - coverage_tolerance):
        out["state"] = STATE_WOULD_UNDERCOVER
        out["reason"] = (
            f"cancelling the {len(cancel)} closed-owned leg(s) would leave "
            f"{stop_kept} of stop against a position of {qty} — under-covered. "
            "This position needs a top-up first; a cancel would open the gap "
            "before anything closed it."
        )
        return out

    out["state"] = STATE_CANCEL_LEGS
    out["cancel_order_ids"] = [r["order_id"] for r in cancel]
    out["reason"] = (
        f"{len(cancel)} resting leg(s) are owned by CLOSED journal rows; the "
        f"{len(keep)} leg(s) owned by OPEN rows carry {stop_kept} of stop "
        f"against a position of {qty}, so cancelling the stale ones leaves the "
        "position covered."
    )
    return out
