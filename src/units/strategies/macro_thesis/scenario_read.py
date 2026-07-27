"""M36 Track C · C1 — ``scenario_read``: fold an M29 scenario ensemble into an
M28 ``TradeThesis`` (the M28⊕M29 merge seam).

M29 (`src.sysdyn`) is the world-MODEL: `engine.simulate` integrates a calibrated
stock/flow model forward along one exogenous path → a `Trajectory`. Running it
over an **ensemble** of exog paths (surprise-vs-consensus / policy-shock
assumptions) yields a **distribution** of the driver's horizon value (e.g. the
forward MNG-price distribution from `gas_storage_price_v1`). M28 is the thesis
ENGINE that bets on the world. This module is the pure adapter that turns that
scenario distribution into three thesis inputs (design
`M36-macro-intelligence-and-crowding-DESIGN.md` Move A):

  1. **`c_scenario`** — a conviction LENS ∈ [0,1]: how strongly the ensemble
     supports the thesis *direction* (the probability mass on the thesis's side).
  2. an informed **`target`** + **`horizon_days`** hint (the scenario's expected
     move + when the model expects it to play out).
  3. a point-in-time **`macro_context.scenario`** snapshot (traceable evidence).

**Pure + stdlib-only, observe-only.** No I/O, no clock, no randomness, no numpy;
the ensemble outcomes are *injected* by the caller (who ran `sysdyn.simulate`),
so this module never fetches or simulates — it only reads a distribution. It is
decoupled from `src.sysdyn` on purpose: it consumes a plain sequence of terminal
outcome values, so any scenario source (the seed NG model, a future model, or a
synthetic test ensemble) feeds it. Nothing here sizes, places, or mutates a live
path; `apply_to_thesis` returns a NEW thesis (immutable-by-copy) that a later
Tier-3 step may act on — the actual blend into `thesis_conviction` and any live
sizing are gated by the C4 backtest + operator approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from statistics import mean, pstdev
from typing import Any, Optional, Sequence

from .thesis import DIRECTIONS, TradeThesis


def _num(v: Any) -> Optional[float]:
    """Best-effort finite float, else None (rejects bool / NaN / inf)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _quantile(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated quantile of an already-sorted list (stdlib, no numpy)."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


@dataclass(frozen=True)
class ScenarioSummary:
    """Summary statistics of a scenario ensemble's horizon-value distribution."""

    driver: str
    base_value: Optional[float]
    horizon_days: Optional[int]
    n: int
    mean: Optional[float] = None
    p_up: Optional[float] = None  # fraction of outcomes ABOVE base_value
    dispersion: Optional[float] = None  # population stdev of outcomes
    q10: Optional[float] = None
    q50: Optional[float] = None
    q90: Optional[float] = None
    expected_move_pct: Optional[float] = None  # (mean - base) / |base|
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "driver": self.driver,
            "base_value": self.base_value,
            "horizon_days": self.horizon_days,
            "n": self.n,
            "mean": self.mean,
            "p_up": self.p_up,
            "dispersion": self.dispersion,
            "q10": self.q10,
            "q50": self.q50,
            "q90": self.q90,
            "expected_move_pct": self.expected_move_pct,
            "note": self.note,
        }


def summarize_ensemble(
    outcomes: Sequence[Any],
    base_value: Any,
    *,
    driver: str = "driver",
    horizon_days: Optional[int] = None,
) -> ScenarioSummary:
    """Summarise the ensemble's terminal-value distribution.

    ``outcomes`` is the sequence of horizon (terminal) values the driver took
    across the scenario ensemble — one per exog path. ``base_value`` is the
    driver's current (as-of) value, the reference the move is measured against.
    Tolerant: non-numeric entries are dropped; an empty ensemble or an invalid
    base yields an honest ``n``/``None``-filled summary with a ``note``.
    """
    vals = [f for f in (_num(o) for o in outcomes) if f is not None]
    n = len(vals)
    base = _num(base_value)
    if n == 0:
        return ScenarioSummary(driver, base, horizon_days, 0, note="empty ensemble")
    s = sorted(vals)
    mu = mean(vals)
    p_up = (sum(1 for v in vals if base is not None and v > base) / n) if base is not None else None
    exp_move = None
    if base is not None and base != 0.0:
        exp_move = (mu - base) / abs(base)
    return ScenarioSummary(
        driver=driver,
        base_value=base,
        horizon_days=horizon_days,
        n=n,
        mean=mu,
        p_up=p_up,
        dispersion=pstdev(vals) if n > 1 else 0.0,
        q10=_quantile(s, 0.10),
        q50=_quantile(s, 0.50),
        q90=_quantile(s, 0.90),
        expected_move_pct=exp_move,
        note=None if base is not None else "no base_value — p_up/expected_move unavailable",
    )


