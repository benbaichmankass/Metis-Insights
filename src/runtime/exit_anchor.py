"""Anchor a CONFIRMED CLOSE to the bar covering its recorded ``closed_at``.

WHY (2026-07-30 provenance work, Tier-2 remedy)
-----------------------------------------------
``order_monitor._sweep_local_pnl_for_unpriced`` priced a trade that had *already
closed* using ``last_mark_price()`` — the market at SWEEP time, up to the
convergence grace later — and booked ``pnl`` from it. That is
:data:`~src.runtime.provenance.FABRICATED`: a true value exists (the fill) and
the mark is not it. Matched-pair proof: trade 4180 (real) −$4.00 vs its mirror
4181 −$2,589.78, same strategy, symbol, bracket and minute.

This module supplies the defensible alternative — the close of the 1-minute bar
covering ``closed_at``. Validated against known broker fills: **median 1.33 bps,
p90 16.05, 46/48 within 50 bps**. That earns :data:`~src.runtime.provenance.ESTIMATED`,
never ``MEASURED``: a bar close says where the market was, not where THIS order
filled, and on a gap or a thin bar the error is larger than those medians.

RUNTIME SAFETY (read before changing anything here)
---------------------------------------------------
The caller runs on the LIVE trader's monitor tick, so an unbounded per-row
network fetch here is the same shape as the 2026-06-09 cold-start wedge that
pegged the 2-core box and froze the heartbeat. Four bounds, all deliberate:

1. **Per-call timeout** (:data:`_TIMEOUT_S`) on the HTTP read.
2. **Per-tick fetch budget** (:class:`AnchorBudget`) — the caller allocates a
   small number of *network* fetches per tick; rows beyond it are deferred to a
   later tick, NOT fabricated and NOT declared.
3. **Positive AND negative caching.** A symbol the venue does not serve (every
   IBKR future — Bybit's public kline endpoint knows nothing of ``MES``) costs
   ONE request per process, not one per row per tick.
4. **Fail-safe**: every failure path returns ``None``. The caller distinguishes
   "not attempted" (retry) from "attempted, no anchor" (declare
   :data:`~src.runtime.provenance.UNMEASURED_MARKER`) — see
   :meth:`AnchorBudget.take`.

Uses Bybit's **public, keyless** v5 kline endpoint (the same one the estimator
was validated against — the repo's own ``fetch_candles`` takes no ``since``, so
it cannot address a historical bar). A non-Bybit symbol simply returns no rows,
which is the honest answer here: **IBKR historical-candle coverage is 0%**, so on
``ib_paper`` this converts fabrication into a *declared* gap rather than into an
estimate. Closing that gap needs `reqHistoricalData` chunking and is its own
piece of work.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "AnchorBudget",
    "bar_close_at",
    "closed_at_to_ms",
    "ANCHOR_SOURCE",
]

#: The provenance value stamped on a successfully anchored exit price. Declared
#: in ``provenance.ESTIMATED_SOURCES`` — never MEASURED.
ANCHOR_SOURCE = "candle_at_close"

_BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"
_TIMEOUT_S = 5.0
_BAR_MS = 60_000

#: Positive cache: (symbol, minute_bucket) -> close price.
_CACHE: Dict[Tuple[str, int], Optional[float]] = {}
#: Negative cache: symbols the venue does not serve at all (every IBKR root).
#: Keyed by symbol with a long TTL so an unsupported symbol costs ONE request
#: per process rather than one per row per tick.
_UNSUPPORTED: Dict[str, float] = {}
_UNSUPPORTED_TTL_S = 3600.0
_MAX_CACHE = 4096


class AnchorBudget:
    """Per-tick allowance of NETWORK fetches, with a not-attempted signal.

    The distinction this exists to preserve: a row we never tried to anchor must
    be RETRIED, while a row we tried and could not anchor should be *declared*
    unmeasured. Collapsing the two would either fabricate (retry forever, then
    something else fills it) or lie (declare a gap we never actually looked for).
    """

    def __init__(self, limit: int = 3) -> None:
        self.limit = max(0, int(limit))
        self.used = 0

    def take(self) -> bool:
        """Consume one fetch. ``False`` when exhausted (caller must DEFER)."""
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def closed_at_to_ms(value: Any) -> Optional[int]:
    """Normalise a journal ``closed_at`` to epoch ms. ``None`` if unparseable.

    Handles both shapes the journal actually contains: an ISO-8601 string and a
    **raw epoch-ms string** (the reconciler-filled close path writes the latter —
    the same trap that silently dropped rows from ``/performance``'s window
    before ``_closed_at.py`` normalised it).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return int(v) if v > 1e11 else int(v * 1000)
    s = str(value).strip()
    if not s:
        return None
    # Raw epoch-ms string.
    head = s.split(".")[0]
    if head.isdigit() and len(head) >= 12:
        try:
            return int(float(s))
        except ValueError:
            return None
    s = s.replace("T", " ").split("+")[0].split("Z")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int(dt.timestamp() * 1000)
    return None


