"""Isolated 2-leg executor for the market-neutral pairs sleeve (M22 D2).

The pairs sleeve does NOT fit the single-symbol intent model (a pair is two
simultaneous opposite legs). Following the prop-bridge pattern, it runs as its own
once-per-tick hook (``run_pairs_tick``), never through ``multi_account_execute``.

This module is split into:
  * a PURE decision core (``decide_pair``) — given the two legs' candles, the
    pair's current open-state, the set of leg-symbols already held by other open
    pairs (the disjoint-legs concurrency gate), and the execution mode, it returns
    a ``PairDecision`` (event + intended 2-leg orders + soak fields). Fully
    unit-tested, no I/O.
  * a thin live I/O layer (``run_pairs_tick`` + ``_place_pair`` / ``_close_pair``)
    that reconstructs open-state from the journal, fetches candles, calls
    ``decide_pair``, and — only for an ``execution: live`` pair on a real account —
    places/closes the legs atomically (leg-imbalance unwind on partial failure),
    journals both legs linked by a shared ``pairs_group_id``, and writes the soak.

``monitor()`` returns ``None`` by design: the executor owns the joint spread-exit,
so the per-package order-monitor must NOT independently close a pairs leg. Each
leg still carries a wide catastrophe-backstop SL/TP on the exchange.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.units.strategies import pairs_engine as pe
from src.units.strategies import pairs_sizing as psz


@dataclass(frozen=True)
class LegOrder:
    symbol: str
    direction: str          # "long" | "short"
    qty: float
    entry_ref: float        # latest close (market entry reference)
    sl: float
    tp: float


@dataclass
class PairDecision:
    event: str                              # skip_flat|skip_concurrency|skip_size|open|hold|close|shadow_*
    pair: str
    soak: Dict[str, Any] = field(default_factory=dict)
    legs: List[LegOrder] = field(default_factory=list)   # intended orders for an OPEN
    close: bool = False                                   # True → close the open pair


def _pair_label(a: str, b: str) -> str:
    return f"{a}/{b}"


def decide_pair(params: pe.PairParams, close_a: Sequence[float], close_b: Sequence[float],
                *, open_state: Optional[pe.OpenPair], held_symbols: set,
                risk_budget_usd: float, correlation_open: int,
                execution_mode: str = "live", corr_factor: float = 0.5,
                backstop_mult: float = 3.0) -> PairDecision:
    """PURE decision for one pair this tick. No I/O. `execution_mode` 'shadow'
    downgrades an would-be open/close to a shadow_* soak event with the legs still
    computed (observe-only). Returns a PairDecision."""
    label = _pair_label(params.symbol_a, params.symbol_b)
    base = {"symbol_a": params.symbol_a, "symbol_b": params.symbol_b,
            "execution_mode": execution_mode}

    # --- IN A POSITION: check exit ---
    if open_state is not None:
        ex = pe.exit_signal(close_a, close_b, params, open_state)
        if ex is None:
            return PairDecision("hold", label, soak={**base, "bars_held": open_state.bars_held})
        ev = "shadow_close" if execution_mode == "shadow" else "close"
        return PairDecision(ev, label, close=(execution_mode != "shadow"),
                            soak={**base, "outcome": ex.get("outcome"),
                                  "exit_spread": ex.get("exit_spread"),
                                  "bars_held": open_state.bars_held})

    # --- FLAT: check entry ---
    sig = pe.entry_signal(close_a, close_b, params)
    if sig is None:
        return PairDecision("skip_flat", label, soak={**base})
    # disjoint-legs concurrency gate
    if params.symbol_a in held_symbols or params.symbol_b in held_symbols:
        return PairDecision("skip_concurrency", label,
                            soak={**base, "z": sig["z"], "direction": sig["direction"],
                                  "held": sorted(held_symbols)})
    # size (with the correlation haircut for already-open correlated pairs)
    haircut = psz.correlation_haircut(correlation_open, factor=corr_factor)
    budget = float(risk_budget_usd) * haircut
    price_a, price_b = float(close_a[-1]), float(close_b[-1])
    sizing = psz.pair_notionals(budget, sig["risk"], sig["beta"], price_a, price_b)
    if sizing["qty_a"] <= 0 or sizing["qty_b"] <= 0:
        return PairDecision("skip_size", label,
                            soak={**base, "z": sig["z"], "risk": sig["risk"],
                                  "budget_usd": round(budget, 2), "haircut": haircut})
    legdirs = pe.leg_directions(sig["direction"])
    sl_a, tp_a = psz.leg_protective_levels(legdirs["a"], price_a, sig["risk"], backstop_mult)
    sl_b, tp_b = psz.leg_protective_levels(legdirs["b"], price_b, sig["risk"], backstop_mult)
    legs = [
        LegOrder(params.symbol_a, legdirs["a"], round(sizing["qty_a"], 8), price_a, sl_a, tp_a),
        LegOrder(params.symbol_b, legdirs["b"], round(sizing["qty_b"], 8), price_b, sl_b, tp_b),
    ]
    ev = "shadow_open" if execution_mode == "shadow" else "open"
    soak = {**base, "z": sig["z"], "direction": sig["direction"], "beta": sig["beta"],
            "risk": sig["risk"], "entry_spread": sig["entry_spread"], "stop_spread": sig["stop_spread"],
            "budget_usd": round(budget, 2), "haircut": haircut, "correlation_open": correlation_open,
            "pairs_group_id": f"pair-{uuid.uuid4().hex[:12]}",
            "legs": [leg.__dict__ for leg in legs]}
    return PairDecision(ev, label, legs=legs, soak=soak)


def monitor(cfg, candles_df, open_pkg):  # noqa: ANN001
    """The executor owns the joint spread-exit; the per-package order-monitor must
    NOT independently close a pairs leg. Always None (the wide per-leg backstop
    SL/TP on the exchange remains the last-resort net)."""
    return None
