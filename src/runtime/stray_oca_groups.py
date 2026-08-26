"""Which resting IB protective groups on a symbol are STRAYS this trade should clear?

THE DEFECT THIS EXISTS FOR, captured live 2026-08-26T02:08:35Z on ``ib_paper``/MHG.
``place_protective`` scopes its pre-cancel **by NAME** to ``oca-protect-t<oca_key>``
(``ib_client._cancel_oca_group_for_symbol`` skips any leg whose ``ocaGroup`` differs).
So when a trade's PRIOR protection rests under a *different* name — a legacy
``oca-protect-<reqId>`` group, or the bare-numeric form ``834864174`` measured on MGC
the same day — that prior group is never a cancellation candidate, and the re-arm mints
a SECOND group beside it::

    02:08:35,505  mhg_pullback_1d verdict={'sl': 6.31757143}      <- a trailing amend
    02:08:35,761  placeOrder StopOrder(473) + LimitOrder(474)
    02:08:35,761  modify -> {'ocaGroup': 'oca-protect-t4796', 'legs_placed': 2}
    02:09:36,322  STOP OVER-COVER ... 87.0 (300%) across 3 groups
                  ['oca-protect-446', 'oca-protect-465', 'oca-protect-t4796']

⚠️ **THIS IS NOT THE MECHANISM `BL-20260825-PLACE-PROTECTIVE-COUNTS-THE-CANCEL-CALL-NOT-ITS-EFFECT`
DESCRIBES, AND MUST NOT BE FOLDED INTO IT.** That row is about the *symbol-wide fallback*
(no ``oca_key`` → IBKR Error 10147 on foreign legs → mint). All three of its defects are
already fixed in ``b81458a4`` (error capture, effect verification, survivor-join), and
that sha is an ancestor of the deployed code. In the capture above there is **no**
``no oca_key`` warning, **no** 10147 and **no** survivor-join log: the keyed path ran
normally and the stray was invisible to it. Two different bugs, one symptom.

⚠️ **IT MINTS ONCE PER TRADE, AT THE LEGACY→KEYED TRANSITION — SO IT IS FINITE.**
Every trade opened since the keyed path shipped is keyed from the start and never
transitions. An earlier reading of this as an unbounded generator was wrong and is
recorded here so it is not carried forward.

WHY THE RULE IS "CANCEL NON-KEYED GROUPS" AND NOT "CANCEL EVERY OTHER GROUP".
IB nets per contract per account, so one symbol can legitimately host N protective
groups — one per open journal trade. A symbol-wide cancel from ONE trade's trailing
amend would strip a SIBLING's resting take-profit, which is
``BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY`` and a bigger blast radius than the
bug being fixed. A **keyed** group carries its owning trade id in its own name, so a
sibling's group is self-identifying and is preserved by construction, with no need to
read the journal from the order path. A **non-keyed** group has no owning trade
identity by construction — it predates the keyed path — so it is exactly the stray set.

FIVE STATES, NEVER COLLAPSED. In particular ``ungrouped`` is NOT ``stray``: a resting
leg carrying no ``ocaGroup`` at all cannot be shown to be this trade's abandoned
protection, and cancelling it could strip a hand-placed exit or a non-protective
working order. It is REPORTED and left alone — the same refusal ``attach-ib-target``
makes when a non-protective order rests on the symbol.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

KEYED_PREFIX = "oca-protect-t"

#: Never collapsed — see the module docstring.
KEEP_TARGET = "keep_target"        # the group this re-arm is (re)placing
SIBLING_KEYED = "sibling_keyed"    # another trade's keyed group — PRESERVE
STRAY_UNKEYED = "stray_unkeyed"    # no owning trade identity — the finding
UNGROUPED = "ungrouped"            # no ocaGroup at all — REPORT, never cancel
NOT_PROTECTIVE = "not_protective"  # not a stop/target leg — leave alone

MODE_OFF = "off"
MODE_ANNOTATE = "annotate"
MODE_APPLY = "apply"
_MODES = (MODE_OFF, MODE_ANNOTATE, MODE_APPLY)


def resolve_mode(raw: Any) -> str:
    """``off`` / ``annotate`` / ``apply``; anything unrecognised → ``annotate``.

    A ``*_MODE`` knob, NOT a default-off ``*_ENABLED`` gate — the Prime Directive
    shape shared with ``PROTECTION_REASSERT_MODE`` / ``NETTING_ATTRIBUTION_MODE``.
    An unparseable value falls back to ``annotate`` rather than to ``off`` or
    ``apply``: a typo must not silently switch the observation off, and must
    certainly not switch a live order path on.
    """
    text = str(raw or "").strip().lower()
    return text if text in _MODES else MODE_ANNOTATE


def is_keyed_group(name: Any) -> bool:
    """Does *name* carry an owning trade id in the keyed form?

    ``oca-protect-t4796`` → True. ``oca-protect-465`` (legacy reqId form) → False.
    ``834864174`` (the bare-numeric form measured on MGC) → False. A bare
    ``oca-protect-t`` with no key after it → **False**: an empty key identifies no
    trade, so treating it as keyed would preserve a group nothing owns.
    """
    text = str(name or "").strip()
    return text.startswith(KEYED_PREFIX) and len(text) > len(KEYED_PREFIX)


def classify_leg(
    leg: Dict[str, Any],
    keep_group: Any,
    protective_side: Optional[str],
) -> str:
    """Classify ONE resting leg against the group this re-arm is placing.

    *protective_side* is the caller's own ``stop``/``target``/``None`` verdict —
    passed in rather than re-derived so this module and
    ``ib_client._protective_leg_side`` can never drift into two answers about
    what counts as protective (the ``_regime_score_semantics`` discipline).
    """
    if protective_side not in ("stop", "target"):
        return NOT_PROTECTIVE
    group = str(leg.get("oca_group") or "").strip()
    if not group:
        return UNGROUPED
    if group == str(keep_group or "").strip():
        return KEEP_TARGET
    return SIBLING_KEYED if is_keyed_group(group) else STRAY_UNKEYED


def plan_stray_cancels(
    legs: Iterable[Dict[str, Any]],
    keep_group: Any,
    side_of: Any,
) -> Dict[str, Any]:
    """Decide which legs a keyed re-arm should ALSO cancel. Pure — touches nothing.

    *side_of* is a callable mapping a leg's ``order_type`` to
    ``stop``/``target``/``None`` (i.e. ``ib_client._protective_leg_side``).

    Returns ``{"cancel": [...], "by_state": {...}, "stray_groups": [...],
    "preserved_groups": [...], "ungrouped_seen": N}``. ``cancel`` is empty when
    *keep_group* is falsy — a re-arm with no keyed group of its own is the
    symbol-wide fallback path, which is a different code path with a different
    (and already-documented) hazard, and must not be widened from here.
    """
    keep = str(keep_group or "").strip()
    cancel: List[Dict[str, Any]] = []
    by_state: Dict[str, int] = {}
    stray_groups: List[str] = []
    preserved: List[str] = []
    ungrouped_seen = 0

    for leg in legs or []:
        state = classify_leg(leg, keep, side_of(leg.get("order_type")))
        by_state[state] = by_state.get(state, 0) + 1
        group = str(leg.get("oca_group") or "").strip()
        if state == UNGROUPED:
            ungrouped_seen += 1
        elif state == SIBLING_KEYED and group not in preserved:
            preserved.append(group)
        elif state == STRAY_UNKEYED:
            if group not in stray_groups:
                stray_groups.append(group)
            if keep:
                cancel.append(leg)

    return {
        "cancel": cancel,
        "by_state": by_state,
        "stray_groups": stray_groups,
        "preserved_groups": preserved,
        "ungrouped_seen": ungrouped_seen,
        "keep_group": keep,
    }
