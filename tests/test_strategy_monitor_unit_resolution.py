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
