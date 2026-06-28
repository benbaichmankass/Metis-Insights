"""Strike/expiry selector — turn an equity signal into a 1-wide debit vertical.

Phase-1 Slice-3a of the Alpaca L3 options build (docs/research/alpaca-options-PHASE1-spec.md).

Baseline architecture (operator decision 2026-06-27): **overlay on the existing
equity signals**. When an equity strategy fires a direction on an underlying, this
selector expresses it as a defined-risk DEBIT VERTICAL on that underlying's option
chain — the cheapest structure that fits the $150 cash account (single longs are too
expensive; the short leg subsidises the cost). Options-specific signals come later.

  - bullish (long)  -> BULL CALL debit spread: buy ~ATM call, sell the next call up.
  - bearish (short) -> BEAR PUT  debit spread: buy ~ATM put,  sell the next put down.

PURE — no I/O. The wiring layer (Slice 3b, operator-gated) builds the normalised
``ChainContract`` list by joining ``AlpacaOptionsData.list_option_contracts`` (strike /
expiry / type) with ``.snapshots`` (mid / greeks / IV), calls ``select_debit_vertical``,
sizes the result with ``options_sizing.size_debit_structure``, and submits the legs via
``AlpacaOptionsExecutor.place_spread`` (``to_option_legs``). This module places nothing.

IV-RANK GATE (honest limit): debit structures want LOW implied vol (cheap to buy).
True IV-rank needs a trailing-IV history this repo does not yet store, so the gate here
is **opt-in** — pass ``iv_rank`` + ``max_iv_rank`` when a caller can compute it; absent
that, selection proceeds ungated (and says so in ``reason``). Building the trailing-IV
store is a later slice.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import List, Optional

from src.units.accounts.alpaca_options_exec import OptionLeg

OPTION_MULTIPLIER = 100


@dataclass(frozen=True)
class ChainContract:
    """One option contract, normalised from contract metadata + its snapshot.

    ``mid`` / ``delta`` / ``iv`` are Optional — a contract with no quote (``mid`` None)
    is unusable as a leg and is skipped.
    """

    symbol: str          # OCC symbol
    type: str            # "call" | "put"
    strike: float
    expiration: str      # ISO date "YYYY-MM-DD"
    mid: Optional[float] = None
    delta: Optional[float] = None
    iv: Optional[float] = None
    open_interest: Optional[int] = None


@dataclass(frozen=True)
class DebitVertical:
    """A selected 1-wide (or n-wide) debit vertical, or a refusal (``ok=False``)."""

    ok: bool
    reason: Optional[str] = None
    long_leg: Optional[ChainContract] = None
    short_leg: Optional[ChainContract] = None
    width: float = 0.0
    net_debit: float = 0.0          # per-share debit paid (long.mid - short.mid)
    max_loss_usd: float = 0.0       # net_debit * 100
    max_gain_usd: float = 0.0       # (width - net_debit) * 100
    breakeven: Optional[float] = None
    expiration: Optional[str] = None
    dte: Optional[int] = None


def _dte(expiration: str, today: _dt.date) -> Optional[int]:
    try:
        d = _dt.date.fromisoformat(expiration)
    except (ValueError, TypeError):
        return None
    return (d - today).days


def _pick_expiration(
    contracts: List[ChainContract], today: _dt.date, target_dte: int,
    min_dte: int, max_dte: int,
) -> Optional[str]:
    """Choose the expiration whose DTE is in [min,max] and closest to target."""
    best: Optional[str] = None
    best_gap = None
    for exp in {c.expiration for c in contracts}:
        d = _dte(exp, today)
        if d is None or d < min_dte or d > max_dte:
            continue
        gap = abs(d - target_dte)
        if best_gap is None or gap < best_gap:
            best, best_gap = exp, gap
    return best


def select_debit_vertical(
    contracts: List[ChainContract],
    *,
    direction: str,
    underlying_price: float,
    today: _dt.date,
    target_dte: int = 35,
    min_dte: int = 21,
    max_dte: int = 60,
    iv_rank: Optional[float] = None,
    max_iv_rank: Optional[float] = None,
) -> DebitVertical:
    """Select a debit vertical expressing *direction* on the chain. Pure; never raises.

    ``direction`` ∈ {long, buy, bull, short, sell, bear}. ``underlying_price`` anchors the
    ~ATM long strike; the short leg is the next strike in the profit direction (width =
    the actual strike gap, so $1 or $5 spacing is handled automatically). Returns a
    refusal (``ok=False`` + ``reason``) when no valid expiry/strikes/quotes exist or the
    optional IV-rank gate fails.
    """
    d = str(direction).strip().lower()
    if d in ("long", "buy", "bull", "bullish"):
        opt_type, up = "call", True
    elif d in ("short", "sell", "bear", "bearish"):
        opt_type, up = "put", False
    else:
        return DebitVertical(False, reason=f"unknown_direction:{direction!r}")

    if iv_rank is not None and max_iv_rank is not None and iv_rank > max_iv_rank:
        return DebitVertical(False, reason=f"iv_rank_too_high:{iv_rank:.2f}>{max_iv_rank:.2f}")

    typed = [c for c in contracts if str(c.type).lower() == opt_type]
    if not typed:
        return DebitVertical(False, reason=f"no_{opt_type}_contracts")

    exp = _pick_expiration(typed, today, target_dte, min_dte, max_dte)
    if exp is None:
        return DebitVertical(False, reason="no_expiration_in_dte_band")

    # Quotable legs in the chosen expiration, sorted by strike.
    leg_pool = sorted(
        (c for c in typed if c.expiration == exp and c.mid is not None and c.mid > 0),
        key=lambda c: c.strike,
    )
    if len(leg_pool) < 2:
        return DebitVertical(False, reason="fewer_than_two_quotable_strikes")

    # Long leg = strike nearest the underlying (~ATM).
    long_leg = min(leg_pool, key=lambda c: abs(c.strike - underlying_price))
    # Short leg = next quotable strike in the profit direction (up for calls, down for puts).
    if up:
        higher = [c for c in leg_pool if c.strike > long_leg.strike]
        short_leg = min(higher, key=lambda c: c.strike) if higher else None
    else:
        lower = [c for c in leg_pool if c.strike < long_leg.strike]
        short_leg = max(lower, key=lambda c: c.strike) if lower else None
    if short_leg is None:
        return DebitVertical(False, reason="no_short_strike_in_profit_direction")

    width = round(abs(short_leg.strike - long_leg.strike), 4)
    net_debit = round(float(long_leg.mid) - float(short_leg.mid), 4)
    if net_debit <= 0:
        # A non-positive debit isn't a debit spread (mis-quote / inverted) — refuse.
        return DebitVertical(False, reason=f"non_positive_debit:{net_debit}")
    if net_debit >= width:
        # Paying >= the width leaves no upside — not a tradeable debit vertical.
        return DebitVertical(False, reason=f"debit_ge_width:{net_debit}>={width}")

    breakeven = (
        round(long_leg.strike + net_debit, 4) if up
        else round(long_leg.strike - net_debit, 4)
    )
    return DebitVertical(
        ok=True,
        reason="ungated_iv" if (iv_rank is None or max_iv_rank is None) else None,
        long_leg=long_leg,
        short_leg=short_leg,
        width=width,
        net_debit=net_debit,
        max_loss_usd=round(net_debit * OPTION_MULTIPLIER, 2),
        max_gain_usd=round((width - net_debit) * OPTION_MULTIPLIER, 2),
        breakeven=breakeven,
        expiration=exp,
        dte=_dte(exp, today),
    )


def to_option_legs(vertical: DebitVertical) -> List[OptionLeg]:
    """Convert a selected vertical into the executor's open legs (buy long / sell short).

    Composes Slice-3a (selection) with Slice-2 (execution). Raises ValueError on a
    refusal so a caller never tries to place an un-selected order.
    """
    if not vertical.ok or vertical.long_leg is None or vertical.short_leg is None:
        raise ValueError(f"cannot build legs from a refused selection: {vertical.reason}")
    return [
        OptionLeg(vertical.long_leg.symbol, "buy", "buy_to_open", 1),
        OptionLeg(vertical.short_leg.symbol, "sell", "sell_to_open", 1),
    ]
