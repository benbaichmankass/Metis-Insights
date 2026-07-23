"""M29 P1 — the EIA NG storage → MNG price seed model + its identification."""

from __future__ import annotations

import pytest

from src.sysdyn.engine import simulate
from src.sysdyn.identify import identify
from src.sysdyn.seed_gas import (
    CAUSAL_STRUCTURE,
    DEFAULT_PARAMS,
    FREE_PARAM_BOUNDS,
    build_gas_storage_model,
    price_series,
    seasonal_exogenous,
    storage_series,
)
from src.sysdyn.structure import CausalStructure


def test_structure_validates_and_round_trips():
    CAUSAL_STRUCTURE.validate()
    d = CAUSAL_STRUCTURE.to_dict()
    back = CausalStructure.from_dict(d)
    assert back.to_dict() == d
    # exactly one balancing loop is the whole point of the seed model
    assert [lp.kind for lp in CAUSAL_STRUCTURE.loops] == ["balancing"]


def test_seasonal_dynamics_are_qualitatively_right():
    weeks = 104  # two annual cycles
    model = build_gas_storage_model(initial_storage=DEFAULT_PARAMS["storage_normal"])
    exog = seasonal_exogenous(weeks)
    traj = simulate(model, DEFAULT_PARAMS, exog, weeks, dt=1.0)

    storage = traj.series["storage"]
    price = traj.series["price"]
    assert min(storage) > 0.0  # never fully drains (no clamp artifacts)

    # In the first year: storage bottoms in winter and the price peak coincides
    # with the storage trough (low storage → high price — the core linkage).
    yr = storage[: 53]
    trough_week = min(range(len(yr)), key=lambda i: yr[i])
    price_yr = price[:52]
    peak_price_week = max(range(len(price_yr)), key=lambda i: price_yr[i])
    assert abs(trough_week - peak_price_week) <= 2
    assert max(price_yr) > DEFAULT_PARAMS["base_price"]  # scarcity lifts price above reference


def test_price_feedback_damps_the_draw():
    # Turning the balancing loop off (price_feedback=0) should draw storage LOWER
    # than with the feedback on — the loop demonstrably counteracts the depletion.
    weeks = 104
    model = build_gas_storage_model(initial_storage=DEFAULT_PARAMS["storage_normal"])
    exog = seasonal_exogenous(weeks)

    on = simulate(model, DEFAULT_PARAMS, exog, weeks).series["storage"]
    off_params = {**DEFAULT_PARAMS, "price_feedback": 0.0}
    off = simulate(model, off_params, exog, weeks).series["storage"]
    assert min(off) < min(on)


def test_identify_recovers_supply_demand_params_from_storage():
    # inj_rate (summer fill) and wd_rate (winter draw) act in DIFFERENT seasons, so
    # they are cleanly identifiable from the storage trajectory (no trade-off valley).
    weeks = 104
    model = build_gas_storage_model(initial_storage=DEFAULT_PARAMS["storage_normal"])
    exog = seasonal_exogenous(weeks)
    observed = storage_series(simulate(model, DEFAULT_PARAMS, exog, weeks))

    fixed = {k: DEFAULT_PARAMS[k] for k in ("base_price", "storage_normal", "price_k", "price_feedback")}
    fit = identify(
        model,
        bounds={"inj_rate": FREE_PARAM_BOUNDS["inj_rate"], "wd_rate": FREE_PARAM_BOUNDS["wd_rate"]},
        init={"inj_rate": 120.0, "wd_rate": 20.0},  # offset from truth (80 / 12)
        fixed=fixed,
        exog=exog,
        observed=observed,
        predict=storage_series,
    )
    assert fit.params["inj_rate"] == pytest.approx(DEFAULT_PARAMS["inj_rate"], rel=0.10)
    assert fit.params["wd_rate"] == pytest.approx(DEFAULT_PARAMS["wd_rate"], rel=0.10)


def test_identify_recovers_price_elasticity_from_price():
    # price_k directly scales the price readout → identifiable from the price series.
    weeks = 104
    model = build_gas_storage_model(initial_storage=DEFAULT_PARAMS["storage_normal"])
    exog = seasonal_exogenous(weeks)
    observed = price_series(simulate(model, DEFAULT_PARAMS, exog, weeks))

    fixed = {k: DEFAULT_PARAMS[k] for k in ("base_price", "storage_normal", "inj_rate", "wd_rate", "price_feedback")}
    fit = identify(
        model,
        bounds={"price_k": FREE_PARAM_BOUNDS["price_k"]},
        init={"price_k": 3.0},  # truth is 1.5
        fixed=fixed,
        exog=exog,
        observed=observed,
        predict=price_series,
    )
    assert fit.params["price_k"] == pytest.approx(DEFAULT_PARAMS["price_k"], rel=0.10)
    assert fit.rmse < 0.02  # price ~ $3, so < ~0.7%


def test_full_free_fit_drives_error_low():
    # Fitting all four free params against price reaches a near-perfect fit even if
    # individual weakly-identified params (price_feedback) trade off — the objective
    # is achieved; identifiability of each param is what walk_forward_stability judges.
    weeks = 104
    model = build_gas_storage_model(initial_storage=DEFAULT_PARAMS["storage_normal"])
    exog = seasonal_exogenous(weeks)
    observed = price_series(simulate(model, DEFAULT_PARAMS, exog, weeks))

    fixed = {"base_price": DEFAULT_PARAMS["base_price"], "storage_normal": DEFAULT_PARAMS["storage_normal"]}
    fit = identify(
        model,
        bounds=FREE_PARAM_BOUNDS,
        init={"inj_rate": 100.0, "wd_rate": 16.0, "price_k": 2.2, "price_feedback": 1.0},
        fixed=fixed,
        exog=exog,
        observed=observed,
        predict=price_series,
    )
    assert fit.rmse < 0.05
