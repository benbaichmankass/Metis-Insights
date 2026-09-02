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

⚠️ **THIS MODULE STILL CANCELS NOTHING AND PLACES NOTHING** — it classifies,
and every function here is pure. But it is **no longer read only by a page.**

⚠️ **THE PARAGRAPH THAT STOOD HERE UNTIL 2026-09-02 SAID THE OPPOSITE AND MUST
NOT BE RE-QUOTED.** It read: *"The caller's re-arm decision still reads the
unchanged side-blind ``covered_qty`` … so landing this changes no order-path
behaviour — only what the page SAYS. That the side-blind sum can also mask a
genuinely under-covered book is a real and separate defect; it is named in the
PR body and is a Tier-2 change, not enacted here."* That was true of the
diagnostic repair (#10739) and became false the moment the separate Tier-2
change it named was made. :func:`graded_book_coverage` is what the naked
sweep's **RE-ARM DECISION** reads (``order_monitor``
``_check_broker_naked_bybit_positions``), so a classification error here can
change which live positions get a protective stop re-armed.

⚠️ **BUT ONLY ON AN ALLOWLISTED ACCOUNT, AND THE SHIPPED ALLOWLIST IS EMPTY.**
The operator's Tier-2 decision (2026-09-02) was to STAGE that basis on
``bybit_1`` (demo) first, so the binding is gated by
``BYBIT_GRADED_COVERAGE_MODE`` + ``BYBIT_GRADED_COVERAGE_ACCOUNTS``
(:mod:`src.runtime.bybit_coverage_basis`), where an **empty allowlist means
NONE**. On every other account the sweep still compares the side-blind sum,
byte-identically to before. So: this module is *capable* of deciding a re-arm,
and whether it does on any given account is an operational fact readable with
``get-env`` — never inferable from this docstring. ⚠️ The grading itself runs
on **every** Bybit account regardless, because the allowlist scopes the
BINDING, never the MEASUREMENT.

The **over-cover TRIP threshold is deliberately still side-blind** and reads
``covered_qty``: that check is the UNION of two conditions — genuine same-book
pile-up AND other-book legs resting on the symbol — and narrowing it to the
graded book would make the second case stop tripping and go SILENT, which is
worse than the mislabelling #10739 fixed. Only the *coverage* comparison moved.

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
    "COVERAGE_GRADED",
    "COVERAGE_UNGRADED_NO_SPLIT",
    "COVERAGE_UNGRADED_POSITION_SIDE",
    "COVERAGE_UNGRADED_LEG_SIDE",
    "COVERAGE_UNGRADED_LEG_QTY",
    "graded_book_coverage",
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


# --- coverage of the GRADED book, for the RE-ARM decision -------------------
#
# ⚠️ THESE FOUR TOKENS ARE A LOG/DIAGNOSTIC VOCABULARY, NOT A DECLARED
# `collapsed-state-guard` CONTRACT, and that is deliberate. The load-bearing
# distinction — *we could not grade this* versus *we graded it and nothing
# covers the book* — is carried by `qty is None` versus `qty == 0.0`, and the
# thing that PRODUCES it is `leg_side_class`, which IS a declared contract
# (`scripts/ci/check_collapsed_states.py`). Registering a second vocabulary
# over the same underlying four states would need a real consumer branch per
# token; the caller legitimately takes ONE branch on `qty is None` and prints
# the token, so declaring it would buy a decorative branch — the failure that
# guard exists to prevent, not the one it exists to catch.
COVERAGE_GRADED = "graded"
COVERAGE_UNGRADED_NO_SPLIT = "no_split"
COVERAGE_UNGRADED_POSITION_SIDE = "position_side_ungraded"
COVERAGE_UNGRADED_LEG_SIDE = "leg_side_ungraded"
COVERAGE_UNGRADED_LEG_QTY = "leg_qty_ungraded"


