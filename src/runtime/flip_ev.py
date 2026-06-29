"""Fee-aware expected-value gate for ``FLIP_POLICY=selective`` (Unit A).

Design source of truth: ``docs/research/pnl-optimal-conflict-resolution-DESIGN.md``
§ 7.1(b) — the decisive inequality that decides whether closing the held trend
to take a counter-signal scalp is *profitable net of the full round-trip cost*.

> **Tier-3 — order-routing-affecting.** This module is pure arithmetic and is
> only consulted when ``FLIP_POLICY=selective`` (opt-in, default ``hold``). It
> never sends an order; it returns a decision the caller acts on. Promotion to a
> live ``FLIP_POLICY=selective`` flip on a real-money account is gated on the
> walk-forward backtest PASS + explicit operator approval (§ 7.4 / § 8).

The flip is **four fills**, not two — that is the whole point of the gate:

    close H  →  open N  →  close N  →  re-open H

A high-confidence scalp into a *small* TP, taken against a *large* held trend,
loses money because re-entering the big trend twice (close H + re-open H) costs
more in fees than the small scalp can earn. The gate makes that the answer.

The inequality (§ 7.1(b)), with one-way fill cost fraction ``f`` (half the
configured round-trip bps), ``R_N = |tp_N − p_N|·q_N`` (scalp reward),
``risk_N = |p_N − sl_N|·q_N`` (scalp risk), ``P_win = c_N`` (calibrated
confidence as a win-probability proxy):

    EV_flip = P_win·R_N − (1 − P_win)·risk_N
              − f·( notional_H + notional_N + notional_N + notional_H )
    flip allowed iff  EV_flip ≥ FLIP_EV_MARGIN_USD

i.e. the scalp's expected edge must clear ``f·(2·notional_H + 2·notional_N)``.

Everything here is a pure function so the live path (``intents.py``) and the
backtest arm (``scripts/backtest_system.py``) can share *identical* math —
the faithful-twin invariant the whole flip-policy machinery already relies on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional


# Default one-way fee, expressed as the round-trip bps the backtester uses
# (``FEE_BPS_ROUNDTRIP=7.5`` in ``scripts/backtest_system.py``). The live path
# has no single canonical fee constant, so the EV gate accepts the round-trip
# bps as a parameter and halves it to the one-way fraction internally; the
# default here mirrors the backtester so an un-parameterised call matches the
# research arm.
_DEFAULT_FEE_BPS_ROUNDTRIP: float = 7.5


def resolve_flip_ev_margin(settings: Optional[Dict[str, Any]] = None) -> float:
    """Minimum ``EV_flip`` (USD) required to allow a selective flip.

    Default ``0.0`` — the flip is allowed as soon as its fee-aware EV is
    non-negative. A positive margin (e.g. ``5.0``) demands a $5 expected
    edge after all four fills before displacing the held trend.

    Resolution order: ``settings["FLIP_EV_MARGIN_USD"]`` → ``FLIP_EV_MARGIN_USD``
    env var → ``0.0``. A non-numeric value falls back to ``0.0`` (never raises,
    so a typo on the VM can't strand the order path). Negative margins are
    honoured (they loosen the gate) — only parse failures reset to 0.

    Tier-3: changing this on the live VM changes live order routing.
    """
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("FLIP_EV_MARGIN_USD")
    if raw is None:
        raw = os.environ.get("FLIP_EV_MARGIN_USD", "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def resolve_fee_bps_roundtrip(settings: Optional[Dict[str, Any]] = None) -> float:
    """Round-trip fee in bps used by the EV gate (one-way = this / 2).

    Resolution: ``settings["FEE_BPS_ROUNDTRIP"]`` → ``FEE_BPS_ROUNDTRIP`` env
    var → ``7.5`` (mirrors ``scripts/backtest_system.py``). Non-numeric or
    non-positive → the default, so the gate always charges *some* cost (a
    zero/negative fee would make every flip look free and defeat the gate).
    """
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("FEE_BPS_ROUNDTRIP")
    if raw is None:
        raw = os.environ.get("FEE_BPS_ROUNDTRIP", "")
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_FEE_BPS_ROUNDTRIP
    except (TypeError, ValueError):
        return _DEFAULT_FEE_BPS_ROUNDTRIP


@dataclass(frozen=True)
class FlipEv:
    """Result of the fee-aware EV computation (§ 7.1(b)).

    Fields
    ------
    ev : float
        ``EV_flip`` in USD — the scalp's expected edge minus the four-fill cost.
    reward : float
        ``R_N = |tp_N − p_N|·q_N`` (the scalp's reward leg, USD).
    risk : float
        ``risk_N = |p_N − sl_N|·q_N`` (the scalp's risk leg, USD).
    p_win : float
        Win-probability proxy used (clamped ``c_N`` into ``[0, 1]``).
    fee_cost : float
        ``f·(2·notional_H + 2·notional_N)`` — the total four-fill fee, USD.
    notional_h, notional_n : float
        The held-trend and scalp notionals the fee was charged on.
    computable : bool
        ``False`` when an input was missing/non-finite so the EV couldn't be
        formed (caller treats this as "cannot confirm profitable → don't flip").
    reason : str
        Single-line human-readable explanation for the audit trail.
    """

    ev: float
    reward: float
    risk: float
    p_win: float
    fee_cost: float
    notional_h: float
    notional_n: float
    computable: bool
    reason: str


def _finite_positive(x: Optional[float]) -> Optional[float]:
    """Return ``float(x)`` if it is a finite, non-negative number, else None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return v if v >= 0 else None


def compute_flip_ev(
    *,
    held_notional: Optional[float],
    scalp_entry: Optional[float],
    scalp_sl: Optional[float],
    scalp_tp: Optional[float],
    scalp_qty: Optional[float],
    scalp_confidence: Optional[float],
    fee_bps_roundtrip: Optional[float] = None,
    ev_margin_usd: Optional[float] = None,
) -> FlipEv:
    """Compute ``EV_flip`` for the four-fill round trip (§ 7.1(b)).

    Parameters
    ----------
    held_notional : float
        ``notional_H`` — the held trend's notional in USD (``|qty_H|·price_H``).
        Charged twice (close H + re-open H).
    scalp_entry, scalp_sl, scalp_tp : float
        ``p_N`` / ``sl_N`` / ``tp_N`` — the incoming counter-signal's entry,
        stop, and target prices.
    scalp_qty : float
        ``q_N`` — the scalp's sized qty (base units). ``notional_N = p_N·q_N``,
        charged twice (open N + close N).
    scalp_confidence : float
        ``c_N`` — calibrated confidence, used as ``P_win`` (clamped to [0, 1]).
    fee_bps_roundtrip : float, optional
        Round-trip fee in bps; one-way fraction ``f = bps / 10_000 / 2``.
        Defaults via ``resolve_fee_bps_roundtrip``.
    ev_margin_usd : float, optional
        Recorded on the result's ``reason`` only — the *decision* (compare EV to
        the margin) is the caller's job via ``flip_ev_passes``. Defaults via
        ``resolve_flip_ev_margin`` for the reason string.

    Returns
    -------
    FlipEv
        ``computable=False`` whenever any required input is missing / non-finite
        / a degenerate scalp (zero stop distance or zero qty), so the caller
        keeps the position (fail-safe: an unprovable flip never fires).
    """
    fee_bps = resolve_fee_bps_roundtrip() if fee_bps_roundtrip is None else float(fee_bps_roundtrip)
    if fee_bps <= 0:
        fee_bps = _DEFAULT_FEE_BPS_ROUNDTRIP
    one_way_f = (fee_bps / 10_000.0) / 2.0
    margin = resolve_flip_ev_margin() if ev_margin_usd is None else float(ev_margin_usd)

    h_notional = _finite_positive(held_notional)
    entry = _finite_positive(scalp_entry)
    sl = _finite_positive(scalp_sl)
    tp = _finite_positive(scalp_tp)
    qty = _finite_positive(scalp_qty)
    conf_raw = scalp_confidence

    missing = []
    if h_notional is None:
        missing.append("held_notional")
    if entry is None:
        missing.append("scalp_entry")
    if sl is None:
        missing.append("scalp_sl")
    if tp is None:
        missing.append("scalp_tp")
    if qty is None or qty <= 0:
        missing.append("scalp_qty")
    if missing:
        return FlipEv(
            ev=0.0, reward=0.0, risk=0.0, p_win=0.0, fee_cost=0.0,
            notional_h=h_notional or 0.0, notional_n=0.0,
            computable=False,
            reason=f"ev_not_computable: missing/invalid {','.join(missing)}",
        )

    stop_dist = abs(entry - sl)
    reward_dist = abs(tp - entry)
    if stop_dist <= 0:
        return FlipEv(
            ev=0.0, reward=0.0, risk=0.0, p_win=0.0, fee_cost=0.0,
            notional_h=h_notional, notional_n=entry * qty,
            computable=False,
            reason="ev_not_computable: degenerate scalp (zero stop distance)",
        )

    try:
        p_win = float(conf_raw)
    except (TypeError, ValueError):
        return FlipEv(
            ev=0.0, reward=0.0, risk=0.0, p_win=0.0, fee_cost=0.0,
            notional_h=h_notional, notional_n=entry * qty,
            computable=False,
            reason="ev_not_computable: missing/invalid scalp_confidence",
        )
    p_win = max(0.0, min(1.0, p_win))

    reward = reward_dist * qty          # R_N
    risk = stop_dist * qty              # risk_N
    n_notional = entry * qty            # notional_N
    # Four fills: close H + open N + close N + re-open H.
    fee_cost = one_way_f * (h_notional + n_notional + n_notional + h_notional)
    ev = (p_win * reward) - ((1.0 - p_win) * risk) - fee_cost

    return FlipEv(
        ev=ev,
        reward=reward,
        risk=risk,
        p_win=p_win,
        fee_cost=fee_cost,
        notional_h=h_notional,
        notional_n=n_notional,
        computable=True,
        reason=(
            f"EV_flip={ev:.4f} (P_win={p_win:.3f}·R_N={reward:.4f} "
            f"− {1.0 - p_win:.3f}·risk_N={risk:.4f} − fee={fee_cost:.4f}) "
            f"vs margin={margin:.4f} [f={one_way_f:.6f} "
            f"notional_H={h_notional:.2f} notional_N={n_notional:.2f}]"
        ),
    )


def flip_ev_passes(
    flip_ev: FlipEv,
    *,
    ev_margin_usd: Optional[float] = None,
) -> bool:
    """True iff the flip is computable AND ``EV_flip ≥ FLIP_EV_MARGIN_USD``.

    An un-computable EV (missing input / degenerate scalp) is **never** a pass
    — the gate is fail-safe: it must positively confirm the flip is profitable
    before displacing a held position, never flip on an unknown.
    """
    if not flip_ev.computable:
        return False
    margin = resolve_flip_ev_margin() if ev_margin_usd is None else float(ev_margin_usd)
    return flip_ev.ev >= margin
