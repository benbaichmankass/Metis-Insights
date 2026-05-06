"""Bybit instrument-precision helpers.

Strategies compute SL/TP from raw float arithmetic
(e.g. ``entry - mult * std_dev``) so the values carry 10-13 decimals
of binary-float noise. Bybit rejects those with
``retCode 170134 — Order price has too many decimals`` because the
exchange enforces price alignment to the symbol's
``priceFilter.tickSize``.

Resolution order for ``get_tick_size``:

  1. Static map of known ``(symbol, category) -> tickSize``.
  2. Process-lifetime cache populated by previous live lookups.
  3. Live ``client.get_instruments_info`` lookup (Bybit V5).
  4. Conservative 0.01 fallback so a transient instruments-info
     outage cannot block the order path for the common
     USDT-quoted pairs.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# (symbol, category) -> tickSize string. Extend as new pairs are
# added to ``config/accounts.yaml`` / strategy configs.
_STATIC_TICK_SIZE: Dict[Tuple[str, str], str] = {
    ("BTCUSDT", "spot"): "0.01",
    ("BTCUSDT", "linear"): "0.10",
    ("ETHUSDT", "spot"): "0.01",
    ("ETHUSDT", "linear"): "0.01",
    ("SOLUSDT", "spot"): "0.001",
    ("SOLUSDT", "linear"): "0.010",
}

_LIVE_CACHE: Dict[Tuple[str, str], str] = {}

_FALLBACK_TICK = Decimal("0.01")


def _live_tick_size(client: Any, symbol: str, category: str) -> Optional[str]:
    """Fetch ``priceFilter.tickSize`` from Bybit V5 instruments-info.

    Returns the tickSize string or ``None`` on any error / empty
    response. The caller caches the result.
    """
    try:
        resp = client.get_instruments_info(category=category, symbol=symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tick_size live lookup failed for %s %s: %s — using fallback",
            category, symbol, exc,
        )
        return None
    items = ((resp or {}).get("result") or {}).get("list") or []
    if not items:
        return None
    return ((items[0] or {}).get("priceFilter") or {}).get("tickSize")


def get_tick_size(client: Any, symbol: str, category: str) -> Decimal:
    """Resolve the ``tickSize`` for ``symbol`` in Bybit ``category``.

    Order: static map → cached live result → live lookup → 0.01
    fallback. The fallback degrades safely for the USDT-quoted spot
    pairs the bot trades today (all have a 0.01 tick).
    """
    key = (symbol.upper(), category.lower())
    static = _STATIC_TICK_SIZE.get(key)
    if static:
        return Decimal(static)
    cached = _LIVE_CACHE.get(key)
    if cached:
        return Decimal(cached)
    if client is not None:
        live = _live_tick_size(client, key[0], key[1])
        if live:
            _LIVE_CACHE[key] = live
            return Decimal(live)
    return _FALLBACK_TICK


def quantize_price(value: float, tick: Decimal) -> str:
    """Round ``value`` to the nearest multiple of ``tick``.

    Returned as a plain decimal string aligned to the tick's
    exponent (``81199.18`` for tick ``0.01``, ``81199.20`` for tick
    ``0.10``) so Bybit's parser does not see binary-float noise.
    """
    d = Decimal(str(value))
    quotient = (d / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str((quotient * tick).quantize(tick))
