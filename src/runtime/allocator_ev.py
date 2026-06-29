"""Cost-aware expected-value scorer for the capital allocator — M18 P1.

A **pure**, observe-only per-candidate score that ranks the full opportunity set
(the M18 P0b candidate batch) by **expected net R** rather than raw confidence.
It is the ``score_fn`` the allocator soak (M18 P0c) plugs in to make its regret
metric *cost-aware* — and the same scorer the eventual selector (M18 P2+) will
rank on. **Nothing here influences an order**; it only scores.

The score, in **R-units** (multiples of the trade's own stop-distance risk, so
cross-instrument candidates compare on one axis):

    EV_R = P_win · R_target − (1 − P_win) · 1.0 − fee_R − funding_R

- ``R_target = |tp − entry| / |entry − sl|`` — reward in R (the win pays this
  many multiples of the 1R stop).
- ``P_win`` — the candidate's calibrated win-probability proxy: its strategy
  confidence (``source_context['confidence']`` = the dominant conviction input
  ``c_strat``). A full conviction blend is a later refinement.
- ``fee_R = (fee_bps_roundtrip / 1e4) · |entry| / |entry − sl|`` — the
  **round-trip transaction cost** expressed in R (qty-independent). This is the
  cost term the per-cell live path never charges today (it's backtest-only); the
  fixed default is replaced by the **logged** per-trade fees once M18 P0a lands.
- ``funding_R`` — perp funding / prop swap in R; **0.0 by default** at P1 (it
  needs an expected hold-time, fed from the P0a capture). Carried as an input so
  the formula is complete and a caller can supply an estimate.

Fail-permissive: anything un-derivable (missing/zero risk distance, bad numbers)
→ ``None`` from :func:`candidate_ev_r`, and a deliberately very-low score from
:func:`candidate_ev_score` so an un-scorable candidate ranks last but never
raises and never strands a tick.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Round-trip transaction cost in basis points. Mirrors the backtest's
# ``FEE_BPS_ROUNDTRIP`` (``scripts/backtest_system.py``) as the fixed first-pass
# cost model; M18 P0a replaces it with logged per-trade fees per cell.
DEFAULT_FEE_BPS_ROUNDTRIP = 7.5

# Score returned for an un-scorable candidate — ranks strictly below any real
# EV (real EV_R is bounded well above this) without raising.
_UNSCORABLE = -1.0e9


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def compute_ev_r(
    *,
    entry: Any,
    sl: Any,
    tp: Any,
    p_win: Any,
    fee_bps_roundtrip: float = DEFAULT_FEE_BPS_ROUNDTRIP,
    funding_r: float = 0.0,
) -> Optional[float]:
    """Pure cost-aware expected net R for one candidate. ``None`` if un-derivable.

    See the module docstring for the formula. ``p_win`` is clamped to ``[0, 1]``;
    ``fee_bps_roundtrip`` / ``funding_r`` clamp negatives to 0 (a cost never adds
    edge). Never raises.
    """
    e, s, t = _f(entry), _f(sl), _f(tp)
    p = _f(p_win)
    if e is None or s is None or t is None or p is None:
        return None
    risk = abs(e - s)
    if risk <= 0.0:
        return None
    p = min(1.0, max(0.0, p))
    r_target = abs(t - e) / risk
    fee_bps = max(0.0, _f(fee_bps_roundtrip) or 0.0)
    fund = max(0.0, _f(funding_r) or 0.0)
    fee_r = (fee_bps / 1.0e4) * abs(e) / risk
    return p * r_target - (1.0 - p) * 1.0 - fee_r - fund


def candidate_p_win(candidate: Any) -> Optional[float]:
    """The candidate's win-probability proxy — its strategy confidence (c_strat)."""
    try:
        ctx = getattr(candidate, "source_context", None) or {}
        return _f(ctx.get("confidence"))
    except AttributeError:
        return None


def candidate_ev_r(
    candidate: Any,
    *,
    fee_bps_roundtrip: float = DEFAULT_FEE_BPS_ROUNDTRIP,
    funding_r: float = 0.0,
) -> Optional[float]:
    """Cost-aware EV_R for a ``SignalPackage`` candidate, or ``None``. Never raises."""
    try:
        return compute_ev_r(
            entry=getattr(candidate, "entry_price", None),
            sl=getattr(candidate, "stop_loss", None),
            tp=getattr(candidate, "take_profit", None),
            p_win=candidate_p_win(candidate),
            fee_bps_roundtrip=fee_bps_roundtrip,
            funding_r=funding_r,
        )
    except Exception:  # noqa: BLE001 — pure scorer must never raise into the soak/allocator
        logger.debug("candidate_ev_r: un-scorable candidate", exc_info=False)
        return None


def candidate_ev_score(candidate: Any) -> float:
    """``score_fn`` adapter for the allocator soak / selector.

    Returns the cost-aware EV_R; an un-scorable candidate gets a sentinel low
    score so it ranks last without raising. (The soak only fires on ≥ 2
    candidates, so the sentinel never spuriously wins.)
    """
    ev = candidate_ev_r(candidate)
    return ev if ev is not None else _UNSCORABLE
