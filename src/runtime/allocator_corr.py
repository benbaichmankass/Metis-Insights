"""Decision-time correlated-exposure features for the capital allocator.

M24 build-side lever for ``MB-20260629-ALLOC-CORR`` (design of record:
``docs/research/capital-allocation-ai-DESIGN.md`` M18 Phase 3, gap #2; consumed
by the M24 P3/P4 cost-aware EV scorer + within-tick net-R ranker per
``docs/research/M24-net-r-cost-aware-DESIGN.md``).

**The gap this closes.** Nothing live computes correlation or covariance between
the symbols the book holds. Two highly-correlated same-direction positions
(e.g. a BTCUSDT long and an ETHUSDT long) are each sized as an *independent*
``risk_pct`` trade, so the risk caps never see the **correlated** exposure as
one number — the portfolio is more concentrated than the per-trade sizing
believes. The cross-asset peer features (``config/cross_asset.yaml`` +
``src/runtime/cross_asset_live.py``) feed only the shadow regime heads; they are
not a portfolio-risk input.

This module is **pure** (stdlib only — no pandas/numpy, no DB, no I/O) and
**observe-only**: it computes correlation-aware exposure features from a
candidate + the currently-held book + recent per-symbol returns. **Nothing here
sizes or routes an order** — graduating any of these features to *influence* a
live size or the allocator's selection is Tier-3 (backtest-A/B-gated, operator-
approved), exactly like the rest of the M24 ladder.

Fail-permissive throughout, mirroring ``allocator_ev``: anything un-derivable
(too few return points, zero variance, missing fields, bad numbers) degrades to
``None``/``0.0`` and is reported via the coverage flags rather than raising into
the tick.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# Minimum aligned return observations before a Pearson correlation is trusted;
# below this the pair is reported as un-measured (``None``) rather than a noisy
# small-sample coefficient.
MIN_CORR_OBSERVATIONS = 20


def _f(x: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (shared shape with allocator_ev)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _direction_sign(direction: Any) -> Optional[int]:
    """+1 for a long, -1 for a short, ``None`` if undecipherable.

    Accepts the several spellings the codebase uses interchangeably
    (``long``/``short``, ``buy``/``sell``, ``+1``/``-1``).
    """
    if direction is None:
        return None
    s = str(direction).strip().lower()
    if s in ("long", "buy", "bid", "1", "+1"):
        return 1
    if s in ("short", "sell", "ask", "-1"):
        return -1
    return None


def pearson(a: Sequence[Any], b: Sequence[Any], *, min_obs: int = MIN_CORR_OBSERVATIONS) -> Optional[float]:
    """Pearson correlation of two return series over their aligned tail.

    The series are aligned on their **last** ``min(len(a), len(b))`` points (the
    most recent common history) and non-finite pairs are dropped. Returns
    ``None`` when fewer than ``min_obs`` clean pairs remain or either side has
    zero variance (an undefined correlation — reported, never faked as 0).
    Never raises.
    """
    try:
        n = min(len(a), len(b))
    except TypeError:
        return None
    if n == 0:
        return None
    xa = [_f(v) for v in list(a)[-n:]]
    xb = [_f(v) for v in list(b)[-n:]]
    xs = [(p, q) for p, q in zip(xa, xb) if p is not None and q is not None]
    if len(xs) < max(2, min_obs):
        return None
    m = len(xs)
    mean_x = sum(p for p, _ in xs) / m
    mean_y = sum(q for _, q in xs) / m
    cov = sum((p - mean_x) * (q - mean_y) for p, q in xs)
    var_x = sum((p - mean_x) ** 2 for p, _ in xs)
    var_y = sum((q - mean_y) ** 2 for _, q in xs)
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    denom = math.sqrt(var_x * var_y)
    if denom <= 0.0:
        return None
    r = cov / denom
    # Clamp to [-1, 1] against floating-point overshoot.
    return max(-1.0, min(1.0, r))


def pairwise_correlations(
    returns: Mapping[str, Sequence[Any]], *, min_obs: int = MIN_CORR_OBSERVATIONS
) -> dict[tuple[str, str], float]:
    """All measurable pairwise correlations over a ``{symbol: returns}`` map.

    Keyed by a sorted ``(sym_a, sym_b)`` tuple so a pair appears once; only pairs
    with a defined coefficient are included (an un-measurable pair is simply
    absent — the honest-coverage rule). Never raises.
    """
    out: dict[tuple[str, str], float] = {}
    syms = sorted(returns.keys())
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            r = pearson(returns[syms[i]], returns[syms[j]], min_obs=min_obs)
            if r is not None:
                out[(syms[i], syms[j])] = r
    return out


def _corr_lookup(
    returns: Mapping[str, Sequence[Any]], sym_a: str, sym_b: str, *, min_obs: int
) -> Optional[float]:
    if sym_a == sym_b:
        return 1.0
    ra = returns.get(sym_a)
    rb = returns.get(sym_b)
    if ra is None or rb is None:
        return None
    return pearson(ra, rb, min_obs=min_obs)


def correlated_exposure(
    *,
    candidate_symbol: str,
    candidate_direction: Any,
    candidate_risk: Any,
    open_positions: Sequence[Mapping[str, Any]],
    returns: Mapping[str, Sequence[Any]],
    min_obs: int = MIN_CORR_OBSERVATIONS,
) -> dict[str, Any]:
    """Decision-time correlated-exposure feature block for one candidate.

    ``open_positions`` is a list of ``{symbol, direction, risk}`` mappings (the
    book as it stands *before* this candidate); ``direction`` accepts the same
    spellings as the candidate. ``risk`` is the position's own risk basis in a
    consistent unit (``risk_usd`` preferred; notional works if used uniformly);
    a missing/bad risk falls back to ``1.0`` so a book with no risk figures still
    yields a **count-based** concentration read rather than nothing.

    Returns (all values ``None`` when un-derivable, never a fabricated ``0`` that
    would read as "measured and flat"):

    - ``max_abs_corr`` — largest |corr| between the candidate and any held symbol.
    - ``corr_weighted_aligned_risk`` — Σ over the book of
      ``corr(cand, pos) · align · pos_risk`` where ``align`` is ``+1`` when the
      correlation-adjusted position points the **same** market way as the
      candidate and ``-1`` when it hedges. Positive ⇒ the candidate *adds* to a
      correlated directional bet the caps don't see; negative ⇒ it diversifies.
    - ``corr_concentration`` — ``corr_weighted_aligned_risk / candidate_risk``
      (in candidate-risk units; how many extra "R-of-the-same-bet" the book
      already carries).
    - ``effective_independent_bets`` — ``1 + Σ (1 − |corr|)`` over held symbols:
      a crude count of how many *independent* directional bets the book+candidate
      represent (held symbols perfectly correlated with the candidate add ~0).
    - ``n_book`` / ``n_book_measured`` — book size and how many held symbols had a
      measurable correlation (the coverage denominator).
    """
    cand_sign = _direction_sign(candidate_direction)
    cand_risk = _f(candidate_risk)
    if cand_risk is not None and cand_risk <= 0.0:
        cand_risk = None

    positions = list(open_positions or [])
    n_book = len(positions)
    base: dict[str, Any] = {
        "candidate_symbol": candidate_symbol,
        "n_book": n_book,
        "n_book_measured": 0,
        "max_abs_corr": None,
        "corr_weighted_aligned_risk": None,
        "corr_concentration": None,
        "effective_independent_bets": None,
    }
    if n_book == 0 or cand_sign is None:
        # No book, or an undecipherable candidate direction: a candidate against
        # an empty book is exactly one independent bet.
        if cand_sign is not None and n_book == 0:
            base["effective_independent_bets"] = 1.0
            base["corr_weighted_aligned_risk"] = 0.0
            base["corr_concentration"] = 0.0
        return base

    max_abs = 0.0
    measured = 0
    weighted = 0.0
    indep = 1.0
    for pos in positions:
        try:
            sym = str(pos.get("symbol"))
        except AttributeError:
            continue
        r = _corr_lookup(returns, candidate_symbol, sym, min_obs=min_obs)
        if r is None:
            continue
        measured += 1
        pos_sign = _direction_sign(pos.get("direction"))
        pos_risk = _f(pos.get("risk"))
        if pos_risk is None or pos_risk <= 0.0:
            pos_risk = 1.0
        max_abs = max(max_abs, abs(r))
        indep += 1.0 - abs(r)
        if pos_sign is not None:
            # align: same market direction (after sign of correlation) ⇒ +1.
            align = cand_sign * pos_sign * (1.0 if r >= 0 else -1.0)
            weighted += abs(r) * align * pos_risk
    base["n_book_measured"] = measured
    if measured == 0:
        return base
    base["max_abs_corr"] = max_abs
    base["corr_weighted_aligned_risk"] = weighted
    base["effective_independent_bets"] = indep
    if cand_risk is not None:
        base["corr_concentration"] = weighted / cand_risk
    return base


# --- Thin, fail-permissive adapters over the runtime objects ---------------

def _candidate_risk(candidate: Any) -> Optional[float]:
    """The candidate's stop-distance risk basis, mirroring allocator_ev's R math.

    ``|entry − stop| × qty`` when both are readable (money-risk); falls back to
    ``|entry − stop|`` (per-unit risk) so a candidate with no qty still carries a
    consistent basis. ``None`` if the stop distance is un-derivable.
    """
    entry = _f(getattr(candidate, "entry_price", None))
    stop = _f(getattr(candidate, "stop_loss", None))
    if entry is None or stop is None:
        return None
    dist = abs(entry - stop)
    if dist <= 0.0:
        return None
    qty = _f(getattr(candidate, "qty", None))
    if qty is not None and qty > 0.0:
        return dist * qty
    return dist


def candidate_correlated_exposure(
    candidate: Any,
    open_positions: Sequence[Mapping[str, Any]],
    returns: Mapping[str, Sequence[Any]],
    *,
    min_obs: int = MIN_CORR_OBSERVATIONS,
) -> dict[str, Any]:
    """``correlated_exposure`` adapter for a ``SignalPackage``-shaped candidate.

    Reads ``symbol`` + a direction (``direction`` or ``side``) + the stop-distance
    risk off the candidate defensively; never raises (an unreadable candidate
    yields the empty-book/undecipherable feature block).
    """
    try:
        symbol = str(getattr(candidate, "symbol", None))
        direction = getattr(candidate, "direction", None)
        if direction is None:
            direction = getattr(candidate, "side", None)
        return correlated_exposure(
            candidate_symbol=symbol,
            candidate_direction=direction,
            candidate_risk=_candidate_risk(candidate),
            open_positions=open_positions,
            returns=returns,
            min_obs=min_obs,
        )
    except Exception:  # noqa: BLE001 — pure feature builder must never raise into the tick
        logger.debug("candidate_correlated_exposure: un-featurable candidate", exc_info=False)
        return {
            "candidate_symbol": None,
            "n_book": 0,
            "n_book_measured": 0,
            "max_abs_corr": None,
            "corr_weighted_aligned_risk": None,
            "corr_concentration": None,
            "effective_independent_bets": None,
        }
