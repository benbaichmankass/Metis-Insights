"""Sizing + per-leg protective-geometry for the market-neutral pairs sleeve (M22 D2).

Pure, deterministic money-math for the 2-leg executor — no I/O, no exchange, no
accounts. Split out from the executor so it can be unit-tested exhaustively.

**Sizing model (derivation).** For a pair with log-spread ``s = logA − β·logB``:
a *long_spread* trade is long A (notional ``N_A``) + short B (notional
``N_B = β·N_A``); its P&L for a small spread move ``ds`` is

    P&L = N_A·dlogA − β·N_A·dlogB = N_A·(dlogA − β·dlogB) = N_A · ds

(and *short_spread* is the mirror, P&L = −N_A·ds). The divergence stop is a spread
move of ``risk_spread = stop_z·std`` (log-spread units), so to risk exactly
``risk_budget_usd`` at the stop:

    N_A = risk_budget_usd / risk_spread ;  qty_a = N_A / price_a ;  qty_b = β·N_A / price_b

This makes the live $-risk-per-trade equal to the account's risk basis at the same
spread stop the backtest used — the "realistic as-if-live" sizing.

**Per-leg protective levels are a CATASTROPHE BACKSTOP, not the exit.** The real
exit is spread-level (the executor closes BOTH legs on revert/stop/timeout). But
linear-perp opens require an SL+TP on the order, so each leg carries a WIDE
per-leg stop at ``backstop_mult × risk_spread`` in that leg's own log-price — it
only fires if a single leg moves that far on its own (i.e. the executor failed to
manage the spread exit). A leg-price SL firing closes only one leg, leaving the
other naked — which the executor's leg-imbalance guard must then flatten; the
backstop is the last-resort net, deliberately far from the spread exit.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Tuple


def pair_notionals(risk_budget_usd: float, risk_spread: float, beta: float,
                   price_a: float, price_b: float) -> Dict[str, float]:
    """Return {qty_a, qty_b, notional_a_usd, notional_b_usd, n_a_usd} for a pair.
    Zeroed dict if any input is non-positive/degenerate (a per-trade refusal)."""
    zero = {"qty_a": 0.0, "qty_b": 0.0, "notional_a_usd": 0.0,
            "notional_b_usd": 0.0, "n_a_usd": 0.0}
    if not (risk_budget_usd > 0 and risk_spread > 0 and price_a > 0 and price_b > 0):
        return dict(zero)
    b = abs(float(beta))
    if b <= 0 or not math.isfinite(b):
        b = 1.0
    n_a = risk_budget_usd / risk_spread          # leg-A notional in USD
    n_b = b * n_a                                 # leg-B notional in USD (β-hedged)
    return {
        "qty_a": n_a / price_a,
        "qty_b": n_b / price_b,
        "notional_a_usd": n_a,
        "notional_b_usd": n_b,
        "n_a_usd": n_a,
    }


# Bounds on the per-leg catastrophe backstop's log-price displacement. This
# level only guards a STRANDED position (the real exit is the spread-z monitor),
# so a bounded-wide band is correct — and a hard bound is necessary: risk_spread
# is a LOG-spread stop (engine: s = logA − β·logB), and a degenerate/inflated
# spread std (e.g. an unstable rolling β on a short lookback) blows it up to
# O(1), so exp(backstop_mult · risk) explodes into a nonsensical level. That was
# the SOLUSDT/BTCUSDT paper pairs order the venue rejected: risk≈5.4 →
# exp(3·5.4)=e^16 → takeProfit ≈ $1.5e9 and stopLoss rounded to $0.000
# (BL-20260716-PAIRS-BACKSTOP-EXPLODE). Clamp the displacement to a sane band.
_MAX_BACKSTOP_LOG_MOVE = math.log(2.0)    # cap: leg backstop never worse than +100% / −50%
_MIN_BACKSTOP_LOG_MOVE = math.log(1.02)   # floor: SL/TP never collapses onto the entry price


def leg_protective_levels(direction: str, entry_price: float, risk_spread: float,
                          backstop_mult: float = 3.0) -> Tuple[float, float]:
    """(sl, tp) catastrophe-backstop levels for ONE leg, given the leg's own
    entry direction ('long'|'short'), its entry price, and the spread stop in
    log units. The backstop sits ``backstop_mult × risk_spread`` away in log-price
    — wide of the spread exit on purpose, but the displacement is clamped to
    ``[_MIN_BACKSTOP_LOG_MOVE, _MAX_BACKSTOP_LOG_MOVE]`` so a degenerate
    ``risk_spread`` can never emit an exploded/collapsed level the venue rejects.
    Returns (0.0, 0.0) on degenerate input."""
    if not (entry_price > 0 and risk_spread > 0 and backstop_mult > 0):
        return (0.0, 0.0)
    move = float(backstop_mult) * float(risk_spread)   # log-price displacement
    move = min(max(move, _MIN_BACKSTOP_LOG_MOVE), _MAX_BACKSTOP_LOG_MOVE)
    up = entry_price * math.exp(move)
    dn = entry_price * math.exp(-move)
    if direction == "long":
        return (round(dn, 8), round(up, 8))            # SL below, TP above
    return (round(up, 8), round(dn, 8))                # short: SL above, TP below


def _floor_to_step(qty: float, step: float) -> float:
    """Floor *qty* DOWN to a multiple of *step* (never up — realised risk must not
    exceed the sized cap). ``step <= 0`` ⇒ passthrough. Kills FP dust."""
    if step <= 0:
        return float(qty)
    return round(math.floor(float(qty) / step + 1e-12) * step, 12)


def min_viable_scale(qty_a: float, qty_b: float, *,
                     min_a: float, min_b: float) -> float:
    """Common scale factor ``k >= 1`` that lifts BOTH legs to their venue minimum
    while PRESERVING the β-hedge (both legs multiplied by the SAME k, so the
    ``qty_b : qty_a`` ratio — the hedge — is untouched).

    This is the crux of the min-qty-aware sizer (G1): the #6591 gate correctly
    REFUSES a sub-min pair (never half-places), but refuse-everything ≠ tradeable.
    On a real (small) balance the ideal β-hedged size floors one leg below its
    minimum lot; scaling the *whole pair* up by k just clears the binding leg. The
    caller then decides whether k is affordable (``k × budget`` within the account
    risk tolerance) — this function only computes the geometry, never the policy.

    ``k == 1.0`` when the pair is already viable. Degenerate/≤0 quantities return
    ``1.0`` (the caller's own refusal handles those)."""
    k = 1.0
    if qty_a > 0 and min_a > 0 and qty_a < min_a:
        k = max(k, min_a / qty_a)
    if qty_b > 0 and min_b > 0 and qty_b < min_b:
        k = max(k, min_b / qty_b)
    return float(k)


def plan_pair_sizing(risk_budget_usd: float, risk_spread: float, beta: float,
                     price_a: float, price_b: float, *,
                     step_a: float, min_a: float, step_b: float, min_b: float,
                     max_risk_multiple: float = 1.0) -> Dict[str, Any]:
    """Min-qty-aware pair sizer (G1). Returns a decision dict:

        {ok, reason, qty_a, qty_b, risk_multiple, notional_a_usd, notional_b_usd,
         ideal_qty_a, ideal_qty_b, scaled}

    Flow: size the ideal β-hedged pair (``pair_notionals``); step-floor each leg;
    if both already clear their min → place as-is (``risk_multiple = 1.0``). Else
    compute the min-viable scale ``k`` (``min_viable_scale``) and, **only if
    ``k <= max_risk_multiple``**, scale BOTH legs by k (hedge preserved),
    re-floor, and place — the realised $-risk is ``k × risk_budget_usd`` so
    ``risk_multiple`` records the inflation the caller journals. If ``k`` exceeds
    the tolerance, or a leg is *still* sub-min after scaling+flooring, → refuse
    (``ok=False``).

    **``max_risk_multiple`` defaults to 1.0 → behaviour identical to the #6591
    both-legs-or-nothing skip** (no scaling; any sub-min pair refuses). Raising it
    is the Tier-3 policy the operator sets once the G2 $-and-lots evidence shows a
    safe value; the geometry is inert until then. Never raises."""
    refuse = {
        "ok": False, "reason": "", "qty_a": 0.0, "qty_b": 0.0,
        "risk_multiple": 0.0, "notional_a_usd": 0.0, "notional_b_usd": 0.0,
        "ideal_qty_a": 0.0, "ideal_qty_b": 0.0, "scaled": False,
    }
    base = pair_notionals(risk_budget_usd, risk_spread, beta, price_a, price_b)
    ideal_a, ideal_b = base["qty_a"], base["qty_b"]
    if not (ideal_a > 0 and ideal_b > 0):
        return {**refuse, "reason": "degenerate_size"}

    def _both_clear(qa: float, qb: float) -> bool:
        fa, fb = _floor_to_step(qa, step_a), _floor_to_step(qb, step_b)
        return fa >= min_a - 1e-12 and fa > 0 and fb >= min_b - 1e-12 and fb > 0

    # already viable at the nominal size?
    if _both_clear(ideal_a, ideal_b):
        fa, fb = _floor_to_step(ideal_a, step_a), _floor_to_step(ideal_b, step_b)
        return {"ok": True, "reason": "viable", "qty_a": fa, "qty_b": fb,
                "risk_multiple": 1.0, "notional_a_usd": fa * price_a,
                "notional_b_usd": fb * price_b, "ideal_qty_a": ideal_a,
                "ideal_qty_b": ideal_b, "scaled": False}

    k = min_viable_scale(ideal_a, ideal_b, min_a=min_a, min_b=min_b)
    if k > float(max_risk_multiple) + 1e-9:
        return {**refuse, "reason": "min_viable_exceeds_risk_tolerance",
                "risk_multiple": round(k, 4), "ideal_qty_a": ideal_a,
                "ideal_qty_b": ideal_b}
    sa, sb = ideal_a * k, ideal_b * k
    if not _both_clear(sa, sb):
        # scaling to the binding leg's min still left the OTHER leg sub-min after
        # step-flooring (a coarse-lot corner) — refuse rather than half-place.
        return {**refuse, "reason": "below_venue_min_qty_after_scale",
                "risk_multiple": round(k, 4), "ideal_qty_a": ideal_a,
                "ideal_qty_b": ideal_b}
    fa, fb = _floor_to_step(sa, step_a), _floor_to_step(sb, step_b)
    return {"ok": True, "reason": "scaled_to_min_viable", "qty_a": fa, "qty_b": fb,
            "risk_multiple": round(k, 4), "notional_a_usd": fa * price_a,
            "notional_b_usd": fb * price_b, "ideal_qty_a": ideal_a,
            "ideal_qty_b": ideal_b, "scaled": True}


def correlation_haircut(n_correlated_open: int, factor: float = 0.5) -> float:
    """Multiplicative risk haircut when N other correlated pairs (sharing a leg,
    e.g. the BTC leg) are already open. ``factor`` in (0,1]; haircut =
    factor**n_correlated_open (compounding), clamped to [0,1]. n=0 → 1.0 (full
    size). A conservative default halves risk per already-open correlated pair."""
    if n_correlated_open <= 0:
        return 1.0
    f = min(max(float(factor), 0.0), 1.0)
    return f ** int(n_correlated_open)
