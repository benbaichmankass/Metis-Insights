"""Conviction lens — v1 formulaic blend (design doc § 3, § 4a).

Combines **calibrated** ``[0, 1]`` P(win) inputs into one conviction score that
(later) drives sizing, competing-trade arbitration, and the no-trade floor. In
P1 this is computed **observe-only** and stamped on the order package's meta
(no influence on the order).

Pure + dependency-free (stdlib only) so it is safe to import in the live signal
path. The caller is responsible for wrapping the call fail-permissively so a
scoring failure never strands a live signal.

v1 formula (calibrated inputs in ``[0,1]``):

    conviction = news_multiplier x ( w1*c_strat + w2*c_setup + w3*c_wr + w4*c_reg )

with weights renormalized over the inputs actually present (a strategy with no
shadow heads -> conviction == c_strat). ``news_multiplier`` is reductive-only
(``[floor, 1]``), mirroring the existing news/advisory reductive contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Default v1 weights (hand-set, tunable via the § 4.2 sweep). c_strat dominates
# because it is the only always-present, strategy-specific signal.
DEFAULT_CONVICTION_WEIGHTS: dict[str, float] = {
    "c_strat": 0.45,  # calibrated strategy signal confidence
    "c_setup": 0.20,  # calibrated setup_quality (R-multiple -> P(win))
    "c_wr": 0.20,     # calibrated trade_outcome_winrate
    "c_reg": 0.15,    # regime-alignment scalar (P(favorable regime))
}


@dataclass
class ConvictionResult:
    """Outcome of a conviction computation (observe-only in P1)."""

    conviction: float | None            # None when no inputs were present
    blended: float | None               # pre-news blend
    news_multiplier: float
    floor: float
    below_floor: bool
    inputs_used: dict[str, float] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "conviction": self.conviction,
            "blended": self.blended,
            "news_multiplier": self.news_multiplier,
            "floor": self.floor,
            "below_floor": self.below_floor,
            "inputs_used": self.inputs_used,
            "weights_used": self.weights_used,
            "note": self.note,
        }


def _clip01(v: float) -> float:
    v = float(v)
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def compute_conviction(
    inputs: dict[str, float] | None,
    *,
    weights: dict[str, float] | None = None,
    news_multiplier: float = 1.0,
    floor: float = 0.0,
) -> ConvictionResult:
    """Blend calibrated ``[0,1]`` inputs into a conviction score.

    * ``inputs`` — keyed by ``DEFAULT_CONVICTION_WEIGHTS`` names; each a calibrated
      P(win) in ``[0,1]``. Missing/``None`` keys are dropped and the weights
      renormalized over what remains.
    * ``news_multiplier`` — reductive-only; clamped to ``[0, 1]``.
    * ``floor`` — no-trade floor read off the final conviction (inert at 0).

    Never raises on empty/partial input — returns ``conviction=None`` when no
    inputs are present (never strands a live signal; ``below_floor`` stays False).
    """
    w = dict(weights) if weights else dict(DEFAULT_CONVICTION_WEIGHTS)
    news = _clip01(news_multiplier)

    present: dict[str, float] = {}
    if inputs:
        for k, v in inputs.items():
            if v is None or k not in w:
                continue
            try:
                present[k] = _clip01(float(v))
            except (TypeError, ValueError):
                continue

    if not present:
        return ConvictionResult(
            conviction=None,
            blended=None,
            news_multiplier=news,
            floor=floor,
            below_floor=False,
            inputs_used={},
            weights_used={},
            note="no_inputs_present",
        )

    wsum = sum(w[k] for k in present)
    if wsum <= 0.0:
        # weights all zero for the present inputs -> fall back to a plain mean
        blended = sum(present.values()) / len(present)
        used_w = {k: 1.0 / len(present) for k in present}
    else:
        blended = sum(w[k] * present[k] for k in present) / wsum
        used_w = {k: w[k] / wsum for k in present}

    blended = _clip01(blended)
    conviction = _clip01(news * blended)
    return ConvictionResult(
        conviction=conviction,
        blended=blended,
        news_multiplier=news,
        floor=floor,
        below_floor=conviction < floor,
        inputs_used=present,
        weights_used=used_w,
        note="ok" if len(present) == len(w) else "partial_inputs",
    )
