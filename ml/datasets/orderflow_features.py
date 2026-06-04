"""Order-flow / microstructure feature estimators (S-MLOPT-S10, M14 Phase 2.2).

Microstructure flow is the highest proven-ROI feature family after range-vol —
but it needs L1/L2 + trade-tick data the bot does NOT capture or store today
(`market_raw` is OHLCV bars only, and exchange L2 history is not freely
backfillable). So S10 is a TWO-part sprint: this Tier-1 estimator core (pure,
CI-testable) + a Tier-2 live-capture path (proposed in
`docs/ml/orderflow-capture-design.md`, operator-gated) that must run forward to
ACCRUE the data before any model can A/B these features. Unlike S9 (range-vol
from existing OHLC) and S11 (funding/OI from REST history), S10 cannot be
back-tested offline today.

All functions here are **pure** and operate on already-captured snapshots /
trades (the caller does the past-only windowing — same contract as
`volatility_estimators.py` / `funding_oi_features.py`). Fed a past-only window
the result is leakage-safe by construction. ``None`` / ``0.0`` on degenerate
input via ``_finite_or_zero``.

References: Cont, Kukanov & Stoikov, *The price impact of order book events*
(2014) — OFI; Easley, López de Prado & O'Hara, *Flow toxicity and liquidity in
a high-frequency world* (2012) — VPIN + bulk-volume classification; Stoikov,
*The micro-price* (2018).
"""
from __future__ import annotations

import math
import statistics
from typing import Mapping, Sequence


def microprice(bid: float, bid_size: float, ask: float, ask_size: float) -> float | None:
    """Stoikov micro-price: ``(bid*ask_size + ask*bid_size) / (bid_size+ask_size)``.

    Weighted toward the side with the larger *opposite* size — a better
    short-horizon fair value than the mid. ``None`` on non-positive prices or a
    zero total size.
    """
    if bid <= 0 or ask <= 0:
        return None
    tot = bid_size + ask_size
    if tot <= 0:
        return None
    return (bid * ask_size + ask * bid_size) / tot


def relative_spread(bid: float, ask: float) -> float | None:
    """``(ask - bid) / mid`` — spread normalised by the mid price."""
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = 0.5 * (bid + ask)
    if mid <= 0:
        return None
    return (ask - bid) / mid


def _ofi_event(
    pb0: float, vb0: float, pa0: float, va0: float,
    pb1: float, vb1: float, pa1: float, va1: float,
) -> float:
    """Single Cont-Kukanov-Stoikov order-flow-event term ``e_n`` between two
    consecutive best-quote snapshots (0 = prev, 1 = curr).

    bid contribution: +Vb1 if the bid price rose, -Vb0 if it fell, ΔVb if flat.
    ask contribution (sign-flipped): +Va1 if the ask price fell, -Va0 if it
    rose, ΔVa if flat. ``e_n = bid_term - ask_term``.
    """
    if pb1 > pb0:
        bid_term = vb1
    elif pb1 < pb0:
        bid_term = -vb0
    else:
        bid_term = vb1 - vb0
    if pa1 < pa0:
        ask_term = va1
    elif pa1 > pa0:
        ask_term = -va0
    else:
        ask_term = va1 - va0
    return bid_term - ask_term


def order_flow_imbalance(snapshots: Sequence[Mapping[str, float]]) -> float | None:
    """Cont OFI summed over a window of best-quote snapshots.

    Each snapshot is ``{bid, bid_size, ask, ask_size}``. Returns the sum of the
    per-event ``e_n`` over consecutive pairs (a signed net order-flow pressure;
    positive = net buy pressure). ``None`` for fewer than 2 usable snapshots.
    """
    rows = [s for s in snapshots if s.get("bid", 0) > 0 and s.get("ask", 0) > 0]
    if len(rows) < 2:
        return None
    total = 0.0
    for prev, cur in zip(rows, rows[1:]):
        total += _ofi_event(
            float(prev["bid"]), float(prev.get("bid_size", 0.0)),
            float(prev["ask"]), float(prev.get("ask_size", 0.0)),
            float(cur["bid"]), float(cur.get("bid_size", 0.0)),
            float(cur["ask"]), float(cur.get("ask_size", 0.0)),
        )
    return total


def _std_normal_cdf(x: float) -> float:
    """Φ(x) via the error function (stdlib only)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bulk_volume_classification(
    price_changes: Sequence[float], volumes: Sequence[float], sigma: float
) -> tuple[list[float], list[float]]:
    """Bulk-volume classification (Easley-López de Prado-O'Hara).

    Splits each bucket's ``volume`` into (buy, sell) by ``Φ(ΔP / sigma)`` — the
    standardized price change over the bucket. ``sigma`` is the stdev of the
    bucket price changes (the caller estimates it past-only). Returns
    ``(buy_volumes, sell_volumes)``. A non-positive ``sigma`` → 50/50 split.
    """
    buys: list[float] = []
    sells: list[float] = []
    for dp, vol in zip(price_changes, volumes):
        frac_buy = 0.5 if sigma <= 0 else _std_normal_cdf(dp / sigma)
        buys.append(vol * frac_buy)
        sells.append(vol * (1.0 - frac_buy))
    return buys, sells


def vpin(buy_volumes: Sequence[float], sell_volumes: Sequence[float]) -> float | None:
    """VPIN = mean over buckets of ``|V_buy - V_sell| / (V_buy + V_sell)``.

    The volume-synchronised probability of informed trading (flow toxicity) over
    the supplied buckets (the caller windows them, e.g. the last 50 volume
    buckets). ``None`` when no bucket carries volume.
    """
    fracs: list[float] = []
    for b, s in zip(buy_volumes, sell_volumes):
        tot = b + s
        if tot > 0:
            fracs.append(abs(b - s) / tot)
    if not fracs:
        return None
    return statistics.fmean(fracs)


def _finite_or_zero(value: float | None) -> float:
    """Feature-emit shape: ``None`` / non-finite → ``0.0`` (neutral)."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)
