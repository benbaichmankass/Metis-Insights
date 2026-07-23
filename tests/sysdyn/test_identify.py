"""M29 P1 — system-identification harness: metrics, synthetic round-trip, stability."""

from __future__ import annotations

import pytest

from src.sysdyn.engine import Flow, Model, simulate
from src.sysdyn.identify import (
    identify,
    r_squared,
    rmse,
    sse,
    walk_forward_stability,
)


def test_metrics_basic():
    assert sse([1.0, 2.0], [1.0, 4.0]) == pytest.approx(4.0)
    assert rmse([1.0, 2.0], [1.0, 4.0]) == pytest.approx((4.0 / 2) ** 0.5)
    # perfect prediction → R² == 1
    assert r_squared([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)
    # flat target → R² undefined (nan)
    import math

    assert math.isnan(r_squared([1.0, 1.0], [2.0, 2.0]))


def _decay_model(s0: float) -> Model:
    # dS/dt = -k*S ; k is the free parameter to recover.
    return Model(
        name="decay",
        stocks={"s": s0},
        flows=[Flow(name="out", rate_fn=lambda st, p, e, t: p["k"] * st["s"], source="s")],
    )


def _stock_series(traj):
    return traj.series["s"][1:]


def test_synthetic_round_trip_recovers_known_param():
    truth = {"k": 0.12}
    s0, steps = 100.0, 40
    model = _decay_model(s0)
    observed = _stock_series(simulate(model, truth, [{}] * steps, steps))

    fit = identify(
        model,
        bounds={"k": (0.0, 1.0)},
        init={"k": 0.5},          # start well away from the truth
        exog=[{}] * steps,
        observed=observed,
        predict=_stock_series,
    )
    assert fit.params["k"] == pytest.approx(0.12, abs=1e-3)
    assert fit.rmse < 1e-3
    assert fit.r2 > 0.999


def test_fit_is_bounded():
    truth = {"k": 0.12}
    steps = 20
    model = _decay_model(100.0)
    observed = _stock_series(simulate(model, truth, [{}] * steps, steps))
    fit = identify(
        model,
        bounds={"k": (0.05, 0.2)},
        init={"k": 0.2},
        exog=[{}] * steps,
        observed=observed,
        predict=_stock_series,
    )
    assert 0.05 <= fit.params["k"] <= 0.2


def test_walk_forward_stability_small_spread_on_stationary_truth():
    truth = {"k": 0.1}
    steps = 60
    model = _decay_model(100.0)
    observed = _stock_series(simulate(model, truth, [{}] * steps, steps))
    report = walk_forward_stability(
        model,
        bounds={"k": (0.0, 1.0)},
        init={"k": 0.4},
        exog=[{}] * steps,
        observed=observed,
        predict=_stock_series,
        n_folds=3,
    )
    # Same generating param in every fold → the recovered k barely moves.
    assert report.max_rel_spread < 0.05
    assert report.param_means["k"] == pytest.approx(0.1, abs=5e-3)
