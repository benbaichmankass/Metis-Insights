"""Bybit instrument-precision helpers.

Strategies compute SL/TP from raw float arithmetic
(e.g. ``entry - mult * std_dev``) so the values carry 10-13 decimals
of binary-float noise. Bybit rejects those with
``retCode 170134 — Order price has too many decimals`` because the
exchange enforces price alignment to the symbol's
``priceFilter.tickSize``.

Resolution order for ``get_tick_size``:

  1. Process cache populated by previous live lookups (2-hour TTL per
     Bybit's own recommendation for instrument-info caching).
  2. Live ``client.get_instruments_info`` lookup (Bybit V5).
  3. Static map of known ``(symbol, category) -> tickSize`` as a
     fallback when the live API is unavailable.
  4. Conservative 0.01 fallback so a transient instruments-info
     outage cannot block the order path for the common
     USDT-quoted pairs.

Live lookup takes priority over the static map because Bybit's tick
sizes can change and a stale hard-coded value silently causes 170134
rejections (BUG-057 reopen 2026-05-06).
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# (symbol, category) -> tickSize string. These are fallback values only;
# the live API takes priority. Extend as new pairs are added to
# ``config/accounts.yaml`` / strategy configs.
_STATIC_TICK_SIZE: Dict[Tuple[str, str], str] = {
    ("BTCUSDT", "spot"): "0.01",
    ("BTCUSDT", "linear"): "0.10",
    ("ETHUSDT", "spot"): "0.01",
    ("ETHUSDT", "linear"): "0.01",
    ("SOLUSDT", "spot"): "0.001",
    ("SOLUSDT", "linear"): "0.010",
}

# (symbol, category) -> (tickSize string, monotonic timestamp)
_LIVE_CACHE: Dict[Tuple[str, str], Tuple[str, float]] = {}
_CACHE_TTL_SECONDS: float = 7200.0  # 2 hours per Bybit's recommendation

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

    Order: cache (2-hour TTL) → live ``get_instruments_info`` lookup →
    static map → 0.01 fallback. Live is preferred over the static map
    so stale hard-coded values do not silently cause 170134 rejections.
    Bybit's own docs recommend caching instrument info for up to 2 hours.
    """
    key = (symbol.upper(), category.lower())
    now = time.monotonic()
    entry = _LIVE_CACHE.get(key)
    if entry is not None:
        tick_str, cached_at = entry
        if now - cached_at < _CACHE_TTL_SECONDS:
            return Decimal(tick_str)
        del _LIVE_CACHE[key]
    if client is not None:
        live = _live_tick_size(client, key[0], key[1])
        if live:
            _LIVE_CACHE[key] = (live, now)
            return Decimal(live)
    static = _STATIC_TICK_SIZE.get(key)
    if static:
        return Decimal(static)
    return _FALLBACK_TICK


def invalidate_tick_cache(symbol: str, category: str) -> None:
    """Evict a cached tick size to force a fresh live lookup on the next call.

    Call this immediately after a Bybit 170134 rejection so the next order
    queries the live ``get_instruments_info`` instead of serving stale data.
    """
    _LIVE_CACHE.pop((symbol.upper(), category.lower()), None)


