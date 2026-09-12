#!/usr/bin/env python3
"""Did the ENTRY-DECLARED stop end this trade, or one a lever had already moved?

ONE OWNER for that question. Import it; do not re-derive it per analysis.

THE DEFECT THIS EXISTS TO STOP
==============================
``trades.stop_loss`` is overwritten IN PLACE by a trailing amend, so a stop-out
graded against it is graded against whatever the stop *had become*, not against
the geometry the leg declares. An analysis that reads that field and calls it
"the declared stop" attributes the exit to a level that did not end the trade —
and that attribution is then evidence for changing that level.

MEASURED (MI-275, ``docs/research/stop-width-counterfactual-2026-09-11.md``
sections 3.5 and 4): 2 of the 7 post-e35 stop-out packages exited at a stop
amended tighter after entry, one of them at **0.276 ATR against a declared
2.0** — roughly a seventh of its declared width. Counting those as evidence for
reverting ``atr_stop_mult`` overstates the case by 29% of that population.
Filed as ``BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT``.

⚠️ ``order_packages.sl`` IS TRAILED TOO, AND THAT IS THE TRAP IN THE OBVIOUS FIX
==============================================================================
The natural repair — "read the package instead of the trade" — does not work.
MEASURED 2026-09-12 over the newest 1000-row ``/api/diag/journal`` tail (599
closed trades, every one linked to a package present in the same tail):
``order_packages.sl`` differs from ``exit_plan.stop.price`` on **118 of 599
(19.7%)**. On BOTH of MI-275's named exhibits it holds the *trailed* value
(``pkg-27b4c7d12e794bcc``: ``sl`` 77776.37857143 against a declared
77044.07142857), so an analysis falling back to it would compare a trailed
level with itself and grade both exhibits **clean** — inverting the finding on
2 of 2.

That is why ``declared_stop_source`` is a returned value and why a non-frozen
source yields ``ungradeable_declared_stop_not_entry_frozen`` rather than a
verdict. *We could not look* and *we looked and it was the declared stop* are
opposite statements; this module refuses to collapse them.

THE SECOND TRAP: THE ANCHOR
===========================
The declared stop is anchored to the **package** entry, not the trade's fill.
``pkg-65f02cffa856451f`` declares ``entry 7.128`` while its trade row records
``entry_price 7.099``. MI-275 lost 15 hours to a reproduction error from
recomputing off the fill. So the anchor is ``package.entry``, the fallback to
the fill is NAMED (``anchor_source``), and the declared distance is read from
the frozen level rather than recomputed as ``atr × mult`` — which also
preserves whatever clamp or rounding the bot actually applied.

WHY THE TOLERANCE IS A FRACTION OF THE DECLARED WIDTH, NOT ATR
==============================================================
MI-275 graded ``abs((final - declared) / atr) <= 0.02``. ATR is entry-frozen in
``package.meta.atr`` and is present on only **322 of 599 (53.8%)** of the same
tail, so an ATR-based tolerance cannot grade the other 46% at all. The basis
here is ``|final - declared| / |anchor - declared|`` against
``DEFAULT_TOLERANCE_FRAC``, which needs no ATR.

⚠️ THE TWO ARE NOT IDENTICAL AND THE DIFFERENCE IS STATED RATHER THAN HIDDEN:
0.01 of the declared width equals 0.02 ATR **exactly at a 2.0-ATR declared
width**, and is proportionally tighter below it (0.015 ATR at 1.5) and looser
above. On the real population that changes nothing — an amended stop moves by
whole percent, not by hundredths — and ``tests/test_stop_attribution.py``
asserts agreement with MI-275's own verdicts on its named exhibits rather than
assuming it. ``moved_atr`` is still REPORTED when ATR is available; it is an
output, not the basis.

SCOPE. Pure and stdlib-only: dicts in, a dict out. No DB, no network, no
runtime import — so a research script, a CI check and a test all reach the same
answer. It classifies WHICH STOP LEVEL was in force at the exit. It does **not**
claim the trade was a stop-out at all; that adjudication belongs to the caller,
and ``exit_reason`` alone does not settle it (MI-275 found ``reconciler_filled``
rows that were stop-outs and ``sl``-labelled rows that were not).
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

__all__ = [
    "STATES",
    "GRADED_STATES",
    "UNGRADEABLE_STATES",
    "DECLARED_STOP_SOURCES",
    "DEFAULT_TOLERANCE_FRAC",
    "declared_stop",
    "classify",
    "split",
    "format_split",
]

#: 1% of the declared stop distance. Equals MI-275's 0.02-ATR tolerance exactly
#: at a 2.0-ATR declared width; see the module docstring for the difference.
DEFAULT_TOLERANCE_FRAC = 0.01

#: The stop that was in force when the trade ended.
GRADED_STATES = (
    "entry_declared",    # within tolerance of the entry-frozen declared level
    "amended_tighter",   # moved toward the entry — a lever ratcheted it in
    "amended_wider",     # moved away from the entry
)

#: Every one of these means *we could not look*. None of them is a clean answer,
#: and none may be folded into `entry_declared`.
UNGRADEABLE_STATES = (
    "ungradeable_no_declared_stop",                 # no entry-frozen level recorded
    "ungradeable_declared_stop_not_entry_frozen",   # only a level that may itself be trailed
    "ungradeable_no_final_stop",                    # the trade records no stop
    "ungradeable_no_anchor",                        # no entry to measure the width from
    "ungradeable_no_direction",                     # cannot say which way is TIGHTER
    "ungradeable_zero_declared_distance",           # declared stop AT the entry: no width to scale
)

STATES = GRADED_STATES + UNGRADEABLE_STATES

DECLARED_STOP_SOURCES = (
    "exit_plan_stop_entry_frozen",   # the level the bot actually placed
    "package_sl_may_be_trailed",     # order_packages.sl — overwritten in place, 19.7% of the tail
    "absent",
)


def _f(v: Any) -> float | None:
    """A finite float, or None. A string, None, NaN and inf all yield None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _obj(v: Any) -> dict:
    """A dict from a value that may already be one, or a JSON string, or junk."""
    if isinstance(v, Mapping):
        return dict(v)
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def declared_stop(package: Mapping[str, Any]) -> tuple[float | None, str]:
    """The ENTRY-FROZEN declared stop, and where it came from.

    Returns ``(price, source)`` with ``source`` from :data:`DECLARED_STOP_SOURCES`.

    ⚠️ ``package_sl_may_be_trailed`` is returned rather than swallowed so a
    caller can see it, but :func:`classify` refuses to grade on it — see the
    module docstring for the 2-of-2 inversion that produced this rule.
    """
    stop_block = _obj(package.get("exit_plan")).get("stop")
    price = _f(stop_block.get("price")) if isinstance(stop_block, Mapping) else None
    if price is not None and price > 0:
        return price, "exit_plan_stop_entry_frozen"
    fallback = _f(package.get("sl"))
    if fallback is not None and fallback > 0:
        return fallback, "package_sl_may_be_trailed"
    return None, "absent"


