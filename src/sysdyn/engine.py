"""M29 P1 — the pure stock-flow simulation engine.

A **system-dynamics** model is stocks (accumulations), flows (rates that move
quantity between stocks / in and out of the system), auxiliary observations
(quantities read off the state each step, e.g. a price), and feedback loops with
delays. This module is the *engine*: declare such a model as data + pure rate
functions, then integrate it forward deterministically.

**Layer 1 (Signals / Modelling) — pure by contract.** No I/O, no randomness, no
clock, no order path, no broker, no config read. `simulate` is a pure function of
its arguments; the same inputs always produce the same trajectory (locked by the
`src.sysdyn` import-linter contract + the determinism test). Data (exogenous
drivers, observed series to calibrate against) is *injected* by the caller — the
engine never fetches it — mirroring how the M28 P4 thesis runner injects its
candle reader so `macro_thesis` stays layer-pure.

Integration is explicit Euler on a fixed step ``dt`` (weeks for the EIA/NG seed
model). Delays are fixed-lag ring buffers: a flow declared with ``delay=k`` uses
the rate it computed ``k`` steps ago, so a cause takes ``k*dt`` to reach its
effect (the lag that makes feedback loops oscillate/settle rather than snap).

Deliberately dependency-light: **pure Python stdlib**, no numpy/scipy. Seed
models are small (a handful of stocks on a weekly clock), so a plain-Python
integrator is more than fast enough and keeps this module trivially portable and
its dependency surface empty.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

# A rate/observation function reads the current model state, the parameter dict,
# the exogenous inputs for this step, and the step index; it returns a scalar.
# It MUST be pure (no side effects) so the whole simulation stays pure.
RateFn = Callable[[Mapping[str, float], Mapping[str, float], Mapping[str, float], int], float]


@dataclass(frozen=True)
class Flow:
    """A rate that moves quantity per unit time.

    ``source``/``target`` name a stock, or ``None`` for the world outside the
    modelled boundary (external inflow has ``source=None``; external outflow has
    ``target=None``). ``rate_fn`` returns the *non-negative* magnitude of the
    flow for the current step (a negative return is clamped to 0 — direction is
    expressed by which of source/target is set, not by the sign of the rate, so
    a flow can never mysteriously reverse). ``delay`` (in whole steps) routes the
    rate through a fixed-lag buffer.
    """

    name: str
    rate_fn: RateFn
    source: Optional[str] = None
    target: Optional[str] = None
    delay: int = 0

    def __post_init__(self) -> None:
        if self.source is None and self.target is None:
            raise ValueError(f"flow {self.name!r}: source and target cannot both be None")
        if self.delay < 0:
            raise ValueError(f"flow {self.name!r}: delay must be >= 0, got {self.delay}")


@dataclass(frozen=True)
class Model:
    """A stock-flow model: named stocks + flows + auxiliary observations.

    ``stocks`` maps each stock name to its initial level. ``observations`` maps a
    readout name to a pure function of the (state, params, exog, t) — used for
    quantities that are *read off* the system rather than accumulated (e.g. a
    price driven by how far a stock sits from normal). ``observations`` are
    computed and recorded but never integrated.
    """

    name: str
    stocks: Mapping[str, float]
    flows: Sequence[Flow]
    observations: Mapping[str, RateFn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = set(self.stocks)
        for fl in self.flows:
            for role, s in (("source", fl.source), ("target", fl.target)):
                if s is not None and s not in names:
                    raise ValueError(
                        f"flow {fl.name!r} {role}={s!r} is not a declared stock {sorted(names)}"
                    )


@dataclass
class Trajectory:
    """The result of a simulation: per-step series for every stock + observation.

    ``series[name]`` is a list of length ``steps + 1`` for a stock (index 0 is the
    initial level) and length ``steps`` for an observation (recorded at each step
    *before* that step's integration, so it reflects the state the step acted on).
    ``times[i] = i * dt``.
    """

    times: list[float]
    series: dict[str, list[float]]

    def final(self, name: str) -> float:
        return self.series[name][-1]

    def observation(self, name: str) -> list[float]:
        return self.series[name]


def _clamp_nonneg(x: float) -> float:
    return x if x > 0.0 else 0.0


def simulate(
    model: Model,
    params: Mapping[str, float],
    exog: Sequence[Mapping[str, float]],
    steps: int,
    *,
    dt: float = 1.0,
    clamp_stocks_nonneg: bool = True,
) -> Trajectory:
    """Integrate ``model`` forward ``steps`` steps of size ``dt``.

    ``params`` are the model's free/fixed parameters (what the identifier fits).
    ``exog`` is the per-step exogenous input: a sequence of at least ``steps``
    mappings, ``exog[t]`` supplying the driver values (e.g. weather/demand) the
    rate functions read at step ``t``. ``clamp_stocks_nonneg`` floors physical
    stocks at 0 (working gas in storage can't go negative); set False for a stock
    that may legitimately go negative.

    Pure: no I/O, no randomness — deterministic in (model, params, exog, steps, dt).
    """
    if steps < 0:
        raise ValueError(f"steps must be >= 0, got {steps}")
    if len(exog) < steps:
        raise ValueError(f"exog has {len(exog)} rows but {steps} steps requested")

    state: dict[str, float] = dict(model.stocks)
    series: dict[str, list[float]] = {name: [state[name]] for name in model.stocks}
    for obs_name in model.observations:
        series[obs_name] = []
    times: list[float] = [0.0]

    # One fixed-lag buffer per delayed flow, pre-filled with 0.0 so a delayed
    # cause has no effect until enough history has accrued.
    buffers: dict[str, deque[float]] = {
        fl.name: deque([0.0] * fl.delay, maxlen=fl.delay) for fl in model.flows if fl.delay > 0
    }

    for t in range(steps):
        exog_t = exog[t]
        # Record observations against the state this step will act on.
        for obs_name, obs_fn in model.observations.items():
            series[obs_name].append(float(obs_fn(state, params, exog_t, t)))

        # Compute each flow's applied rate (routing delayed flows through their buffer).
        net: dict[str, float] = {name: 0.0 for name in model.stocks}
        for fl in model.flows:
            raw = _clamp_nonneg(float(fl.rate_fn(state, params, exog_t, t)))
            if fl.delay > 0:
                buf = buffers[fl.name]
                applied = buf[0] if len(buf) == fl.delay else 0.0
                buf.append(raw)
            else:
                applied = raw
            if fl.source is not None:
                net[fl.source] -= applied
            if fl.target is not None:
                net[fl.target] += applied

        # Euler step.
        for name in model.stocks:
            nxt = state[name] + dt * net[name]
            if clamp_stocks_nonneg and nxt < 0.0:
                nxt = 0.0
            state[name] = nxt
            series[name].append(nxt)
        times.append((t + 1) * dt)

    return Trajectory(times=times, series=series)
