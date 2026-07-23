"""M29 P1 — stock-flow engine correctness."""

from __future__ import annotations

import math

import pytest

from src.sysdyn.engine import Flow, Model, simulate


def _const(rate: float):
    return lambda state, params, exog, t: rate


def test_exponential_decay_matches_euler_recurrence():
    # dS/dt = -k*S  →  Euler:  S_{n+1} = S_n*(1 - k*dt).  Exact, so assert exactly.
    k, dt, s0, steps = 0.1, 0.5, 100.0, 20
    model = Model(
        name="decay",
        stocks={"s": s0},
        flows=[Flow(name="out", rate_fn=lambda st, p, e, t: k * st["s"], source="s")],
    )
    traj = simulate(model, {}, [{}] * steps, steps, dt=dt, clamp_stocks_nonneg=False)
    for n in range(steps + 1):
        assert traj.series["s"][n] == pytest.approx(s0 * (1 - k * dt) ** n, rel=1e-12)


def test_closed_system_conserves_total():
    # A → B at a constant rate; with no external in/out the total is conserved.
    model = Model(
        name="conserve",
        stocks={"a": 100.0, "b": 0.0},
        flows=[Flow(name="a_to_b", rate_fn=_const(3.0), source="a", target="b")],
    )
    traj = simulate(model, {}, [{}] * 10, 10, dt=1.0, clamp_stocks_nonneg=False)
    for i in range(len(traj.series["a"])):
        assert traj.series["a"][i] + traj.series["b"][i] == pytest.approx(100.0, abs=1e-9)


def test_delay_lags_effect_by_declared_steps():
    # A constant external inflow with delay=3 has NO effect for the first 3 steps.
    model = Model(
        name="delayed",
        stocks={"s": 0.0},
        flows=[Flow(name="fill", rate_fn=_const(5.0), target="s", delay=3)],
    )
    traj = simulate(model, {}, [{}] * 6, 6, dt=1.0)
    # series index i is the level AFTER step i-1.  Steps 0,1,2 apply 0 (buffer filling).
    assert traj.series["s"][0] == pytest.approx(0.0)
    assert traj.series["s"][1] == pytest.approx(0.0)
    assert traj.series["s"][2] == pytest.approx(0.0)
    assert traj.series["s"][3] == pytest.approx(0.0)   # after step 2
    assert traj.series["s"][4] == pytest.approx(5.0)   # step 3 finally applies the lagged rate
    assert traj.series["s"][5] == pytest.approx(10.0)


def test_negative_rate_is_clamped_to_zero():
    # A flow whose rate_fn returns negative never runs backwards.
    model = Model(
        name="clamp_rate",
        stocks={"s": 10.0},
        flows=[Flow(name="out", rate_fn=_const(-4.0), source="s")],
    )
    traj = simulate(model, {}, [{}] * 3, 3)
    assert all(v == pytest.approx(10.0) for v in traj.series["s"])


def test_stock_floored_at_zero_when_requested():
    model = Model(
        name="floor",
        stocks={"s": 5.0},
        flows=[Flow(name="drain", rate_fn=_const(10.0), source="s")],
    )
    traj = simulate(model, {}, [{}] * 3, 3, clamp_stocks_nonneg=True)
    assert traj.series["s"][1] == pytest.approx(0.0)
    assert min(traj.series["s"]) >= 0.0


def test_observations_recorded_against_acting_state():
    model = Model(
        name="obs",
        stocks={"s": 2.0},
        flows=[Flow(name="fill", rate_fn=_const(1.0), target="s")],
        observations={"twice": lambda st, p, e, t: 2.0 * st["s"]},
    )
    traj = simulate(model, {}, [{}] * 3, 3)
    # observation at step t reflects the pre-step state: 2*2, 2*3, 2*4
    assert traj.series["twice"] == pytest.approx([4.0, 6.0, 8.0])
    assert len(traj.series["twice"]) == 3  # one per step (not steps+1)


def test_exogenous_input_is_read_per_step():
    model = Model(
        name="exog",
        stocks={"s": 0.0},
        flows=[Flow(name="fill", rate_fn=lambda st, p, e, t: e["r"], target="s")],
    )
    exog = [{"r": 1.0}, {"r": 2.0}, {"r": 3.0}]
    traj = simulate(model, {}, exog, 3)
    assert traj.series["s"] == pytest.approx([0.0, 1.0, 3.0, 6.0])


def test_determinism():
    model = Model(
        name="det",
        stocks={"s": 1.0},
        flows=[Flow(name="g", rate_fn=lambda st, p, e, t: 0.05 * st["s"], target="s")],
    )
    a = simulate(model, {}, [{}] * 30, 30, dt=0.3)
    b = simulate(model, {}, [{}] * 30, 30, dt=0.3)
    assert a.series["s"] == b.series["s"]


def test_validation_errors():
    with pytest.raises(ValueError):
        Flow(name="bad", rate_fn=_const(1.0))  # no source and no target
    with pytest.raises(ValueError):
        Flow(name="bad_delay", rate_fn=_const(1.0), target="s", delay=-1)
    with pytest.raises(ValueError):
        Model(name="m", stocks={"s": 1.0}, flows=[Flow(name="f", rate_fn=_const(1.0), source="ghost")])
    good = Model(name="m", stocks={"s": 1.0}, flows=[Flow(name="f", rate_fn=_const(1.0), target="s")])
    with pytest.raises(ValueError):
        simulate(good, {}, [{}], 5)  # exog shorter than steps
