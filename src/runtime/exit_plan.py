"""Canonical strategy ``ExitPlan`` schema + validator + legacy derivation (P1).

A live trade / order-package is *owned* by the strategy that opened it. The
2026-06-16 live-trade-management contract formalised the per-tick ``Verdict``
(``src/runtime/strategy_verdict.py``) — the *delta* a strategy's ``monitor()``
emits each tick. This module adds the complementary **static** half: the
``ExitPlan`` — a strategy's declaration of the *whole intended exit structure*
for an open trade (a ladder of partial-take-profit rungs and/or a single final
target, plus the stop and any trailing rule).

Why both. A ``Verdict`` says "this tick, move the SL to X" or "close 25% now".
An ``ExitPlan`` says "the intended exit is: bank 25% at TP1, run the rest to
TP2, stop at S, trail to break-even after 1R". The plan is what a *materializer*
(P2+) rests on the broker (or renders into a prop ticket) so the exit is a
stable, realistic, broker-persistable instruction rather than something that
only exists tick-by-tick inside the bot. The ``ExitPlan`` is the static superset;
the ``Verdict`` stays the dynamic delta that *evolves* a materialized plan. Both
validators coexist — this module does **not** replace ``validate_verdict``.

This module is **pure and dependency-free** (stdlib typing only) and both
``validate_exit_plan`` and ``build_exit_plan_from_legacy`` **never raise** — the
validator returns ``(ok, reason)`` and the builder returns ``None`` on anything
it can't derive. It is import-safe from any layer (signal builders,
order-monitor, prop ticket, tests, CI guards) and performs no I/O.

The canonical ExitPlan schema
=============================

A plan is a JSON-serialisable dict, version-tagged::

    {
      "version": 1,
      "rungs": [ {"price": <pos float>, "qty_pct": <(0,1]>}, ... ],
      "final": {"kind": "fixed", "price": <pos float>}
               | {"kind": "trailing", "trail_r": <pos>,
                  "activate_r": <>=0>, "floor": "breakeven" | <pos float>},
      "stop":  {"price": <pos float>},
      "trailing_stop": None
                       | {"activate_r": <>=0>,
                          "trail_kind": "be" | "chandelier" | "atr",
                          "param": <>=0>},
      "time_decay_minutes": None | <pos float>,
      "meta": { ... }     # optional, free-form
    }

- ``rungs`` is the (possibly empty) partial-TP ladder. Each rung banks
  ``qty_pct`` of the *original* qty when ``price`` is reached. The cumulative
  ``qty_pct`` across all rungs must be ``<= 1.0`` — whatever is left rides to
  ``final``. An empty ``rungs`` list means "single target, no scale-out".
- ``final`` is the target for the remaining (post-ladder) qty: a ``fixed`` price,
  or a ``trailing`` rule (run the remainder behind a trailing stop).
- ``stop`` is the protective stop (always present).
- ``trailing_stop`` is an optional ratchet rule applied to the stop while the
  trade runs (e.g. trail to break-even after ``activate_r`` R).
- ``time_decay_minutes`` carries a session/max-hold bound (vwap/fvg_range).

The validator is **direction-agnostic** — it checks types, ranges, and the
cumulative-qty invariant, not whether a rung sits above/below entry (the caller
that has the direction owns that; the realism guard in
``exit_plan_realism.py`` does the directional reach check). It is deliberately
permissive about *extra* keys (forward-compat) but strict about the value types
of the keys it knows.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Tuple

__all__ = [
    "EXIT_PLAN_VERSION",
    "validate_exit_plan",
    "build_exit_plan_from_legacy",
]

# The schema version this module emits and validates against.
EXIT_PLAN_VERSION = 1

# Recognised values for the discriminated unions.
_FINAL_KINDS = ("fixed", "trailing")
_TRAIL_KINDS = ("be", "chandelier", "atr")
_FLOOR_BREAKEVEN = "breakeven"

# Cumulative-qty tolerance — floats accumulated from per-rung pcts may overshoot
# 1.0 by an ulp or two; a 0.25+0.25+0.5 ladder must not be rejected.
_QTY_EPS = 1e-6


def _is_positive_number(value: Any) -> bool:
    """True iff ``value`` is a real, finite, strictly-positive number.

    Rejects bools (``True`` is an ``int`` but never a price), NaN/inf, strings,
    and non-positive values. Prices, R-multiples and SL/TP levels are positive.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0.0