def classify(
    package: Mapping[str, Any],
    trade: Mapping[str, Any],
    *,
    tolerance_frac: float = DEFAULT_TOLERANCE_FRAC,
) -> dict[str, Any]:
    """Which stop level was in force when ``trade`` ended.

    ``package`` is an ``order_packages`` row (needs ``exit_plan``, ``entry``;
    ``meta.atr`` optional). ``trade`` is a ``trades`` row (needs ``stop_loss``,
    ``direction``; ``entry_price`` used only as an anchor fallback).

    Every returned magnitude is ``None`` rather than ``0.0`` when it could not
    be computed — a zero move is a real reading and must stay distinguishable.
    """
    dstop, dsource = declared_stop(package)
    direction = str(trade.get("direction") or package.get("direction") or "").strip().lower()
    final = _f(trade.get("stop_loss"))

    anchor = _f(package.get("entry"))
    anchor_source = "package_entry"
    if anchor is None or anchor <= 0:
        anchor = _f(trade.get("entry_price"))
        anchor_source = "trade_fill_fallback" if anchor else "absent"

    atr = _f(_obj(package.get("meta")).get("atr"))
    if atr is not None and atr <= 0:
        atr = None

    out: dict[str, Any] = {
        "state": None,
        "declared_stop": dstop,
        "declared_stop_source": dsource,
        "final_stop": final,
        "anchor": anchor,
        "anchor_source": anchor_source,
        "direction": direction or None,
        "atr": atr,
        "declared_distance": None,
        "moved_abs": None,
        "moved_frac": None,
        "moved_atr": None,
        "final_stop_atr_from_anchor": None,
        "tolerance_frac": tolerance_frac,
    }

    # Order matters: report the MOST specific reason we cannot grade.
    if dsource == "absent":
        out["state"] = "ungradeable_no_declared_stop"
        return out
    if dsource != "exit_plan_stop_entry_frozen":
        out["state"] = "ungradeable_declared_stop_not_entry_frozen"
        return out
    if final is None:
        out["state"] = "ungradeable_no_final_stop"
        return out
    if anchor is None:
        out["state"] = "ungradeable_no_anchor"
        return out

    declared_distance = abs(anchor - dstop)
    out["declared_distance"] = declared_distance
    moved_abs = abs(final - dstop)
    out["moved_abs"] = moved_abs
    if atr is not None:
        out["moved_atr"] = moved_abs / atr
        out["final_stop_atr_from_anchor"] = abs(anchor - final) / atr

    if declared_distance <= 0:
        # A declared stop sitting ON the entry has no width, so "1% of the
        # width" is not a threshold. Refused rather than graded at zero.
        out["state"] = "ungradeable_zero_declared_distance"
        return out

    out["moved_frac"] = moved_abs / declared_distance
    if out["moved_frac"] <= tolerance_frac:
        out["state"] = "entry_declared"
        return out

    # Tighter = the stop moved TOWARD the entry, i.e. it can be hit sooner.
    # Keyed on the POSITION's direction, never on an order side.
    if direction == "long":
        tighter = final > dstop
    elif direction == "short":
        tighter = final < dstop
    else:
        # The stop MOVED, and without a direction we cannot say which way is
        # tighter. Reported as its own state rather than guessed: naming the
        # wrong side here is what BYBIT_HEDGE_MODE_SYMBOLS' `positionIdx`
        # resolver refuses to do for the same reason.
        out["state"] = "ungradeable_no_direction"
        return out
    out["state"] = "amended_tighter" if tighter else "amended_wider"
    return out


