"""Did this close actually reach the trade's DECLARED bracket?

The third member of the provenance family, and the one that answers the
operator's own question — *"are trades ending at their brackets?"*
:mod:`src.runtime.provenance` grades a row's ``pnl``;
:mod:`src.runtime.r_provenance` grades the risk R was divided by; this grades
the **exit LABEL** by re-deriving it from the price that was actually recorded.

WHY IT EXISTS
-------------
``trades.exit_reason`` is written ONCE, at close time, and for the largest
close path that is the one moment the answer cannot be known.
``order_monitor._close_trade_from_order_status``'s no-record fallback
hard-codes ``exit_reason='reconciler_filled'`` with ``exit_price`` still NULL —
correctly, because no price exists yet. ``_sweep_pending_pnl_from_bybit``
re-runs the classifier when the price later arrives (#10262,
BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE), but that is ONE path,
FORWARD only: every row closed before it shipped, and every row closed by a
path it does not cover, still carries the label it was born with.

⚠️ **THIS MODULE WRITES NOTHING.** It re-derives the verdict on the READ path,
beside the stored label, so a reader can see both. Rewriting ``exit_reason`` in
the journal is a Tier-2/3 writer change and is deliberately not done here — and
the label a producer AUTHORED (``vwap_cross``, ``pairs_stop``, ``exit_head``)
is a real record of a real decision that a re-derivation must not overwrite.

MEASURED — the size of the gap
------------------------------
Live journal pulled 2026-09-06 via ``/api/bot/db/table/{trades,order_packages}``
(5518 + 4435 rows). Population: ``status='closed'``, non-backtest,
``pnl NOT NULL``, minus ``orphan_adopt`` / ``superseded`` /
``exchange_reset_flat`` — **n = 1287**.

* the STORED label reads ``sl`` or ``tp`` on **251 of 1287 (19.5%)**;
* re-derived, of the **1125** rows that could be graded, **430 (38.2%)** reached
  a declared bracket (345 sl · 85 tp) and 695 (61.8%) ended mid-bracket.

On the LAST 200 closes the stored label reads ``sl|tp`` on **27 (13.5%)** while
the re-derivation finds **46 (23.0%)**. The label understates bracket exits by
roughly a factor of two, in both windows.

THE STATES — seven, never collapsed
-----------------------------------
``reached_sl`` / ``reached_tp``
    The recorded exit price is at or through a declared level. The inequality
    is CONSERVATIVE and deliberately identical to
    ``order_monitor._classify_broker_exit``'s (``<=`` / ``>=``, not ``==``,
    because fills slip through a level), so a mid-range flatten can never be
    mislabelled as a bracket hit.

``mid_bracket``
    We LOOKED, on a measurable price, against a real bracket, and the price sat
    between the levels. A genuine non-bracket close. ⚠️ This is an OUTCOME, not
    a defect: many legs (``vwap_cross``, ``exit_head``, ``time_decay``) exit on
    purpose before a bracket, and reading this bucket as failure would be as
    wrong as reading the stored label as truth.

``no_exit_price``
    **We could not look** — no price was recorded.

``price_not_measurable``
    **REFUSED.** A price exists and is FABRICATED (``local_markprice`` is the
    market at SWEEP time, hours after the exit; ``netted_duplicate_unattributed``
    is one record's magnitude copied onto N rows). Comparing either against a
    bracket would manufacture a verdict out of unrelated price action. The same
    refusal ``provenance.EXIT_LABEL_REFUSED_UNMEASURED`` already makes on the
    writer side — imported, never re-spelled.

``no_bracket_record``
    A price, but no declared ``sl``/``tp`` to compare it against. Not a
    mid-bracket close: there was no bracket.

``direction_unreadable``
    **We could not look** — without a side, ``<=`` and ``>=`` are meaningless.

``excluded_reduce_leg``
    An ``intent_reduce`` leg's bracket is INVERTED relative to its own order
    direction (its SL/TP are the ORIGINAL position's, its direction is the
    closing side), so grading it would mislabel a deliberate partial close as a
    bracket hit. The exclusion ``_classify_broker_exit`` already makes.

The seven partition the population, so a consumer can check the split with
arithmetic rather than trusting it.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Tuple

from src.runtime.provenance import FABRICATED_SOURCES

BRACKET_REACHED_SL = "reached_sl"
BRACKET_REACHED_TP = "reached_tp"
BRACKET_MID = "mid_bracket"
BRACKET_NO_EXIT_PRICE = "no_exit_price"
BRACKET_PRICE_NOT_MEASURABLE = "price_not_measurable"
BRACKET_NO_RECORD = "no_bracket_record"
BRACKET_DIRECTION_UNREADABLE = "direction_unreadable"
BRACKET_EXCLUDED_REDUCE_LEG = "excluded_reduce_leg"

BRACKET_STATES = (
    BRACKET_REACHED_SL,
    BRACKET_REACHED_TP,
    BRACKET_MID,
    BRACKET_NO_EXIT_PRICE,
    BRACKET_PRICE_NOT_MEASURABLE,
    BRACKET_NO_RECORD,
    BRACKET_DIRECTION_UNREADABLE,
    BRACKET_EXCLUDED_REDUCE_LEG,
)

#: The states on which a bracket-vs-other RATIO has a meaningful denominator.
#: Everything else is *we could not look* / *we refused* / *not applicable*, and
#: folding those into the denominator would publish a rate over a population it
#: does not describe.
GRADEABLE_STATES = (BRACKET_REACHED_SL, BRACKET_REACHED_TP, BRACKET_MID)

_LONG = ("buy", "long")
_SHORT = ("sell", "short")


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _notes(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


def is_reduce_leg(row: Mapping) -> bool:
    """Mirrors ``order_monitor``'s own ``is_reduce_leg`` test: the ``setup_type``
    column OR the ``notes.intent_reduce`` flag, since a reattached row can carry
    the flag without the setup_type."""
    if str(row.get("setup_type") or "").strip().lower() == "intent_reduce":
        return True
    return bool(_notes(row.get("notes")).get("intent_reduce"))


def classify_bracket_outcome(
    row: Mapping, package_sl: Any = None, package_tp: Any = None,
) -> Tuple[str, Optional[str]]:
    """Grade one closed row. Returns ``(state, verdict)``.

    ``verdict`` is ``"sl"`` / ``"tp"`` / ``"other"`` on a GRADEABLE row and
    ``None`` on every other state — so a caller cannot accidentally read *we
    could not look* as *it did not reach its bracket*, which is the entire
    point of separating them.

    Row keys consulted: ``direction``, ``exit_price``, ``setup_type``,
    ``notes`` (for ``exit_price_source`` + ``intent_reduce``). The bracket
    levels come from the trade's ``order_packages`` row and are passed in
    rather than looked up, so this stays a pure function that can be argued
    about in tests instead of against a live position.
    """
    if is_reduce_leg(row):
        return BRACKET_EXCLUDED_REDUCE_LEG, None

    price = _num(row.get("exit_price"))
    if price is None or price <= 0:
        return BRACKET_NO_EXIT_PRICE, None

    source = str(_notes(row.get("notes")).get("exit_price_source") or "")
    if source in FABRICATED_SOURCES:
        return BRACKET_PRICE_NOT_MEASURABLE, None

    direction = str(row.get("direction") or "").strip().lower()
    if direction in _LONG:
        long_side = True
    elif direction in _SHORT:
        long_side = False
    else:
        return BRACKET_DIRECTION_UNREADABLE, None

    sl = _num(package_sl)
    tp = _num(package_tp)
    sl = sl if (sl is not None and sl > 0) else None
    tp = tp if (tp is not None and tp > 0) else None
    if sl is None and tp is None:
        return BRACKET_NO_RECORD, None

    # CONSERVATIVE inequality, identical to
    # `order_monitor._classify_broker_exit`: fills slip THROUGH a level, so
    # `<=` / `>=` rather than `==`; anything strictly between the two levels is
    # a genuine non-bracket close.
    if long_side:
        if sl is not None and price <= sl:
            return BRACKET_REACHED_SL, "sl"
        if tp is not None and price >= tp:
            return BRACKET_REACHED_TP, "tp"
    else:
        if sl is not None and price >= sl:
            return BRACKET_REACHED_SL, "sl"
        if tp is not None and price <= tp:
            return BRACKET_REACHED_TP, "tp"
    return BRACKET_MID, "other"


def empty_bracket_counts() -> "dict[str, int]":
    """A zeroed count for EVERY state — explicit zeros, never a missing key."""
    return {state: 0 for state in BRACKET_STATES}