@dataclass(frozen=True)
class ScenarioRead:
    """The thesis-facing read derived from a :class:`ScenarioSummary`."""

    driver: str
    direction: Optional[str]
    horizon_days: Optional[int]
    c_scenario: Optional[float]  # [0,1] — mass on the thesis direction (the lens)
    conviction_signed: Optional[float]  # [-1,1] = 2*c_scenario - 1
    p_up: Optional[float]
    expected_move_pct: Optional[float]
    dispersion: Optional[float]
    n: int
    snapshot: dict = field(default_factory=dict)
    note: Optional[str] = None


def read_scenario(summary: ScenarioSummary, direction: Optional[str]) -> ScenarioRead:
    """Turn a :class:`ScenarioSummary` + a thesis ``direction`` into a thesis read.

    ``c_scenario`` = the probability mass the ensemble puts on the thesis's side:
    ``p_up`` for a long, ``1 - p_up`` for a short. A direction the scenario
    supports scores high; one it fights scores low — the calibratable "does the
    world-model back this bet" lens. ``None`` when the ensemble carries no
    direction read (no base / empty). Direction-agnostic when ``direction`` is
    unset (``c_scenario`` stays None; the snapshot still carries the distribution).
    """
    d = (direction or "").strip().lower() if direction else None
    if d is not None and d not in DIRECTIONS:
        d = None
    p_up = summary.p_up
    c_scenario: Optional[float] = None
    if p_up is not None and d is not None:
        c_scenario = p_up if d == "long" else (1.0 - p_up)
        c_scenario = max(0.0, min(1.0, c_scenario))
    signed = None if c_scenario is None else (2.0 * c_scenario - 1.0)
    snapshot = summary.to_dict()
    snapshot["direction"] = d
    snapshot["c_scenario"] = c_scenario
    return ScenarioRead(
        driver=summary.driver,
        direction=d,
        horizon_days=summary.horizon_days,
        c_scenario=c_scenario,
        conviction_signed=signed,
        p_up=p_up,
        expected_move_pct=summary.expected_move_pct,
        dispersion=summary.dispersion,
        n=summary.n,
        snapshot=snapshot,
        note=summary.note,
    )


def apply_to_thesis(thesis: TradeThesis, read: ScenarioRead, *, updated_at: str) -> TradeThesis:
    """Return a NEW thesis with the scenario read folded in (pure, observe-only).

    Conservative by design — annotate, never override a human/former decision:
      * ``macro_context['scenario']`` gets the snapshot (point-in-time evidence).
      * ``conviction_provenance['c_scenario']`` records the lens value + n
        (the blend into ``thesis_conviction`` itself is the C4-gated step — this
        module never sets ``thesis_conviction``).
      * ``target`` / ``horizon_days`` are filled ONLY when currently unset (a
        hint from the model), never overwriting a target the former already set.
      * ``direction`` is never touched (the scenario conditions a bet the
        value/event thesis already justified — it does not pick the side).

    Immutable-by-copy so the caller keeps the prior state for the observe-only
    soak (log the would-be annotation, then apply).
    """
    macro_context = dict(thesis.macro_context)
    macro_context["scenario"] = read.snapshot

    provenance = dict(thesis.conviction_provenance)
    provenance["c_scenario"] = {"value": read.c_scenario, "n": read.n, "driver": read.driver}

    target = thesis.target
    if not target and read.expected_move_pct is not None:
        target = {
            "source": "scenario",
            "expected_move_pct": read.expected_move_pct,
            "expected_value": read.snapshot.get("mean"),
        }

    horizon = thesis.horizon_days if thesis.horizon_days is not None else read.horizon_days

    return replace(
        thesis,
        macro_context=macro_context,
        conviction_provenance=provenance,
        target=target,
        horizon_days=horizon,
        updated_at=updated_at,
    )
