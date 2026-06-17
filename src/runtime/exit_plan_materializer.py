"""ExitPlan materializer — translate a static ExitPlan into concrete, broker-
persistable exit instructions (P2 tier, observe-only).

``exit_plan.py`` (P1) gives a strategy's *static* intended exit structure — a
ladder of partial-take-profit rungs and/or a single final target, a protective
stop, and any trailing rule. That plan is abstract: rung prices + ``qty_pct``
fractions, direction implied by the owning package. To actually *rest* the exit
on a broker (or render it into a prop ticket) you need it **materialized**: an
ordered set of concrete reduce-only take-profit orders (absolute price +
absolute qty) plus the protective stop, direction-resolved, realism-bounded, and
lot-rounded — exactly what the verdict→broker senders (``order_monitor`` →
``execute.modify_open_order`` / ``close_open_position``) or a laddered prop
ticket would consume.

This module is the **translation** half. In P2 it is **observe-only**: the
coordinator materializes the derived ExitPlan once at order-package creation and
journals the result into ``order_packages.exit_plan_state`` (the column P1
created and left null). **Nothing reads it back to drive an order yet** — the
live re-materialization cadence + the verdict/ticket emission are the
behaviour-changing P3 (prop) / P4 (API) phases, each gated on its standalone
backtest. Materializing here proves the translation is correct against real
packages without touching any order path.

Account-agnostic by default
============================

An order package is the *decision* — it is logged **before** per-account
position sizing (``Coordinator.multi_account_execute`` sizes each eligible
account separately). So there is no single absolute qty at creation time. The
materializer therefore defaults ``qty_total`` to ``1.0`` and emits **fractional**
quantities (a rung's qty == its ``qty_pct``), describing the exit *structure*
independent of any account. A later per-account materialization (P3/P4) passes
the real account qty + the instrument's lot step to get absolute, rounded
order quantities; the same function serves both.

Purity
======

Pure, dependency-free (stdlib + the sibling ExitPlan modules), and **never
raises** — ``materialize_exit_plan`` returns ``None`` on an unusable plan /
context and an otherwise-valid dict on success, so it can sit inertly on the
order path. It performs no I/O.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from src.runtime.exit_plan import validate_exit_plan
from src.runtime.exit_plan_realism import (
    DEFAULT_MAX_REACH_R,
    clamp_exit_plan,
    reach_r,
)

__all__ = ["MATERIALIZED_EXIT_VERSION", "materialize_exit_plan"]

# Schema version this module emits.
MATERIALIZED_EXIT_VERSION = 1

_LONG = ("long", "buy")
_SHORT = ("short", "sell")


def _coerce_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _norm_direction(direction: Any) -> Optional[str]:
    d = str(direction).lower() if direction is not None else ""
    if d in _LONG:
        return "long"
    if d in _SHORT:
        return "short"
    return None


def _round_qty(qty: float, qty_step: Optional[float]) -> float:
    """Round ``qty`` DOWN to a multiple of ``qty_step`` (reduce-only must never
    over-close). No step → unrounded. Never raises."""
    step = _coerce_float(qty_step)
    if step is None or step <= 0:
        return qty
    return math.floor(qty / step) * step


def materialize_exit_plan(
    plan: Any,
    *,
    direction: Any,
    entry: Any,
    stop: Any = None,
    qty_total: Any = 1.0,
    qty_step: Optional[float] = None,
    max_reach_r: float = DEFAULT_MAX_REACH_R,
    as_of: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Materialize an ExitPlan into concrete, ordered exit instructions.

    Args:
        plan: a schema-valid ExitPlan (``exit_plan.validate_exit_plan``). Invalid
            ⇒ ``None``.
        direction: ``long``/``buy`` or ``short``/``sell``. Resolves which side
            the take-profits sit (above entry for long, below for short).
        entry: entry price (used for the R / reach computation and realism clamp).
        stop: protective stop price. Defaults to the plan's own ``stop.price``
            when omitted; an explicit value (e.g. a ratcheted stop) overrides it.
        qty_total: total position qty the plan covers. Default ``1.0`` ⇒
            **fractional** materialization (account-agnostic, for the
            package-creation artifact). Pass a real account qty for an absolute
            per-account materialization.
        qty_step: instrument lot step. When given, each order qty is floored to a
            multiple of it (reduce-only never over-closes); a rung that floors to
            0 is dropped with a ``qty_underflow`` note.
        max_reach_r: R-multiple ceiling handed to the realism guard
            (``clamp_exit_plan``) so fantasy targets are pulled to a reachable
            price before they are rested.
        as_of: optional caller timestamp, echoed into the result (observe-only).

    Returns a JSON-serialisable ``MaterializedExit`` dict, or ``None`` when the
    plan is invalid / the context unusable. **Never raises.**
    """
    try:
        ok, _reason = validate_exit_plan(plan)
        if not ok:
            return None

        d = _norm_direction(direction)
        e = _coerce_float(entry)
        if d is None or e is None:
            return None

        # Stop: explicit arg wins, else the plan's own stop.
        s = _coerce_float(stop)
        if s is None:
            s = _coerce_float((plan.get("stop") or {}).get("price"))
        if s is None:
            return None
        risk = abs(e - s)
        if not (risk > 0):
            return None

        qty_t = _coerce_float(qty_total)
        if qty_t is None or qty_t <= 0:
            qty_t = 1.0

        notes: List[Dict[str, Any]] = []

        # Pull fantasy targets to a reachable price first, so what we rest is
        # realistic (and record what moved).
        clamped, realism_notes = clamp_exit_plan(
            plan, direction=d, entry=e, stop=s, max_reach_r=max_reach_r
        )

        targets: List[Dict[str, Any]] = []
        allocated_pct = 0.0

        def _add_target(kind: str, price: Any, qty_pct: float) -> None:
            nonlocal allocated_pct
            p = _coerce_float(price)
            if p is None or p <= 0:
                notes.append({"dropped": kind, "reason": "non_positive_price", "price": price})
                return
            raw_qty = qty_pct * qty_t
            qty = _round_qty(raw_qty, qty_step)
            allocated_pct += qty_pct
            if qty <= 0:
                notes.append({"dropped": kind, "reason": "qty_underflow", "qty_pct": qty_pct})
                return
            r = reach_r(p, entry=e, stop=s, direction=d)
            targets.append({
                "kind": kind,
                "price": p,
                "qty": qty,
                "qty_pct": round(qty_pct, 6),
                "reach_r": round(r, 4) if r is not None else None,
            })

        # Ladder rungs (partial take-profits).
        rungs = clamped.get("rungs")
        if isinstance(rungs, list):
            for rung in rungs:
                if isinstance(rung, dict):
                    _add_target("rung", rung.get("price"), float(rung.get("qty_pct") or 0.0))

        # Final target for the post-ladder remainder.
        final = clamped.get("final") or {}
        final_trailing: Optional[Dict[str, Any]] = None
        residual_pct = max(0.0, 1.0 - allocated_pct)
        if final.get("kind") == "fixed":
            if residual_pct > 0:
                _add_target("final", final.get("price"), residual_pct)
        elif final.get("kind") == "trailing":
            # A trailing final has no fixed resting price — the remainder rides a
            # trailing stop. Surface the rule + the residual qty it governs; the
            # live trail materialization (a moving stop) is P3/P4.
            final_trailing = {
                "trail_r": _coerce_float(final.get("trail_r")),
                "activate_r": _coerce_float(final.get("activate_r")),
                "floor": final.get("floor"),
            }

        # Order the resting take-profits near→far in the profit direction.
        targets.sort(key=lambda t: t["price"], reverse=(d == "short"))

        # Residual qty that no fixed target covers (rides a trailing final, or is
        # left uncovered if the ladder banked <100% with a trailing/absent final).
        # A fixed final consumes ``residual_pct`` above, driving this toward 0.
        covered_pct = min(1.0, allocated_pct)
        residual_qty = _round_qty(max(0.0, 1.0 - covered_pct) * qty_t, qty_step)

        result: Dict[str, Any] = {
            "version": MATERIALIZED_EXIT_VERSION,
            "as_of": as_of,
            "direction": d,
            "entry": e,
            "risk": risk,
            "qty_total": qty_t,
            "fractional": qty_step is None and math.isclose(qty_t, 1.0),
            "targets": targets,
            "final_trailing": final_trailing,
            "stop": {"price": s, "qty": qty_t},
            "trailing_stop": clamped.get("trailing_stop"),
            "time_decay_minutes": clamped.get("time_decay_minutes"),
            "residual_qty": residual_qty,
            "realism_notes": realism_notes,
            "notes": notes,
        }
        return result
    except Exception:  # noqa: BLE001 — observe-only translation must never crash the path
        return None