def graded_book_coverage(split: Any):
    """Qty of resting protection acting on the GRADED book — or a refusal.

    Returns ``(qty, reason)``:

    * ``(float, COVERAGE_GRADED)`` — every leg on the symbol was classified AND
      its qty parsed, so the float is a MEASUREMENT of how much protection acts
      on the position we graded. ``0.0`` here is a real reading (nothing
      protects this book), never *we could not look*.
    * ``(None, <one of the four ungraded tokens>)`` — REFUSE. Where this figure
      is BINDING the caller must not re-arm, and must not skip on the strength
      of the side-blind sum either; it says so and moves on. Where it is not
      binding (an account outside ``BYBIT_GRADED_COVERAGE_ACCOUNTS``, or a
      mode below ``apply``) the caller RECORDS the refusal and otherwise
      behaves exactly as it did before this function existed — an ``annotate``
      mode that introduced a new refusal would not be an annotation.

    **Why the caller needs this and not ``covered_qty``** (2026-09-02). A
    protective leg is reduce-only, so it acts on the book it can SHRINK.
    ``order_monitor._bybit_position_protection`` sums EVERY resting SL leg on
    the symbol into one side-blind ``covered_qty``, and the naked sweep then
    decides ``if covered + eps >= size: continue``. Under one-way netting that
    was sound — one book, so every leg was the graded book's. Since HEDGE mode
    was armed on ``bybit_1``/``bybit_2`` (2026-08-30,
    ``BYBIT_HEDGE_MODE_SYMBOLS``) a symbol can carry legs for TWO books in that
    one sum, so an OTHER-book leg can push the total past ``size`` on a
    position whose own stop is gone — and the sweep skips a genuinely naked
    position as "fully covered".

    ⚠️ CONSTRUCTED FROM THE LIVE 2026-09-02T03:30:33Z READ, **n = 1, and NO
    LIVE INSTANCE OF THE MASKING HAS BEEN OBSERVED.** ``bybit_1``/BTCUSDT held
    ``Buy 0.018 positionIdx=1`` with its own ``Sell 0.018`` SL plus a
    ``Buy 0.46`` SL on the other book. Had the ``Sell 0.018`` leg been lost,
    ``covered_qty`` would still have read ``0.46 >= 0.018`` and the sweep would
    NOT have re-armed a naked long. That is the shape; it is a construction
    over a real venue reading, not a sighting.

    ⚠️ **REFUSAL IS ALL-OR-NOTHING, mirroring the caller's existing
    ``unknown_qty_sl_legs`` guard.** ONE ungradeable leg leaves the graded sum
    a LOWER BOUND, and a lower bound compared against ``size`` under-reports
    coverage — which drives a re-arm, i.e. a live order-path mutation, on a
    position that may already be protected. So a partial read refuses outright
    rather than grading the part it could see.
    """
    if not isinstance(split, dict):
        # No split at all — the legs were never read. Emphatically not "no leg
        # covers this book"; that is a measurement nobody took.
        return None, COVERAGE_UNGRADED_NO_SPLIT
    if int(split.get(f"{POSITION_SIDE_UNREADABLE}_legs") or 0):
        # NOTHING on the symbol is gradeable — with no position side there is
        # no "opposite" to compare a leg against. Reported ahead of the
        # per-leg reason because it points at the other half of the read.
        return None, COVERAGE_UNGRADED_POSITION_SIDE
    if int(split.get(f"{LEG_SIDE_UNREADABLE}_legs") or 0):
        return None, COVERAGE_UNGRADED_LEG_SIDE
    unreadable_qty = int(split.get("qty_unreadable_legs") or 0)
    if unreadable_qty:
        # Defence in depth: `_bybit_position_protection`'s own
        # `unknown_qty_sl_legs` guard already refuses this upstream. Repeated
        # here so the accessor is safe on its own terms — a caller that reached
        # it by another route must not be handed a lower bound as a total.
        return None, COVERAGE_UNGRADED_LEG_QTY
    return float(split.get(f"{LEG_REDUCES_GRADED_BOOK}_qty") or 0.0), COVERAGE_GRADED
