"""Classify a resting Bybit protective leg by WHICH BOOK it reduces.

WHY THIS EXISTS (2026-09-02). ``order_monitor._bybit_position_protection``
sums every resting Partial-mode SL leg on a symbol into ONE ``covered_qty``
with **no reference to the leg's side**, and
``_check_broker_naked_bybit_positions`` compares that side-blind sum against
the graded position's size. When the excess trips ``_BYBIT_OVERCOVER_FACTOR``
the operator is paged with:

    "position 0.018 but resting SL legs total 0.478 (2656%)"

which invites the reader to investigate why the LIVE position is
over-protected. MEASURED on ``bybit_1``/BTCUSDT via
``/api/diag/bybit_open_orders``, read 2026-09-02T03:30:33Z (trader
``git_sha 68e73de8``): the live position is ``Buy 0.018 positionIdx=1`` and
its own two legs are ``Sell 0.018`` SL + ``Sell 0.018`` TP — a 1.00x match.
The entire excess is ``Buy 0.46`` SL + ``Buy 0.46`` TP, which are reduce-only
orders that can only reduce a SHORT, and no short book was reported for that
symbol. So the page named a cause no code path tested — UNPROVENANCED
DIAGNOSTIC OUTPUT sub-class A (``CLAUDE.md`` § "Diagnostic provenance"):
different condition, different remedy.

**The remedy is to branch on the actual condition, not to reword the label**,
and that is what this module supplies. It is a PURE function of the venue
read, deliberately, so the policy is arguable in tests rather than against a
live position — the lesson of
``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``,
and the same shape as ``src/runtime/over_cover_decision.py`` and
``src/runtime/stray_oca_groups.py``.

⚠️ **THIS MODULE DECIDES NOTHING AND CANCELS NOTHING.** It classifies. The
caller's re-arm decision still reads the unchanged side-blind ``covered_qty``
(see ``_bybit_position_protection``), so landing this changes no order-path
behaviour — only what the page SAYS. That the side-blind sum can also mask a
genuinely under-covered book is a real and separate defect; it is named in
the PR body and is a Tier-2 change, not enacted here.

⚠️ **"REDUCES THE OTHER BOOK" IS NOT "ORPHANED", AND THIS MODULE REFUSES TO
SAY IT IS.** Under one-way netting (``positionIdx == 0``) no opposite book can
exist, so such a leg is orphaned by construction. Under HEDGE mode — armed on
``bybit_1`` and ``bybit_2`` since 2026-08-30, see ``CLAUDE.md``
§ ``BYBIT_HEDGE_MODE_SYMBOLS`` — the opposite book MAY be a live sibling
position whose protection must be preserved. Collapsing the two would
re-commit the original sin one level along: a confident label over a quantity
the code did not compute. So the leg class says only which book the leg acts
on, and :func:`other_book_state` separately says whether such a book could
exist at all.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence

__all__ = [
    "LEG_REDUCES_GRADED_BOOK",
    "LEG_REDUCES_OTHER_BOOK",
    "LEG_SIDE_UNREADABLE",
    "POSITION_SIDE_UNREADABLE",
    "LEG_SIDE_STATES",
    "OTHER_BOOK_IMPOSSIBLE_ONE_WAY",
    "OTHER_BOOK_POSSIBLE_HEDGE",
    "OTHER_BOOK_UNKNOWN",
    "OTHER_BOOK_STATES",
    "classify_leg_side",
    "other_book_state",
    "split_legs_by_side",
]

# --- the leg-side vocabulary, never collapsed -------------------------------
#
# `LEG_REDUCES_GRADED_BOOK` is the ONLY class that is coverage of the position
# we graded. The other three are each a different fact, and two of them are
# "we did not look" — which `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed
# states" requires stay distinguishable from "we looked and found nothing".
LEG_REDUCES_GRADED_BOOK = "reduces_graded_book"
LEG_REDUCES_OTHER_BOOK = "reduces_other_book"
LEG_SIDE_UNREADABLE = "leg_side_unreadable"
POSITION_SIDE_UNREADABLE = "position_side_unreadable"

LEG_SIDE_STATES = (
    LEG_REDUCES_GRADED_BOOK,
    LEG_REDUCES_OTHER_BOOK,
    LEG_SIDE_UNREADABLE,
    POSITION_SIDE_UNREADABLE,
)

# --- whether an opposite book can exist at all ------------------------------
OTHER_BOOK_IMPOSSIBLE_ONE_WAY = "impossible_one_way"
OTHER_BOOK_POSSIBLE_HEDGE = "possible_hedge"
OTHER_BOOK_UNKNOWN = "unknown"

OTHER_BOOK_STATES = (
    OTHER_BOOK_IMPOSSIBLE_ONE_WAY,
    OTHER_BOOK_POSSIBLE_HEDGE,
    OTHER_BOOK_UNKNOWN,
)

_LONG = "long"
_SHORT = "short"


def _norm_side(raw: Any) -> str:
    """``Buy``/``long`` -> ``"long"``, ``Sell``/``short`` -> ``"short"``, else ``""``.

    A private copy of ``order_monitor._norm_position_side``'s mapping rather
    than an import, so this module stays a leaf with no runtime dependency on
    the 10k-line monitor. The vocabularies are asserted equal in
    ``tests/test_bybit_leg_sides.py`` so the two cannot drift apart silently.
    """
    s = str(raw or "").strip().lower()
    if s in ("buy", "long"):
        return _LONG
    if s in ("sell", "short"):
        return _SHORT
    return ""


def classify_leg_side(position_side: Any, leg_side: Any) -> str:
    """Which book does this reduce-only protective leg act on?

    A protective leg is reduce-only and therefore acts on the book it can
    SHRINK: a ``Sell`` reduces a LONG, a ``Buy`` reduces a SHORT. So a leg
    whose side is the OPPOSITE of the position's side reduces that position;
    a leg whose side EQUALS it cannot touch it and acts on the other book.

    ⚠️ ``position_side`` unreadable is graded ``POSITION_SIDE_UNREADABLE``, not
    guessed. With no position side there is no "opposite" to compare against,
    and picking one would be a coin flip stamped as a measurement.
    """
    pos = _norm_side(position_side)
    if pos not in (_LONG, _SHORT):
        return POSITION_SIDE_UNREADABLE
    leg = _norm_side(leg_side)
    if leg not in (_LONG, _SHORT):
        return LEG_SIDE_UNREADABLE
    return LEG_REDUCES_OTHER_BOOK if leg == pos else LEG_REDUCES_GRADED_BOOK


def other_book_state(position_idx: Any) -> str:
    """Could an opposite book exist for the symbol we graded?

    Read from the venue's own ``positionIdx``: ``0`` is one-way netting (there
    is exactly one book, so no opposite book can exist and a leg acting on one
    is stranded by construction); ``1``/``2`` are the hedge books, where the
    sibling may be a LIVE position whose protection must be preserved.

    ⚠️ ``None`` / unparseable is ``OTHER_BOOK_UNKNOWN`` — *we could not look* —
    and must never be defaulted to ``0``/one-way. ``CLAUDE.md`` states that
    exact hazard for this same venue field: "defaulting an unread mode to the
    netting value is precisely the reading that would make a hedge account look
    safe to treat as netted."
    """
    raw = str(position_idx if position_idx is not None else "").strip()
    if not raw.lstrip("-").isdigit():
        return OTHER_BOOK_UNKNOWN
    idx = int(raw)
    if idx == 0:
        return OTHER_BOOK_IMPOSSIBLE_ONE_WAY
    if idx in (1, 2):
        return OTHER_BOOK_POSSIBLE_HEDGE
    return OTHER_BOOK_UNKNOWN


def split_legs_by_side(
    position_side: Any,
    legs: Sequence[Dict[str, Any]],
    *,
    qty_of: Callable[[Dict[str, Any]], Optional[float]],
    side_key: str = "side",
    position_idx: Any = None,
) -> Dict[str, Any]:
    """Split resting protective legs into the four side classes.

    *qty_of* is injected rather than re-implemented so this shares
    ``order_monitor._bybit_sl_leg_qty``'s exact parsing — two copies of "what
    qty does this leg close" is how the sum and the split would drift.

    Returns a dict carrying, per class, the LEG COUNT and the summed QTY, plus
    ``qty_unreadable_legs``: legs whose side graded fine but whose qty did not.
    ⚠️ Those legs contribute **0.0** to their class's qty sum, so a sum must
    never be read without its ``*_qty_unreadable`` count beside it — a total
    over an incompletely-parsed population is a lower bound, not a measurement.
    """
    out: Dict[str, Any] = {
        "other_book_state": other_book_state(position_idx),
        "leg_states": [],
    }
    for state in LEG_SIDE_STATES:
        out[f"{state}_legs"] = 0
        out[f"{state}_qty"] = 0.0
        out[f"{state}_qty_unreadable"] = 0
    unreadable_total = 0

    for leg in legs or ():
        state = classify_leg_side(position_side, leg.get(side_key))
        out["leg_states"].append(state)
        out[f"{state}_legs"] += 1
        q = qty_of(leg)
        if q is None:
            out[f"{state}_qty_unreadable"] += 1
            unreadable_total += 1
            continue
        out[f"{state}_qty"] += float(q)

    out["qty_unreadable_legs"] = unreadable_total
    out["legs_seen"] = len(out["leg_states"])
    return out