def _default_fetch(symbol: str, start_ms: int, end_ms: int) -> Optional[list]:
    """One bounded public-kline request. ``None`` on ANY failure."""
    qs = urllib.parse.urlencode({
        "category": "linear", "symbol": symbol, "interval": "1",
        "start": start_ms, "end": end_ms, "limit": 10,
    })
    req = urllib.request.Request(
        f"{_BYBIT_KLINE}?{qs}", headers={"User-Agent": "metis-exit-anchor"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 — a market read never breaks the tick
        logger.warning("exit_anchor: kline fetch failed for %s: %s", symbol, exc)
        return None
    return (payload.get("result") or {}).get("list") or []


def bar_close_at(
    symbol: Any,
    closed_at: Any,
    *,
    budget: Optional[AnchorBudget] = None,
    fetch: Optional[Callable[[str, int, int], Optional[list]]] = None,
) -> Tuple[Optional[float], str]:
    """Close of the 1m bar covering *closed_at*. Returns ``(price, status)``.

    ``status`` is the caller's contract and is what keeps this honest:

    * ``"anchored"``   — a bar was found; *price* is its close. Stamp
      :data:`ANCHOR_SOURCE` (ESTIMATED).
    * ``"deferred"``   — the per-tick budget was exhausted, or the timestamp is
      unparseable in a way a later tick might resolve. **Retry**; do NOT declare.
    * ``"no_anchor"``  — the venue was asked and has no bar for that time (an
      IBKR root, or a close outside the served history). **Declare unmeasured**
      rather than substituting a mark.

    Never raises. *fetch* is injected in tests so no network is touched.
    """
    sym = str(symbol or "").strip().upper()
    ts_ms = closed_at_to_ms(closed_at)
    if not sym or ts_ms is None:
        # No symbol or no close time = nothing to anchor TO. This is a property
        # of the row, not of the venue, so a later tick won't help — but it is
        # also not evidence the venue lacks the bar. Treat as no_anchor so the
        # row converges to a declaration instead of retrying forever.
        return None, "no_anchor"

    bucket = ts_ms // _BAR_MS
    key = (sym, bucket)
    if key in _CACHE:
        cached = _CACHE[key]
        return (cached, "anchored" if cached is not None else "no_anchor")

    seen_at = _UNSUPPORTED.get(sym)
    if seen_at is not None and (time.monotonic() - seen_at) < _UNSUPPORTED_TTL_S:
        # Known-unsupported symbol (every IBKR root). Costs no request.
        return None, "no_anchor"

    if budget is not None and not budget.take():
        return None, "deferred"

    rows = (fetch or _default_fetch)(sym, bucket * _BAR_MS, bucket * _BAR_MS + _BAR_MS)
    if rows is None:
        # A transient read failure — NOT evidence the bar doesn't exist.
        return None, "deferred"
    if not rows:
        # The venue answered and has nothing. Remember the symbol so an
        # unsupported root isn't re-requested for every row on every tick.
        _UNSUPPORTED[sym] = time.monotonic()
        _remember(key, None)
        return None, "no_anchor"

    close: Optional[float] = None
    try:
        # Bybit rows: [start, open, high, low, close, volume, turnover].
        newest = sorted(rows, key=lambda r: int(r[0]))[-1]
        close = float(newest[4])
    except (IndexError, TypeError, ValueError) as exc:
        logger.warning("exit_anchor: unparseable kline row for %s: %s", sym, exc)
        return None, "deferred"
    if not close or close <= 0:
        _remember(key, None)
        return None, "no_anchor"
    _remember(key, close)
    return close, "anchored"


def _remember(key: Tuple[str, int], value: Optional[float]) -> None:
    if len(_CACHE) >= _MAX_CACHE:
        _CACHE.clear()
    _CACHE[key] = value


def reset_caches() -> None:
    """Test hook — clear both caches."""
    _CACHE.clear()
    _UNSUPPORTED.clear()