def quantize_price(value: float, tick: Decimal) -> str:
    """Round ``value`` to the nearest multiple of ``tick``.

    Returned as a plain decimal string aligned to the tick's
    exponent (``81199.18`` for tick ``0.01``, ``81199.20`` for tick
    ``0.10``) so Bybit's parser does not see binary-float noise.
    """
    d = Decimal(str(value))
    quotient = (d / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str((quotient * tick).quantize(tick))


# ---------------------------------------------------------------------------
# Quantity lot-size alignment (BL-20260611-005, 2026-06-11)
# ---------------------------------------------------------------------------
# The price side has always been tick-aligned (above); the QUANTITY side was
# not, and Bybit enforces ``lotSizeFilter.qtyStep`` per symbol the same way
# it enforces tickSize. The account-level sizing precision (3dp, tuned for
# BTCUSDT's 0.001 step) produced e.g. 14.937 ETH on a 0.01-step contract →
# ``retCode 10001 Qty invalid`` on every eth_pullback_2h order. Same
# cache → live → static → (None) resolution as get_tick_size; ``None``
# means "rule unknown — submit unmodified" so an instruments-info outage
# can never block the order path with a wrong guess.

# (symbol, category) -> (qtyStep string, minOrderQty string). Fallback only;
# live lookup takes priority.
_STATIC_LOT_RULE: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("BTCUSDT", "linear"): ("0.001", "0.001"),
    ("ETHUSDT", "linear"): ("0.01", "0.01"),
    ("SOLUSDT", "linear"): ("0.1", "0.1"),
}

# (symbol, category) -> ((qtyStep, minOrderQty, maxOrderQty|None) strings,
# monotonic timestamp). The THIRD slot was added 2026-08-13
# (BL-20260810-ICTSCALP-AVAX-QTY-EXCEEDS-VENUE-MAX): ``maxOrderQty`` was
# already arriving in the SAME ``lotSizeFilter`` payload this module fetches
# and was being discarded, so nothing downstream could clamp an order to the
# venue's per-order ceiling. Measured consequence: ict_scalp_avax_5m sized
# 23,090-34,526 AVAX against a 22,000 cap and had 18 of 22 sized orders
# bounced by Bybit while reading healthy on every status surface.
#
# ``None`` in the third slot means "the venue published no maximum" and is
# NEVER conflated with 0.0 — a zero ceiling would refuse every order, so the
# two must stay distinguishable (the collapsed-state rule).
_LOT_CACHE: Dict[Tuple[str, str], Tuple[Tuple[str, str, Optional[str]], float]] = {}


def _live_lot_rule(
    client: Any, symbol: str, category: str,
) -> Optional[Tuple[str, str, Optional[str]]]:
    """Fetch ``lotSizeFilter`` (qtyStep, minOrderQty, maxOrderQty) from
    instruments-info.

    Spot symbols carry ``basePrecision`` instead of ``qtyStep``; both are
    "the base-asset quantity granularity", so basePrecision is used when
    qtyStep is absent. Returns ``None`` on any error / empty response.

    The third element is the venue's per-order CEILING, or ``None`` when the
    venue published none. It comes from the same response the step and min
    already came from — this is a wider read of a payload we were fetching
    anyway, not a new round-trip.
    """
    try:
        resp = client.get_instruments_info(category=category, symbol=symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "lot_rule live lookup failed for %s %s: %s — submitting unmodified",
            category, symbol, exc,
        )
        return None
    items = ((resp or {}).get("result") or {}).get("list") or []
    if not items:
        return None
    lot = (items[0] or {}).get("lotSizeFilter") or {}
    step = lot.get("qtyStep") or lot.get("basePrecision")
    min_qty = lot.get("minOrderQty") or step
    if not step:
        return None
    # An ABSENT / unparseable / non-positive maximum resolves to None ("the
    # venue published no ceiling"), never to 0.0. Collapsing those would turn a
    # missing field into a ceiling of zero and refuse every order for that
    # symbol — strictly worse than the bug being fixed.
    raw_max = lot.get("maxOrderQty")
    max_qty: Optional[str] = None
    if raw_max not in (None, ""):
        try:
            if Decimal(str(raw_max)) > 0:
                max_qty = str(raw_max)
        except (InvalidOperation, TypeError, ValueError):
            logger.debug(
                "lot_rule: unparseable maxOrderQty %r for %s %s — treating as absent",
                raw_max, category, symbol,
            )
    return (str(step), str(min_qty), max_qty)