def _is_nonneg_number(value: Any) -> bool:
    """True iff ``value`` is a real, finite, ``>= 0`` number (rejects bool)."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f >= 0.0


def _validate_rungs(rungs: Any) -> Tuple[bool, str]:
    if not isinstance(rungs, list):
        return False, f"'rungs' must be a list, got {type(rungs).__name__}"
    cumulative = 0.0
    for i, rung in enumerate(rungs):
        if not isinstance(rung, dict):
            return False, f"rung[{i}] must be a dict, got {type(rung).__name__}"
        if not _is_positive_number(rung.get("price")):
            return False, f"rung[{i}] 'price' must be a positive number, got {rung.get('price')!r}"
        pct = rung.get("qty_pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            return False, f"rung[{i}] 'qty_pct' must be a number, got {pct!r}"
        fpct = float(pct)
        if not math.isfinite(fpct) or not (0.0 < fpct <= 1.0):
            return False, f"rung[{i}] 'qty_pct' must be in (0, 1], got {pct!r}"
        cumulative += fpct
    if cumulative > 1.0 + _QTY_EPS:
        return False, f"cumulative rung qty_pct {cumulative:.6f} exceeds 1.0"
    return True, "ok"


def _validate_final(final: Any) -> Tuple[bool, str]:
    if not isinstance(final, dict):
        return False, f"'final' must be a dict, got {type(final).__name__}"
    kind = final.get("kind")
    if kind not in _FINAL_KINDS:
        return False, f"final 'kind' must be one of {_FINAL_KINDS}, got {kind!r}"
    if kind == "fixed":
        if not _is_positive_number(final.get("price")):
            return False, f"fixed final 'price' must be a positive number, got {final.get('price')!r}"
        return True, "ok"
    # trailing
    if not _is_positive_number(final.get("trail_r")):
        return False, f"trailing final 'trail_r' must be a positive number, got {final.get('trail_r')!r}"
    if not _is_nonneg_number(final.get("activate_r")):
        return False, f"trailing final 'activate_r' must be a number >= 0, got {final.get('activate_r')!r}"
    floor = final.get("floor")
    if floor != _FLOOR_BREAKEVEN and not _is_positive_number(floor):
        return False, f"trailing final 'floor' must be 'breakeven' or a positive number, got {floor!r}"
    return True, "ok"


def _validate_trailing_stop(ts: Any) -> Tuple[bool, str]:
    if ts is None:
        return True, "ok"
    if not isinstance(ts, dict):
        return False, f"'trailing_stop' must be a dict or None, got {type(ts).__name__}"
    if not _is_nonneg_number(ts.get("activate_r")):
        return False, f"trailing_stop 'activate_r' must be a number >= 0, got {ts.get('activate_r')!r}"
    if ts.get("trail_kind") not in _TRAIL_KINDS:
        return False, f"trailing_stop 'trail_kind' must be one of {_TRAIL_KINDS}, got {ts.get('trail_kind')!r}"
    if not _is_nonneg_number(ts.get("param")):
        return False, f"trailing_stop 'param' must be a number >= 0, got {ts.get('param')!r}"
    return True, "ok"


def validate_exit_plan(plan: Any) -> Tuple[bool, str]:
    """Validate an ``ExitPlan`` against the canonical schema.

    Pure, dependency-free, and **never raises**. Returns ``(ok, reason)``:

    - ``(True, "ok")`` when ``plan`` conforms.
    - ``(False, "<why>")`` describing the first violation otherwise.

    Unlike ``validate_verdict`` (where ``None`` is the valid no-op), an
    ``ExitPlan`` is a structure — ``None`` is *not* a valid plan here.
    """
    if not isinstance(plan, dict):
        return False, f"exit plan must be a dict, got {type(plan).__name__}"

    if plan.get("version") != EXIT_PLAN_VERSION:
        return False, f"'version' must be {EXIT_PLAN_VERSION}, got {plan.get('version')!r}"

    ok, reason = _validate_rungs(plan.get("rungs"))
    if not ok:
        return False, reason

    ok, reason = _validate_final(plan.get("final"))
    if not ok:
        return False, reason

    stop = plan.get("stop")
    if not isinstance(stop, dict) or not _is_positive_number(stop.get("price")):
        return False, f"'stop' must be {{'price': positive}}, got {stop!r}"

    ok, reason = _validate_trailing_stop(plan.get("trailing_stop"))
    if not ok:
        return False, reason

    tdm = plan.get("time_decay_minutes")
    if tdm is not None and not _is_positive_number(tdm):
        return False, f"'time_decay_minutes' must be None or a positive number, got {tdm!r}"

    if "meta" in plan and not isinstance(plan["meta"], dict):
        return False, f"'meta' must be a dict when present, got {type(plan['meta']).__name__}"

    return True, "ok"


# ---------------------------------------------------------------------------
# Legacy derivation — every strategy gets a plan with zero per-strategy code
# ---------------------------------------------------------------------------

def _coerce_positive(value: Any) -> Optional[float]:
    """Best-effort float coercion → positive float or None (never raises)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if (math.isfinite(f) and f > 0.0) else None


