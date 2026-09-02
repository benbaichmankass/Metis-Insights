"""WHICH coverage figure may decide a Bybit re-arm — and on which accounts.

``_check_broker_naked_bybit_positions`` asks one question per open position:
*is enough protective quantity resting to cover this position?* Until
2026-09-02 it answered with ``_bybit_position_protection``'s ``covered_qty``,
which sums **every** resting SL leg on the symbol with no reference to the
leg's side. That was sound while every symbol was one-way netting — one book,
so every leg was the graded book's.

Since HEDGE mode was armed on ``bybit_1``/``bybit_2`` (2026-08-30,
``BYBIT_HEDGE_MODE_SYMBOLS``) a symbol can carry legs for **two** books in that
one sum. A protective leg is reduce-only, so it acts on the book it can SHRINK
— and an OTHER-book leg can therefore push the side-blind total past ``size``
on a position whose own stop is gone, at which point the sweep skips a
genuinely naked position as "fully covered".

⚠️ **CONSTRUCTED FROM THE LIVE 2026-09-02T03:30:33Z READ — n = 1, AND NO LIVE
INSTANCE OF THE MASKING HAS BEEN OBSERVED.** ``bybit_1``/BTCUSDT held
``Buy 0.018 positionIdx=1`` with its own ``Sell 0.018`` SL plus a ``Buy 0.46``
SL on the other book. Had the ``Sell 0.018`` leg been lost, ``covered_qty``
would still have read ``0.46 >= 0.018``. That is the shape. It is a
construction over a real venue reading, not a sighting, and this module's
docstrings say so wherever the claim is repeated.

WHY THIS MODULE EXISTS RATHER THAN A BARE CODE CHANGE
-----------------------------------------------------
The operator's Tier-2 decision (2026-09-02) was **stage it on ``bybit_1``
(demo) first**, explicitly accepting that ``bybit_2`` (real money) stays
exposed to the masking during the soak and that demo may never produce the
triggering collision. "Stage it on bybit_1" has to be a PROPERTY OF THE SYSTEM
rather than a sentence in a document, which is the same argument
``stray_oca_groups.account_may_apply`` makes for its own allowlist.

The decision is a **pure function** so the policy is arguable in tests rather
than against a live position — the lesson of
``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``.

THE TWO KNOBS
-------------
``BYBIT_GRADED_COVERAGE_MODE`` ∈ ``off`` / ``annotate`` (default) / ``apply``
and ``BYBIT_GRADED_COVERAGE_ACCOUNTS`` (CSV). A ``*_MODE`` knob, **not** a
default-off ``*_ENABLED`` gate (Prime Directive), and an unparseable value
falls back to ``annotate`` rather than to ``off`` or ``apply``: a typo must not
silently switch the observation off, and must certainly not switch a live
order path on.

⚠️ **AN EMPTY ALLOWLIST MEANS *NONE*, DELIBERATELY THE OPPOSITE OF
``CONVICTION_SIZING_ACCOUNTS`` / ``NETTING_ATTRIBUTION_ACCOUNTS``.** Those
widen a size and a DB write and read empty as ALL — which ``CLAUDE.md`` already
calls out for what it is: *"an empty allowlist is not a safe default, it is the
widest one"*. This one decides whether a live position gets a protective stop
CANCELLED-AND-RE-PLACED, so an unset variable must not arm it everywhere. It
copies ``PROTECTION_REASSERT_ACCOUNTS`` / ``PROTECTION_STRAY_GROUP_ACCOUNTS``
on purpose. **Do not "harmonise" it back.**

⚠️ **THE ALLOWLIST SCOPES THE BINDING, NEVER THE MEASUREMENT.** Every Bybit
account is graded and annotated to ``bybit_coverage_soak`` regardless, so the
rows a reviewer needs *before widening* actually exist. This is the exact
correction ``NETTING_ATTRIBUTION_ACCOUNTS`` needed on 2026-08-09, where
intersecting the account set at the top of the pass made the account being
staged TOWARD invisible — a staging control that disables measurement of the
thing you are staging toward is self-defeating.

⚠️ **``covered_qty`` ITSELF IS UNCHANGED AND STAYS SIDE-BLIND.** It feeds the
over-cover TRIP, which is the UNION of same-book pile-up AND other-book legs
resting on the symbol; narrowing that to the graded book would make the second
condition stop tripping and go SILENT — worse than the mislabelling #10739
fixed. Only the *coverage/re-arm* comparison is scoped here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# --- the mode vocabulary ----------------------------------------------------
MODE_OFF = "off"
MODE_ANNOTATE = "annotate"
MODE_APPLY = "apply"
_MODES = (MODE_OFF, MODE_ANNOTATE, MODE_APPLY)

# --- why the EFFECTIVE mode differs from the requested one, never collapsed --
SCOPE_ALLOWLISTED = "allowlisted"          # apply was asked for AND permitted
SCOPE_NOT_ALLOWLISTED = "not_allowlisted"  # apply was asked for and HELD BACK
SCOPE_NOT_APPLY = "not_apply"              # apply was never asked for

# --- WHICH figure actually decided this row ---------------------------------
BASIS_GRADED = "graded"          # the graded-book qty bound the decision
BASIS_SIDE_BLIND = "side_blind"  # the pre-2026-09-02 side-blind sum bound it

#: The gate was OFF, so nothing was graded. ⚠️ Emphatically NOT one of
#: ``bybit_leg_sides``' ungraded tokens, which mean *we tried and could not*.
#: "We did not look because we were switched off" and "we looked and failed"
#: are different facts and a reader chasing one must not find the other.
COVERAGE_NOT_COMPUTED = "not_computed"

# --- what the coverage comparison concluded, per basis -----------------------
VERDICT_COVERED = "covered"
VERDICT_UNCOVERED = "uncovered"

# --- the headline outcome of the decision -----------------------------------
DECISION_SKIP_COVERED = "skip_covered"            # enough rests; do nothing
DECISION_REARM_INDICATED = "rearm_indicated"      # a hole was measured
DECISION_REFUSED_UNGRADEABLE = "refused_ungradeable"  # we could not look

__all__ = [
    "MODE_OFF", "MODE_ANNOTATE", "MODE_APPLY",
    "SCOPE_ALLOWLISTED", "SCOPE_NOT_ALLOWLISTED", "SCOPE_NOT_APPLY",
    "BASIS_GRADED", "BASIS_SIDE_BLIND",
    "COVERAGE_NOT_COMPUTED",
    "VERDICT_COVERED", "VERDICT_UNCOVERED",
    "DECISION_SKIP_COVERED", "DECISION_REARM_INDICATED",
    "DECISION_REFUSED_UNGRADEABLE",
    "resolve_mode", "account_may_apply", "effective_mode", "coverage_decision",
]


def resolve_mode(raw: Any) -> str:
    """``off`` / ``annotate`` / ``apply``; anything unrecognised → ``annotate``.

    Falls back to ``annotate`` rather than ``off`` or ``apply`` for the reason
    ``stray_oca_groups.resolve_mode`` gives: a typo must not silently switch
    the observation off, and must certainly not switch a live order path on.
    """
    text = str(raw or "").strip().lower()
    return text if text in _MODES else MODE_ANNOTATE


def account_may_apply(account_id: Any, allowlist_raw: Any) -> bool:
    """May the GRADED figure bind the re-arm decision on this account?

    ⚠️ An empty / unset allowlist returns **False for every account** — see the
    module docstring. An unknown or absent ``account_id`` also returns False:
    we cannot show the account is allowlisted, and the fail-safe direction for
    a live order-path change is to decline.
    """
    account_id = str(account_id or "").strip()
    allow = {
        part.strip() for part in str(allowlist_raw or "").split(",")
        if part.strip()
    }
    return bool(account_id) and account_id in allow


def effective_mode(
    global_mode: Any, account_id: Any, allowlist_raw: Any,
) -> "tuple[str, str]":
    """``(effective_mode, apply_scope)`` for one account.

    ``apply_scope`` is never collapsed, so a held-back row can never read as an
    applied one: ``not_apply`` (apply was never requested) and
    ``not_allowlisted`` (it was requested and this account is staged out) both
    yield an effective ``annotate`` and mean entirely different things about
    the operator's intent.
    """
    mode = resolve_mode(global_mode)
    if mode != MODE_APPLY:
        return mode, SCOPE_NOT_APPLY
    if account_may_apply(account_id, allowlist_raw):
        return MODE_APPLY, SCOPE_ALLOWLISTED
    return MODE_ANNOTATE, SCOPE_NOT_ALLOWLISTED


def _verdict(qty: Optional[float], size: float, eps: float) -> Optional[str]:
    if qty is None:
        return None
    return VERDICT_COVERED if qty + eps >= size else VERDICT_UNCOVERED


def coverage_decision(
    *,
    global_mode: Any,
    account_id: Any,
    allowlist_raw: Any,
    size: float,
    eps: float,
    side_blind_qty: float,
    graded_qty: Optional[float],
    coverage_state: str,
    source: Any = None,
    symbol: Any = None,
) -> Dict[str, Any]:
    """Decide which figure binds, and what both figures would have concluded.

    *graded_qty* is ``None`` whenever the graded book could not be measured —
    either because the gate is ``off`` (*coverage_state*
    :data:`COVERAGE_NOT_COMPUTED`) or because ``bybit_leg_sides``' split was
    incomplete (one of its four ungraded tokens). Those are different facts and
    the token is what separates them; the ``None`` alone does not.

    Returns a dict carrying, at minimum:

    ``bound_qty``
        The figure the caller must compare against ``size``. **``None`` means
        REFUSE** — do not re-arm, and do not bank the side-blind sum as
        coverage either. Only reachable when the graded basis is BINDING and
        the split was ungradeable.
    ``basis``
        :data:`BASIS_GRADED` or :data:`BASIS_SIDE_BLIND` — which figure that
        was, so a soak row can never leave a reader guessing.
    ``verdicts_differ``
        ⚠️ **THE FIELD A REVIEWER READS BEFORE WIDENING THE ALLOWLIST.** True
        when the two bases reach OPPOSITE covered/uncovered conclusions on this
        row, i.e. arming would actually have changed the outcome. ``None`` when
        only one verdict exists, which is *we could not compare*, never
        *they agree*. It is computed on every graded row regardless of basis,
        because it is the MEASUREMENT and the allowlist scopes only the
        BINDING.

    ⚠️ **AN UNGRADEABLE SPLIT REFUSES ONLY WHERE THE GRADED BASIS BINDS.** On a
    held-back or ``annotate`` account the caller's behaviour must stay
    byte-identical to before this gate existed — introducing a new refusal
    there would make ``annotate`` change live behaviour, which is precisely
    what ``annotate`` promises not to do. The condition is still recorded.
    """
    mode, scope = effective_mode(global_mode, account_id, allowlist_raw)
    binding = mode == MODE_APPLY

    side_blind_verdict = _verdict(float(side_blind_qty), size, eps)
    graded_verdict = _verdict(
        None if graded_qty is None else float(graded_qty), size, eps)
    differ = (
        None if graded_verdict is None
        else bool(graded_verdict != side_blind_verdict)
    )

    if binding:
        basis = BASIS_GRADED
        bound: Optional[float] = (
            None if graded_qty is None else float(graded_qty))
    else:
        basis = BASIS_SIDE_BLIND
        bound = float(side_blind_qty)

    if bound is None:
        decision = DECISION_REFUSED_UNGRADEABLE
    elif bound + eps >= size:
        decision = DECISION_SKIP_COVERED
    else:
        decision = DECISION_REARM_INDICATED

    return {
        "account_id": None if account_id is None else str(account_id),
        "symbol": None if symbol is None else str(symbol),
        "source": None if source is None else str(source),
        # `mode` governed THIS row; `global_mode` is what was requested;
        # `apply_scope` says why they differ.
        "mode": mode,
        "global_mode": resolve_mode(global_mode),
        "apply_scope": scope,
        "basis": basis,
        "binding": binding,
        "coverage_state": str(coverage_state),
        "position_size": float(size),
        "eps": float(eps),
        # ⚠️ `graded_qty` is None when unmeasured, NEVER 0.0 — zero here is a
        # real and serious reading (nothing protects this book).
        "side_blind_qty": float(side_blind_qty),
        "graded_qty": None if graded_qty is None else float(graded_qty),
        "bound_qty": bound,
        "verdict_side_blind": side_blind_verdict,
        "verdict_graded": graded_verdict,
        "verdicts_differ": differ,
        "decision": decision,
    }