def get_lot_rule(
    client: Any, symbol: str, category: str,
) -> Optional[Tuple[Decimal, Decimal]]:
    """Resolve ``(qtyStep, minOrderQty)`` for ``symbol``/``category``.

    Order: cache (2-hour TTL) → live ``get_instruments_info`` → static
    map → ``None`` (rule unknown; caller submits the qty unmodified —
    today's behaviour — rather than aligning to a guessed step).
    """
    bounds = get_lot_bounds(client, symbol, category)
    if bounds is None:
        return None
    step, min_qty, _max_qty = bounds
    return (step, min_qty)


def get_lot_bounds(
    client: Any, symbol: str, category: str,
) -> Optional[Tuple[Decimal, Decimal, Optional[Decimal]]]:
    """Resolve ``(qtyStep, minOrderQty, maxOrderQty|None)`` for ``symbol``.

    The superset of :func:`get_lot_rule`, which now delegates here so the two
    can never disagree about the step/min — one resolution, one cache, two
    views of it.

    The third element is the venue's per-order CEILING, or ``None`` for "the
    venue published no maximum" (including every entry resolved from the static
    fallback map, which carries step/min only). ``None`` and ``0`` are
    deliberately different answers: a caller must clamp on a real ceiling and
    do nothing on an absent one.

    Same cache (2-hour TTL) → live → static → ``None`` resolution as before.
    """
    key = (symbol.upper(), category.lower())
    now = time.monotonic()
    entry = _LOT_CACHE.get(key)
    if entry is not None:
        rule, cached_at = entry
        if now - cached_at < _CACHE_TTL_SECONDS:
            return (Decimal(rule[0]), Decimal(rule[1]),
                    Decimal(rule[2]) if rule[2] is not None else None)
        del _LOT_CACHE[key]
    if client is not None:
        live = _live_lot_rule(client, key[0], key[1])
        if live:
            _LOT_CACHE[key] = (live, now)
            return (Decimal(live[0]), Decimal(live[1]),
                    Decimal(live[2]) if live[2] is not None else None)
    static = _STATIC_LOT_RULE.get(key)
    if static:
        # The static map is a step/min fallback only — it asserts no ceiling.
        return (Decimal(static[0]), Decimal(static[1]), None)
    return None


def quantize_qty(value: float, step: Decimal) -> Decimal:
    """Floor ``value`` DOWN to a multiple of ``step``.

    Always rounds toward zero (S-026 G3: realised risk must never exceed
    the sized cap) — the price side rounds half-up, quantity must not.
    """
    d = Decimal(str(value))
    quotient = (d / step).to_integral_value(rounding=ROUND_DOWN)
    return (quotient * step).quantize(step)


def live_instrument_diagnostic(
    client: Any, symbol: str, category: str,
) -> Optional[Dict[str, Any]]:
    """Fetch the full ``priceFilter`` + ``lotSizeFilter`` for diagnostics.

    BUG-057 reopen (2026-05-06): post-#420, Bybit still rejects spot
    BTCUSDT SL/TP values that are quantized to the static-map's 0.01
    tick. Either the static map is wrong or the SL/TP precision rule
    on spot Market orders differs from ``priceFilter.tickSize``. This
    helper captures the raw filters from a fresh ``get_instruments_info``
    call (no cache) so the next live failure logs ground-truth data
    the operator can use to pick a fix.

    Returns ``None`` if the client raises or returns an empty list.
    Never raises — diagnostics on the failure path must not amplify
    the failure.
    """
    try:
        resp = client.get_instruments_info(category=category, symbol=symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "live_instrument_diagnostic: get_instruments_info raised "
            "for %s %s: %s", category, symbol, exc,
        )
        return None
    items = ((resp or {}).get("result") or {}).get("list") or []
    if not items:
        return None
    item = items[0] or {}
    return {
        "symbol": item.get("symbol"),
        "category": category,
        "status": item.get("status"),
        "priceFilter": item.get("priceFilter") or {},
        "lotSizeFilter": item.get("lotSizeFilter") or {},
    }