def _resolve_meta(open_pkg: Any) -> dict:
    """Return the package ``meta`` as a dict (decoding a JSON string), or {}.

    The order-monitor normalises the JSON blob, but unit tests and ad-hoc
    callers may pass the raw row — mirror turtle_soup.monitor's defence.
    """
    if not isinstance(open_pkg, dict):
        return {}
    meta = open_pkg.get("meta")
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str) and meta:
        try:
            import json as _json
            decoded = _json.loads(meta)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def build_exit_plan_from_legacy(open_pkg: Any, cfg: Any = None) -> Optional[dict]:
    """Derive a schema-valid ``ExitPlan`` from an order package's legacy fields.

    This is the **no-break guarantee**: a strategy that never declares an
    explicit ``exit_plan()`` still gets a plan, constructed purely from the
    ``entry``/``sl``/``tp`` (+ ``meta.tp2``) fields every package already
    carries — behaviour-identical to today (the same single target / TP1→TP2
    roll the legacy ``monitor()`` already drives). Pure; **never raises**;
    returns ``None`` only when the mandatory ``sl``/``tp`` (and entry, when a
    rung is present) can't be resolved as positive numbers.

    Field synonyms are accepted (``entry``/``entry_price``, ``sl``/``stop_loss``,
    ``tp``/``take_profit``) so it resolves against either the order_packages row
    shape or the monitor's normalised dict.
    """
    if not isinstance(open_pkg, dict):
        return None

    entry = _coerce_positive(open_pkg.get("entry") if open_pkg.get("entry") is not None
                             else open_pkg.get("entry_price"))
    sl = _coerce_positive(open_pkg.get("sl") if open_pkg.get("sl") is not None
                          else open_pkg.get("stop_loss"))
    tp = _coerce_positive(open_pkg.get("tp") if open_pkg.get("tp") is not None
                          else open_pkg.get("take_profit"))
    if sl is None or tp is None:
        return None

    meta = _resolve_meta(open_pkg)
    tp2 = _coerce_positive(meta.get("tp2"))

    cfg_dict = cfg if isinstance(cfg, dict) else {}
    try:
        partial_pct = float(cfg_dict.get("partial_close_pct", 0.25))
    except (TypeError, ValueError):
        partial_pct = 0.25
    if not (0.0 < partial_pct < 1.0):
        partial_pct = 0.25

    # A distinct, valid TP2 ⇒ a two-rung ladder (bank partial_pct at TP1, run
    # the remainder to TP2). Otherwise a single fixed target at TP.
    if tp2 is not None and entry is not None and not math.isclose(tp2, tp, rel_tol=1e-9, abs_tol=1e-8):
        rungs = [{"price": tp, "qty_pct": round(partial_pct, 6)}]
        final = {"kind": "fixed", "price": tp2}
    else:
        rungs = []
        final = {"kind": "fixed", "price": tp}

    # Optional break-even trailing rule — only when cfg declares it, so a
    # cfg-less derivation stays minimal (and behaviour-neutral).
    trailing_stop = None
    try:
        be_at_r = cfg_dict.get("be_at_r")
        if be_at_r is not None:
            be_r = float(be_at_r)
            if math.isfinite(be_r) and be_r > 0:
                trailing_stop = {"activate_r": be_r, "trail_kind": "be", "param": 0.0}
    except (TypeError, ValueError):
        trailing_stop = None

    plan = {
        "version": EXIT_PLAN_VERSION,
        "rungs": rungs,
        "final": final,
        "stop": {"price": sl},
        "trailing_stop": trailing_stop,
        "time_decay_minutes": None,
        "meta": {
            "source": str(open_pkg.get("strategy_name") or open_pkg.get("strategy") or "legacy"),
            "derived": True,
        },
    }

    ok, _reason = validate_exit_plan(plan)
    return plan if ok else None
