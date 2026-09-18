#!/usr/bin/env python3
"""Target basis — the ONE definition of "what take-profit did this arm place?"

WHY THIS MODULE EXISTS. MI-307 (`docs/research/offline-mfe-distribution-2026-09-18.md`)
ran two arms per leg and named them ``uncapped`` and ``capped``:

    uncapped   ``--tp-cap-pct 0``        the TARGET-SETTING basis
    capped     ``--tp-cap-pct 0.099``    the LIVE-COMPARABLE basis

On ``backtest_trend`` / ``backtest_pullback`` / ``backtest_squeeze`` those two
names are accurate, because those harnesses have **no other source of a
target**: ``tp_price`` is ``None`` unless ``--tp-cap-pct`` supplies one, so
"no flag" really is "no take-profit" and "flag" really is the live book.

**THAT MAPPING IS FALSE FOR ``backtest_ict_scalp`` AND ``backtest_fvg_range``,
AND SILENTLY SO**, which is why the naming is now data rather than convention.
Both of those harnesses get a target from the strategy itself — the scalp
harness calls the live ``ict_scalp.order_package`` and exits at its ``tp``
(``entry ± tp_at_r * risk``); the fvg harness computes the range boundary its
live unit also uses. So on those two harnesses:

  * "no flag" is **not** the uncapped basis — it is the LIVE book; and
  * ``--tp-cap-pct 0.099`` is **not** the live book — it is a COUNTERFACTUAL,
    because neither live unit clamps (``src/runtime/tp_venue_cap.py``'s
    ``CLAMPING_FAMILIES`` is ``{donchian, pullback, fade, squeeze}``, and a
    grep of all of ``src/`` finds the constant applied in exactly four unit
    modules and nowhere downstream).

A driver that passed the flag and labelled the result "capped/live-comparable"
would therefore be stating a provenance the run does not have — sub-class **A**
of the UNPROVENANCED DIAGNOSTIC OUTPUT class (``CLAUDE.md`` §  "Diagnostic
provenance"), the one where the label names a quantity the code did not
compute. The remedy that section prescribes is to **branch on the actual
condition rather than reword the label**, so every trade carries the basis it
actually got and a reader never has to infer it from what the caller believes
it passed.

FIVE STATES, NEVER COLLAPSED
----------------------------
``unit_tp``            the strategy's own target, unmodified. On a
                       non-clamping family this IS live parity —
                       ``m20_fleet_exit_sweep.tp_geometry_for`` calls the same
                       condition ``live_parity_uncapped`` and that function
                       remains the owner of the RUN-level question.
``unit_tp_clamped``    a cap was requested **and it bound** (it moved the
                       target). On a non-clamping family this is a
                       counterfactual, not parity.
``unit_tp_cap_inert``  a cap was requested and did **not** bind. Distinct from
                       ``unit_tp_clamped`` on purpose: pooling them would let a
                       run report "capped" over trades the cap never touched,
                       and the inert COUNT is the measurement that says whether
                       the clamp's absence on this family matters at all.
``no_target``          the target was switched off. This is the arm MI-307
                       calls ``uncapped`` — the target-setting basis, and the
                       one these two harnesses could not previously produce.
``no_unit_tp``         the strategy produced no target to start from. *We did
                       not get one* — never folded into ``no_target``, which is
                       a deliberate choice by the caller.

WHY ``no_target`` AND ``no_unit_tp`` ARE SEPARATE. They yield the same ``tp``
(``None``) and mean opposite things: one is an arm we asked for, the other is a
strategy that declined to name a target. Collapsing them would let a
degenerate-signal bug read as a deliberate experimental arm — the
``collapsed-state`` class this repo keeps a guard for.

Dependency-free by construction (stdlib ``typing`` only), so a harness importing
it costs nothing, matching ``src/runtime/tp_venue_cap.py``'s own rule.
"""
from __future__ import annotations

from typing import Optional, Tuple

#: The strategy's own target, unmodified.
UNIT_TP = "unit_tp"
#: A cap was requested AND it moved the target.
UNIT_TP_CLAMPED = "unit_tp_clamped"
#: A cap was requested and did NOT move the target.
UNIT_TP_CAP_INERT = "unit_tp_cap_inert"
#: The target was deliberately switched off — the target-setting arm.
NO_TARGET = "no_target"
#: The strategy produced no target at all — *we did not get one*.
NO_UNIT_TP = "no_unit_tp"

#: Every state this module can return. A consumer that branches on the basis
#: should assert membership rather than assume the set, so a future state
#: cannot be silently swallowed by an ``else``.
ALL_STATES = frozenset({
    UNIT_TP, UNIT_TP_CLAMPED, UNIT_TP_CAP_INERT, NO_TARGET, NO_UNIT_TP,
})

#: States in which a take-profit exit is REACHABLE. ``no_target`` / ``no_unit_tp``
#: are not — a reader counting take-profit exits over a population containing
#: them is dividing by the wrong denominator.
TARGET_BEARING_STATES = frozenset({
    UNIT_TP, UNIT_TP_CLAMPED, UNIT_TP_CAP_INERT,
})


def resolve_target(
    *,
    entry: float,
    direction: str,
    unit_tp: Optional[float],
    cap_pct: float = 0.0,
    disabled: bool = False,
) -> Tuple[Optional[float], str]:
    """Return ``(tp, basis)`` for one trade.

    ``disabled`` is tested FIRST and unconditionally: switching the target off
    is a property of the arm, not something a cap can override. Passing both
    ``disabled=True`` and a positive ``cap_pct`` is a caller error in the sense
    that the cap is ignored, and the returned basis says so plainly rather than
    quietly honouring one of the two.

    ``cap_pct <= 0`` is the DEFAULT and returns the unit's target untouched, so
    a harness that never passes the flag is byte-identical to before this
    module existed — the same opt-out discipline ``--tp-cap-pct`` already
    carries in ``backtest_trend.py``.
    """
    if disabled:
        return None, NO_TARGET
    if unit_tp is None:
        return None, NO_UNIT_TP
    unit_tp = float(unit_tp)
    if cap_pct is None or cap_pct <= 0.0:
        return unit_tp, UNIT_TP
    entry = float(entry)
    if str(direction) == "long":
        capped = entry * (1.0 + float(cap_pct))
        tp = min(unit_tp, capped)
    else:
        capped = entry * (1.0 - float(cap_pct))
        tp = max(unit_tp, capped)
    return tp, _basis_after_cap(unit_tp, tp)


def _basis_after_cap(unit_tp: float, tp: float) -> str:
    """Basis for a cap that was REQUESTED — bound, or inert.

    Exact equality is the right test: ``tp`` is either ``unit_tp`` itself or
    the other operand of a ``min``/``max``, so there is no arithmetic drift to
    tolerate. A tolerance here would report a cap that bound by a hair as
    inert, which is the direction that hides the clamp.
    """
    return UNIT_TP_CLAMPED if tp != unit_tp else UNIT_TP_CAP_INERT


def cap_r(*, entry: float, risk: float, cap_pct: float) -> Optional[float]:
    """``cap_r = cap_pct * entry / risk`` — the clamp expressed in R.

    The same expression as ``position_telemetry.cap_r`` and
    ``mi307_offline_mfe._cap_r``. ``None`` — never ``0.0`` — when risk is
    non-positive: a zero would read as "the clamp binds immediately", the
    opposite of "we cannot express it".
    """
    try:
        risk = float(risk)
        if risk <= 0:
            return None
        return float(cap_pct) * float(entry) / risk
    except (TypeError, ValueError):
        return None
