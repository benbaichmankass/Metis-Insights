"""Drift guard: every registered strategy must resolve to a unit module that
the order-monitor can actually call monitor() on.

Aliased strategies (the WS-A metals + M15 equity/fx sleeves, ict_scalp_5m, …)
have no same-name module — they reuse a base unit via the signal builder. The
order-monitor resolves them through pipeline.monitor_unit_for(). If a new
aliased strategy is added to _STRATEGY_BUILDERS without a matching entry in
_STRATEGY_MONITOR_UNIT, its open positions would silently run on static SL/TP
with no active monitor() — exactly the orphan-MHG gap. This test fails CI in
that case.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

pytest.importorskip("pandas")

from src.runtime.pipeline import _STRATEGY_BUILDERS, monitor_unit_for
from src.runtime.intent_multiplexer import _resolve_builders


def _all_registered_strategies():
    """Union of BOTH live rosters the order-monitor must resolve against.

    The legacy ``pipeline._STRATEGY_BUILDERS`` is NOT the whole story — the
    IBKR/FX symbol sleeves register only in the intent-layer roster
    (``intent_multiplexer``), which is what actually generates signals
    (``MULTI_STRATEGY_INTENT_LAYER`` default on). Iterating only
    ``_STRATEGY_BUILDERS`` left those sleeves unguarded, so a sleeve whose
    ``monitor()`` couldn't be resolved (``mgc_trend_1h`` →
    ``No module named 'src.units.strategies.mgc_trend_1h'``) slipped past CI
    while its live positions ran naked on static SL/TP (BL-20260615-MGCNAKED).
    """
    names = set(_STRATEGY_BUILDERS)
    names.update(_resolve_builders())
    return sorted(names)


def test_every_strategy_resolves_to_a_module_with_monitor():
    missing = []
    for strategy_name in _all_registered_strategies():
        module_name = monitor_unit_for(strategy_name)
        try:
            mod = importlib.import_module(f"src.units.strategies.{module_name}")
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{strategy_name} → {module_name}: import failed ({exc})")
            continue
        if getattr(mod, "monitor", None) is None:
            missing.append(f"{strategy_name} → {module_name}: no monitor()")
    assert not missing, (
        "Strategies with no resolvable monitor() (add to "
        "pipeline._STRATEGY_MONITOR_UNIT): " + "; ".join(missing)
    )


# Canonical monitor() signature the order-monitor calls positionally:
#   _call_strategy_monitor -> monitor_fn(cfg, candles_df, open_pkg)
# (src/runtime/order_monitor.py::_call_strategy_monitor). A strategy whose
# monitor() doesn't accept these three positional args would raise every tick
# and its position would run blind on the static backstop alone. This guard is
# a STATIC inspect.signature check — it never executes monitor() on synthetic
# candles (which risks flaky false failures), so it stays deterministic.
_MONITOR_PARAMS = ("cfg", "candles_df", "open_pkg")


def _accepts_three_positionals(sig: inspect.Signature) -> bool:
    """True iff ``sig`` can be called positionally with exactly 3 args.

    Accepts the canonical ``(cfg, candles_df, open_pkg)`` plus any function
    that varargs-captures them (``*args``) or carries extra params with
    defaults — what matters is that ``monitor(a, b, c)`` is a legal call.
    """
    positional = [
        p
        for p in sig.parameters.values()
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(
        p.kind is inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()
    )
    if has_varargs:
        return True
    # Must have at least 3 positional slots, and no more than 3 REQUIRED
    # (extras must carry defaults so a 3-arg call is legal).
    if len(positional) < 3:
        return False
    required = [p for p in positional if p.default is inspect.Parameter.empty]
    return len(required) <= 3


def test_every_strategy_monitor_has_callable_three_arg_signature():
    bad = []
    for strategy_name in _all_registered_strategies():
        module_name = monitor_unit_for(strategy_name)
        try:
            mod = importlib.import_module(f"src.units.strategies.{module_name}")
        except Exception as exc:  # noqa: BLE001 — resolution covered by sibling test
            bad.append(f"{strategy_name} → {module_name}: import failed ({exc})")
            continue
        monitor_fn = getattr(mod, "monitor", None)
        if monitor_fn is None:
            bad.append(f"{strategy_name} → {module_name}: no monitor()")
            continue
        if not callable(monitor_fn):
            bad.append(f"{strategy_name} → {module_name}: monitor is not callable")
            continue
        try:
            sig = inspect.signature(monitor_fn)
        except (TypeError, ValueError) as exc:
            bad.append(f"{strategy_name} → {module_name}: no signature ({exc})")
            continue
        if not _accepts_three_positionals(sig):
            bad.append(
                f"{strategy_name} → {module_name}: monitor{sig} is not callable as "
                f"monitor{_MONITOR_PARAMS}"
            )
    assert not bad, (
        "Strategies whose monitor() can't be called as "
        "monitor(cfg, candles_df, open_pkg): " + "; ".join(bad)
    )
