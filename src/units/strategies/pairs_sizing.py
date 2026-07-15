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
from typing import Dict, Tuple


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


def leg_protective_levels(direction: str, entry_price: float, risk_spread: float,
                          backstop_mult: float = 3.0) -> Tuple[float, float]:
    """(sl, tp) catastrophe-backstop levels for ONE leg, given the leg's own
    entry direction ('long'|'short'), its entry price, and the spread stop in
    log units. The backstop sits ``backstop_mult × risk_spread`` away in log-price
    — wide of the spread exit on purpose. Returns (0.0, 0.0) on degenerate input."""
    if not (entry_price > 0 and risk_spread > 0 and backstop_mult > 0):
        return (0.0, 0.0)
    move = float(backstop_mult) * float(risk_spread)   # log-price displacement
    up = entry_price * math.exp(move)
    dn = entry_price * math.exp(-move)
    if direction == "long":
        return (round(dn, 8), round(up, 8))            # SL below, TP above
    return (round(up, 8), round(dn, 8))                # short: SL above, TP below


def correlation_haircut(n_correlated_open: int, factor: float = 0.5) -> float:
    """Multiplicative risk haircut when N other correlated pairs (sharing a leg,
    e.g. the BTC leg) are already open. ``factor`` in (0,1]; haircut =
    factor**n_correlated_open (compounding), clamped to [0,1]. n=0 → 1.0 (full
    size). A conservative default halves risk per already-open correlated pair."""
    if n_correlated_open <= 0:
        return 1.0
    f = min(max(float(factor), 0.0), 1.0)
    return f ** int(n_correlated_open)
