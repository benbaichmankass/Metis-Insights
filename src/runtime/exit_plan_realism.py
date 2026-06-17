"""ExitPlan realism guard — R-multiple reach bounds (P1 tier).

The point of materialising a strategy's ``ExitPlan`` (``exit_plan.py``) onto the
broker is that the resting instruction is *realistic* — a take-profit the trade
can actually reach, not a far placeholder that never fills (wasting the risk
budget) and, for the prop accounts, raising breach probability against the
3%-daily / 6%-static-DD account-killers.

This module is the **advisory** guard: it flags (and, when asked, clamps) ladder
rungs / a fixed final target whose distance from entry exceeds a per-strategy
**R-multiple ceiling**. Reach is measured in units of risk
``R = |entry - stop|``:

    long:  reach_r = (price - entry) / R     (profit is above entry)
    short: reach_r = (entry - price) / R     (profit is below entry)

A rung beyond the ceiling is **clamped to the ceiling price** (never dropped /
never refused) and the change is reported in the returned notes — consistent
with the fail-permissive Prime-Directive posture of the regime gate and
news-sizing: the guard never strands or refuses a live signal, it only makes a
fantasy target realistic.

This is the **cheap, immediately-shippable** tier — it needs no new data (just
the package's own entry/stop). The empirical tier (an MFE/reach quantile per
strategy fed from the backtest sweep mirror → ``config/exit_reach_bounds.yaml``)
is a later follow-on; ``src/prop/montecarlo.py`` is outcome-R, not a
reach distribution, so it is *not* the feed for this.

Pure, dependency-free, **never raises** — on any malformed input it returns the
plan unchanged with an empty notes list, so it can sit on the order path
inertly.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["DEFAULT_MAX_REACH_R", "reach_r", "clamp_exit_plan"]

# Default per-strategy reach ceiling, in R. Generous enough that a normal
# multi-R target (e.g. turtle_soup's 2R TP2) is never touched; a rung beyond
# this is almost certainly a placeholder / mis-computed level.
DEFAULT_MAX_REACH_R = 5.0


def _coerce_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def reach_r(price: Any, *, entry: Any, stop: Any, direction: Any) -> Optional[float]:
    """Profit-direction distance of ``price`` from ``entry`` in units of risk.

    Returns ``None`` on any unusable input (non-numeric, non-positive risk,
    unknown direction). Never raises.
    """
    p = _coerce_float(price)
    e = _coerce_float(entry)
    s = _coerce_float(stop)
    if p is None or e is None or s is None:
        return None
    risk = abs(e - s)
    if not (risk > 0):
        return None
    d = str(direction).lower() if direction is not None else ""
    if d in ("long", "buy"):
        return (p - e) / risk
    if d in ("short", "sell"):
        return (e - p) / risk
    return None


def _ceiling_price(*, entry: float, risk: float, direction: str, max_r: float) -> float:
    """The price at exactly ``max_r`` R in the profit direction."""
    if direction in ("long", "buy"):
        return entry + max_r * risk
    return entry - max_r * risk


def clamp_exit_plan(
    plan: Any,
    *,
    direction: Any,
    entry: Any,
    stop: Any,
    max_reach_r: float = DEFAULT_MAX_REACH_R,
) -> Tuple[Any, List[Dict[str, Any]]]:
    """Clamp ladder rungs / a fixed final target beyond ``max_reach_r`` R.

    Returns ``(maybe_clamped_plan, notes)``. ``notes`` is a list of
    ``{"target": "rung[i]"|"final", "from": price, "to": price, "reach_r": r}``
    fragments describing each clamp applied (empty when nothing was clamped).

    The input plan is **not mutated** — a deep copy is returned when any change
    is made; the original object is returned untouched when there is nothing to
    clamp. Pure; **never raises** — on malformed input returns ``(plan, [])``.
    """
    try:
        if not isinstance(plan, dict):
            return plan, []
        e = _coerce_float(entry)
        s = _coerce_float(stop)
        d = str(direction).lower() if direction is not None else ""
        if e is None or s is None or d not in ("long", "buy", "short", "sell"):
            return plan, []
        risk = abs(e - s)
        try:
            ceil_r = float(max_reach_r)
        except (TypeError, ValueError):
            ceil_r = DEFAULT_MAX_REACH_R
        if not (risk > 0) or not (ceil_r > 0):
            return plan, []

        ceil_price = _ceiling_price(entry=e, risk=risk, direction=d, max_r=ceil_r)
        notes: List[Dict[str, Any]] = []
        out = None  # lazily deep-copied on first clamp

        def _needs_clamp(price: Any) -> Optional[float]:
            r = reach_r(price, entry=e, stop=s, direction=d)
            if r is None:
                return None
            return r if r > ceil_r else None

        # Rungs.
        rungs = plan.get("rungs")
        if isinstance(rungs, list):
            for i, rung in enumerate(rungs):
                if not isinstance(rung, dict):
                    continue
                over = _needs_clamp(rung.get("price"))
                if over is not None:
                    if out is None:
                        out = copy.deepcopy(plan)
                    notes.append({
                        "target": f"rung[{i}]",
                        "from": _coerce_float(rung.get("price")),
                        "to": ceil_price,
                        "reach_r": round(over, 4),
                    })
                    out["rungs"][i]["price"] = ceil_price

        # Fixed final target.
        final = plan.get("final")
        if isinstance(final, dict) and final.get("kind") == "fixed":
            over = _needs_clamp(final.get("price"))
            if over is not None:
                if out is None:
                    out = copy.deepcopy(plan)
                notes.append({
                    "target": "final",
                    "from": _coerce_float(final.get("price")),
                    "to": ceil_price,
                    "reach_r": round(over, 4),
                })
                out["final"]["price"] = ceil_price

        return (out if out is not None else plan), notes
    except Exception:  # noqa: BLE001 — advisory guard must never crash the path
        return plan, []
