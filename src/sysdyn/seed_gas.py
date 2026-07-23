"""M29 P1 — the seed causal model: EIA weekly NG storage → MNG price response.

The operator-locked P0 seed system (the ROADMAP_MACRO M1/M2 canonical case). A
deliberately small, legible stock-flow model of the natural-gas storage balance
and its price readout, on a **weekly** clock (EIA publishes working-gas-in-storage
every Thursday):

    stock:   storage            working gas in storage (Bcf)
    flows:   injection (in)     seasonal supply into storage (summer/shoulder)
             withdrawal (out)   heating-demand-driven draw (winter)
    aux:     price              MNG/NG price, rising as storage falls below normal

    Balancing loop B1 (the whole point of the model):
        heating_demand ↑ → withdrawal ↑ → storage ↓ → (storage gap ↑) → price ↑
        → price feedback → effective demand ↓ → withdrawal ↓

That loop is why a cold-snap surprise reprices gas and then self-limits — the
research edge M28 is after lives in this linkage, not in any single series.

Fixed structural constants (``base_price``, ``storage_normal``) ride in ``params``
so the rate functions stay pure closures over their arguments; the *free* params
(``inj_rate``, ``wd_rate``, ``price_k``, ``price_feedback``) are what
:mod:`src.sysdyn.identify` fits from history. Pure stdlib.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from .engine import Flow, Model, Trajectory
from .structure import CausalStructure, Link, Loop

# --- default parameters (plausible orders of magnitude; the identifier refines) ---

DEFAULT_PARAMS: dict[str, float] = {
    # structural constants (held fixed during identification)
    "base_price": 3.0,          # $/MMBtu reference
    "storage_normal": 2000.0,   # Bcf — the "normal" level price is referenced to
    # free parameters (fit from data)
    "inj_rate": 80.0,           # Bcf/week at full injection season
    "wd_rate": 12.0,            # Bcf/week per unit heating demand
    "price_k": 1.5,             # price elasticity to the storage gap
    "price_feedback": 0.5,      # demand reduction per unit price deviation (the B1 gain)
}

# Search bounds for the free parameters (system identification).
FREE_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "inj_rate": (10.0, 200.0),
    "wd_rate": (1.0, 40.0),
    "price_k": (0.1, 5.0),
    "price_feedback": (0.0, 3.0),
}


def _price_from_storage(storage: float, params: Mapping[str, float]) -> float:
    gap = (params["storage_normal"] - storage) / params["storage_normal"]
    return params["base_price"] * math.exp(params["price_k"] * gap)


def _injection_rate(state, params, exog, t) -> float:  # noqa: ANN001
    # Supply into storage scales with the seasonal injection window (0..1).
    return params["inj_rate"] * float(exog.get("injection_season", 0.0))


def _withdrawal_rate(state, params, exog, t) -> float:  # noqa: ANN001
    # Demand-driven draw, damped by the price feedback (the balancing loop).
    price = _price_from_storage(state["storage"], params)
    price_dev = (price - params["base_price"]) / params["base_price"]
    eff_demand = float(exog.get("heating_demand", 0.0)) - params["price_feedback"] * price_dev
    return params["wd_rate"] * eff_demand  # engine clamps to >= 0


def _price_obs(state, params, exog, t) -> float:  # noqa: ANN001
    return _price_from_storage(state["storage"], params)


def build_gas_storage_model(initial_storage: float = 2000.0) -> Model:
    """The seed stock-flow model. ``initial_storage`` sets the stock's start level."""
    return Model(
        name="gas_storage_price_v1",
        stocks={"storage": float(initial_storage)},
        flows=[
            Flow(name="injection", rate_fn=_injection_rate, target="storage"),
            Flow(name="withdrawal", rate_fn=_withdrawal_rate, source="storage"),
        ],
        observations={"price": _price_obs},
    )


def price_series(traj: Trajectory) -> Sequence[float]:
    """Predictor for :mod:`identify` — the modelled price series to fit to observed."""
    return traj.observation("price")


def storage_series(traj: Trajectory) -> Sequence[float]:
    """Predictor for :mod:`identify` when calibrating against observed EIA storage."""
    # Stocks record steps+1 points (index 0 = initial); drop it to align with the
    # per-step observation/observed length.
    return traj.series["storage"][1:]


# --- the committed, versioned causal structure (legibility artifact) ---

CAUSAL_STRUCTURE = CausalStructure(
    model="gas_storage_price_v1",
    version="1.0.0",
    stocks=("storage",),
    flows=("injection", "withdrawal"),
    auxiliaries=("price",),
    exogenous=("heating_demand", "injection_season"),
    links=(
        Link("injection_season", "injection", "+", note="seasonal window drives supply"),
        Link("injection", "storage", "+", note="supply fills storage"),
        Link("heating_demand", "withdrawal", "+", note="cold → draws"),
        Link("withdrawal", "storage", "-", note="draws deplete storage"),
        Link("storage", "price", "-", note="low storage vs normal → higher price"),
        Link("price", "withdrawal", "-", delayed=False, note="price feedback dampens demand (B1)"),
    ),
    loops=(
        Loop(
            name="B1_storage_price",
            kind="balancing",
            nodes=("heating_demand", "withdrawal", "storage", "price"),
            note="demand → draws → storage down → price up → demand destruction → draws ease",
        ),
    ),
    description=(
        "EIA weekly working-gas-in-storage balance with a price readout. The seed "
        "system for M29 P1 (macro-energy target A). Free params: inj_rate, wd_rate, "
        "price_k, price_feedback. Weekly clock (dt=1 week)."
    ),
)


def seasonal_exogenous(
    weeks: int,
    *,
    demand_amp: float = 5.0,
    demand_base: float = 3.0,
    peak_week: int = 3,
) -> list[dict[str, float]]:
    """A deterministic, plausible weekly driver series for demos/tests (pure — no
    randomness). Heating demand peaks in deep winter (``peak_week``) and troughs in
    summer; the injection window is its complement (fills in summer). One full
    annual cycle per 52 weeks.

    This is a *modelling convenience* for exercising the engine, NOT the live feed —
    real runs inject point-in-time EIA/weather history (the remaining P1 step).
    """
    exog: list[dict[str, float]] = []
    for t in range(weeks):
        phase = 2.0 * math.pi * ((t - peak_week) / 52.0)
        heating = max(0.0, demand_base + demand_amp * math.cos(phase))
        injection_season = max(0.0, 0.5 - 0.5 * math.cos(phase))  # ~1 in summer, ~0 in winter
        exog.append({"heating_demand": heating, "injection_season": injection_season})
    return exog
