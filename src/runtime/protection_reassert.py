"""Should a diverged protective leg be RE-ASSERTED at its declared level?

WHY THIS EXISTS — the capability, not the one repair
====================================================
Operator directive 2026-08-23: *"we need to be able to adjust trades on IB
without disconnecting the integration, this is an important pipeline
achievement and we can't leave it to chance because of one trade not being
worth the effort."*

The mechanism to amend already exists — :meth:`IBClient.modify_protective`
runs on the trader's OWN client, so it needs no ops clientId and evicts
nothing. What was missing is the TRIGGER, and the reason it was missing is
structural: ``interpret_verdict`` is passed ``current_sl=open_pkg.get("sl")``
— the JOURNAL. The venue's resting price is never read on that path. So the
strategy recomputes its level, the journal already says that level, the filter
drops it as ``no_meaningful_change``, and nothing is sent. Once the journal and
the venue diverge, the divergence is PERMANENT BY CONSTRUCTION
(``BL-20260823-MODIFY-IDEMPOTENCE-COMPARES-INTENT-TO-JOURNAL-NEVER-TO-VENUE``).

Live instance this was written against: ``ib_paper`` MES trade 4350 declares
``stop_loss`` 7533.69642857 while its only resting stop rests at 7516.50 — 69
ticks, $1,289.73 on 15 contracts at ``contract_value_usd`` 5.0 — held since
2026-08-20 with a healthy monitor on a connected session.

WHAT THIS MODULE IS
===================
The DECISION only, and pure. It takes a graded price verdict plus the state
that bounds action, and returns what should happen. It opens no socket, reads
no DB and touches no order — so the policy is testable without a broker, which
is the half that went wrong on 2026-08-20 when the repair was decided ad hoc.

⚠️ THE LEVEL IS ALWAYS THE JOURNAL'S, NEVER A CALLER'S. This module returns
what to re-assert TO by echoing the declared values it was given; a caller that
passes an operator-supplied level is using it wrong. That rule is what
``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``
criterion 2 states, for the reason its own title records.

⚠️ BOTH LEGS OR NEITHER. ``modify_protective`` is a cancel-then-re-arm of the
symbol's whole bracket, so a re-assert that supplies only the stop would drop
a resting target — its docstring says so explicitly. ``STATE_NEEDS_BOTH_LEGS``
exists so that is a refusal rather than a silent half-protection.

STATES, never collapsed
=======================
``reassert``              a graded divergence, inside policy, both levels known
``agrees``                the venue matches the declaration (within tolerance)
``not_graded``            the price verdict could not decide — we did not look
``suppressed_cooldown``   a re-assert for this key fired too recently
``suppressed_attempts``   the per-key attempt budget is spent
``needs_both_legs``       diverged, but a declared level is missing
``position_absent``       nothing to protect (flat, or the trade is not open)

``not_graded`` is deliberately distinct from ``agrees``: a verdict of
``no_tick_size`` or ``no_resting_price`` means the comparison did not happen,
and reporting that as agreement is the collapse this family of bugs is made of.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.runtime.protection_price import PRICE_AGREES, PRICE_DIVERGES

__all__ = [
    "STATE_REASSERT", "STATE_AGREES", "STATE_NOT_GRADED",
    "STATE_SUPPRESSED_COOLDOWN", "STATE_SUPPRESSED_ATTEMPTS",
    "STATE_NEEDS_BOTH_LEGS", "STATE_POSITION_ABSENT",
    "MODE_OFF", "MODE_ANNOTATE", "MODE_APPLY",
    "decide_reassert", "resolve_mode", "account_may_apply",
]

STATE_REASSERT = "reassert"
STATE_AGREES = "agrees"
STATE_NOT_GRADED = "not_graded"
STATE_SUPPRESSED_COOLDOWN = "suppressed_cooldown"
STATE_SUPPRESSED_ATTEMPTS = "suppressed_attempts"
STATE_NEEDS_BOTH_LEGS = "needs_both_legs"
STATE_POSITION_ABSENT = "position_absent"

MODE_OFF = "off"
MODE_ANNOTATE = "annotate"
MODE_APPLY = "apply"
_MODES = (MODE_OFF, MODE_ANNOTATE, MODE_APPLY)

#: Default seconds between re-assert attempts for one (account, symbol).
#: The condition can hold for DAYS — MES 4350 has held it since 2026-08-20 —
#: and `modify_protective` is a real cancel-and-re-place at the venue, so an
#: attempt every sweep would be churn on a live bracket, not a retry.
DEFAULT_COOLDOWN_S = 3600.0

#: Attempts per (account, symbol) before the condition is left to a human.
#: A re-assert that keeps failing is not a transient — it is a fault whose
#: cause is not the level, and hammering it would be the desensitised alarm in
#: order form.
DEFAULT_MAX_ATTEMPTS = 3


def _f(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def resolve_mode(raw: Any) -> str:
    """``off`` / ``annotate`` / ``apply``; anything unrecognised → ``annotate``.

    A ``*_MODE`` knob, NOT a default-off ``*_ENABLED`` gate — the Prime
    Directive shape shared with ``NEWS_INFLUENCE_MODE`` /
    ``CONVICTION_SIZING_MODE`` / ``NETTING_ATTRIBUTION_MODE``.

    An unparseable value falls back to ``annotate`` rather than to ``off`` or
    ``apply``: a typo must not silently switch the observation off, and must
    certainly not switch a live order path on.
    """
    text = str(raw or "").strip().lower()
    return text if text in _MODES else MODE_ANNOTATE


def account_may_apply(account_id: Any, allowlist_raw: Any) -> bool:
    """Is this account allowed to have a re-assert TOUCH the venue?

    ⚠️ AN EMPTY ALLOWLIST MEANS **NONE**, AND THAT IS DELIBERATELY THE
    OPPOSITE OF ITS SIBLINGS. ``CONVICTION_SIZING_ACCOUNTS`` and
    ``NETTING_ATTRIBUTION_ACCOUNTS`` both read empty as ALL — and CLAUDE.md
    already flags that for what it is: *"an empty allowlist is not a safe
    default, it is the widest one"*. Those two widen a size and a DB write.
    This one cancels and re-places a live position's exit, so inheriting the
    convention would mean an unset variable arms an order path on every
    account including real money.

    The inconsistency is the point and is stated here so a later reader does
    not "harmonise" it back.
    """
    account_id = str(account_id or "").strip()
    allow = {
        part.strip() for part in str(allowlist_raw or "").split(",")
        if part.strip()
    }
    return bool(account_id) and account_id in allow


def decide_reassert(
    *,
    price_verdict: Optional[Dict[str, Any]],
    declared_sl: Any,
    declared_tp: Any,
    position_size: Any,
    trade_is_open: bool,
    seconds_since_last_attempt: Optional[float] = None,
    attempts_so_far: int = 0,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Dict[str, Any]:
    """Decide whether the declared protection should be re-asserted. Pure.

    ``price_verdict`` is a :func:`protection_price.grade_protection_price`
    result, or ``None`` when grading did not run.

    The returned ``levels`` block echoes the DECLARED values, which is what a
    caller must send to ``modify_protective`` — both of them, because that
    call re-arms the whole bracket.
    """
    out: Dict[str, Any] = {
        "state": STATE_NOT_GRADED,
        "reason": None,
        "levels": None,
        "price_state": (price_verdict or {}).get("state"),
        "ticks": (price_verdict or {}).get("ticks"),
        "exposure": (price_verdict or {}).get("exposure"),
    }

    # Nothing to protect. Checked FIRST: a re-assert onto a flat book would
    # place a resting order with no position behind it, which is the naked
    # -reverse hazard the over-cover row is about, arrived at from the other
    # direction.
    if not trade_is_open or _f(position_size) is None:
        out["state"] = STATE_POSITION_ABSENT
        out["reason"] = (
            "the trade is not open, or its size is not a positive number — "
            "there is nothing to re-assert protection onto"
        )
        return out

    state = (price_verdict or {}).get("state")
    if state == PRICE_AGREES:
        out["state"] = STATE_AGREES
        out["reason"] = "the resting level matches the declaration within tolerance"
        return out
    if state != PRICE_DIVERGES:
        # no_declared_level / no_resting_price / no_resting_leg / no_tick_size
        # / None. NONE of these is agreement. `no_resting_leg` in particular is
        # a NAKED finding and belongs to the naked sweep, not here — reporting
        # it as a divergence would double-count one condition as two.
        out["reason"] = (
            f"price verdict {state!r} did not decide — this is 'we did not "
            f"look', never 'the level agrees'"
        )
        return out

    sl, tp = _f(declared_sl), _f(declared_tp)
    if sl is None or tp is None:
        out["state"] = STATE_NEEDS_BOTH_LEGS
        out["reason"] = (
            "modify_protective re-arms the WHOLE bracket, so a re-assert "
            "carrying only one declared level would drop the other leg; "
            f"declared_sl={declared_sl!r} declared_tp={declared_tp!r}"
        )
        return out

    if attempts_so_far >= max(0, int(max_attempts)):
        out["state"] = STATE_SUPPRESSED_ATTEMPTS
        out["reason"] = (
            f"{attempts_so_far} attempt(s) already made for this key and the "
            f"divergence persists — the cause is not the level, so this is "
            f"left for a human rather than retried"
        )
        return out

    if (seconds_since_last_attempt is not None
            and seconds_since_last_attempt < max(0.0, float(cooldown_s))):
        out["state"] = STATE_SUPPRESSED_COOLDOWN
        out["reason"] = (
            f"last attempt {seconds_since_last_attempt:.0f}s ago, inside the "
            f"{cooldown_s:.0f}s cooldown — modify_protective is a real "
            f"cancel-and-re-place, so a per-sweep retry is churn on a live "
            f"bracket"
        )
        return out

    out["state"] = STATE_REASSERT
    out["levels"] = {"sl": sl, "tp": tp}
    out["reason"] = (
        "the resting protection diverges from the journal-declared level; "
        "re-assert BOTH declared levels through the trader's own client"
    )
    return out