def split(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Count a population of :func:`classify` results by state.

    Returns every state as a key (zeros included, so a state's absence is
    visible) plus ``graded``, ``ungradeable`` and ``total``. ``amended_frac``
    is the share of the **graded** population that was amended, and is ``None``
    — never ``0.0`` — when nothing was gradeable.
    """
    counts = {s: 0 for s in STATES}
    unknown = 0
    for r in records:
        s = r.get("state")
        if s in counts:
            counts[s] += 1
        else:
            unknown += 1
    graded = sum(counts[s] for s in GRADED_STATES)
    ungradeable = sum(counts[s] for s in UNGRADEABLE_STATES)
    amended = counts["amended_tighter"] + counts["amended_wider"]
    return {
        **counts,
        "graded": graded,
        "ungradeable": ungradeable,
        "unrecognised_state": unknown,
        "total": graded + ungradeable + unknown,
        "amended": amended,
        "amended_frac": (amended / graded) if graded else None,
    }


def format_split(summary: Mapping[str, Any], label: str = "stop attribution") -> str:
    """One line that CANNOT omit the ungradeable count.

    The row this module answers says excluding trailed stop-outs silently is
    not sufficient either: the count is load-bearing and belongs in the output.
    So it is rendered here rather than left to each caller to remember.
    """
    graded = int(summary.get("graded") or 0)
    frac = summary.get("amended_frac")
    frac_txt = "n/a (nothing gradeable)" if frac is None else f"{frac:.1%}"
    return (
        f"{label}: {summary.get('total', 0)} trade(s) — "
        f"{summary.get('entry_declared', 0)} at the ENTRY-DECLARED stop, "
        f"{summary.get('amended_tighter', 0)} amended tighter, "
        f"{summary.get('amended_wider', 0)} amended wider "
        f"({frac_txt} of {graded} graded were amended); "
        f"{summary.get('ungradeable', 0)} UNGRADEABLE (we could not look)"
    )
