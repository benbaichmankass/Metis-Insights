"""Per-trade transaction-cost estimation — M18 P0a.

A **pure** fixed-model estimator for a trade's round-trip transaction cost, used
to populate ``trades.fee_taker_usd`` / ``cost_source='estimate'`` on the close
path when the broker doesn't expose per-fill fees. This closes the #1 data gap
for the M18 capital allocator (``docs/research/capital-allocation-ai-DESIGN.md``
§ 4): the per-cell live path never recorded transaction cost, so the EV scorer
can't learn cost as a feature and a learned ranker can't get an unbiased net-R
label.

The estimate is **consistent with the allocator EV scorer's fee term**
(``src/runtime/allocator_ev.py``): the round-trip cost ≈ ``fee_bps_roundtrip``
applied to the entry notional. A broker-truth writer (where the integration
exposes real per-fill fees + funding) is the follow-up that upgrades
``cost_source`` to ``'broker'``; until then this fixed estimate is explicitly
flagged so a consumer can distinguish measured from modelled cost.

Pure + fail-permissive: anything un-derivable → ``None``; never raises.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from src.runtime.allocator_ev import DEFAULT_FEE_BPS_ROUNDTRIP


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def estimate_roundtrip_fee_usd(
    *,
    entry_price: Any,
    qty: Any,
    contract_value_usd: Any = 1.0,
    fee_bps_roundtrip: float = DEFAULT_FEE_BPS_ROUNDTRIP,
) -> Optional[float]:
    """Fixed-model round-trip taker fee in USD for one trade. ``None`` if un-derivable.

    ``fee_usd = (fee_bps_roundtrip / 1e4) · |entry_price| · |qty| · |contract_value_usd|``
    — the round-trip cost as ``fee_bps_roundtrip`` on the entry notional (the same
    approximation the EV scorer's ``fee_R`` uses, so the logged cost and the
    decision-time cost agree). ``contract_value_usd`` is the USD-per-point
    multiplier (1.0 for crypto-perps; 5/10/… for futures). Negative ``fee_bps`` is
    clamped to 0 (a cost never adds value). Never raises.
    """
    e = _f(entry_price)
    q = _f(qty)
    cvu = _f(contract_value_usd)
    if e is None or q is None or e <= 0 or q <= 0:
        return None
    if cvu is None or cvu <= 0:
        cvu = 1.0
    bps = _f(fee_bps_roundtrip)
    if bps is None or bps < 0:
        bps = 0.0
    notional = abs(e) * abs(q) * abs(cvu)
    return (bps / 1.0e4) * notional
