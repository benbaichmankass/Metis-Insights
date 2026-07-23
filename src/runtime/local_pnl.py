"""Local (broker-independent) realised / unrealised PnL computation.

Background
----------
Historically the bot computed close PnL with a local formula
(``order_monitor._compute_close_pnl``). That helper was deleted on 2026-05-18
under the directive *"Bybit is the only source of trade data; the system
doesn't need its own calculator"* — replaced by
:func:`order_monitor._sweep_pending_pnl_from_bybit`, which recovers the
authoritative ``closedPnl`` from Bybit's ``/v5/position/closed-pnl`` endpoint.

That recovery path is **Bybit-only**:
:func:`src.units.accounts.clients.account_closed_pnl_for_trade` returns
``None`` for every non-Bybit account. The bot now also trades IBKR futures
(MES / MGC / MHG on the ``ib_paper`` paper account) and Alpaca / OANDA paper,
none of which expose a closed-pnl lookup the bot consumes. So those trades'
``pnl`` column stays NULL forever and the dashboard renders ``$0.00`` against
a closed / orphaned position that genuinely had a result.

This module restores a *fallback* local calculator for exactly those cases.
Broker truth still wins wherever it's available (Bybit goes through the
existing closed-pnl sweep first); this only fills the gap the broker can't.
PnL is computed from the data we always have on the trade row + its order
package — entry, exit (or last market mark), qty, direction, and the
per-contract USD multiplier (``contract_value_usd`` from
``config/instruments.yaml``; e.g. MGC=10, MHG=2500, MES=5, crypto perps=1).
For paper accounts fees are not modelled, so the fee-blindness that motivated
the 2026-05-18 deletion is immaterial here.

Everything in this module is pure / best-effort and never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LONG = {"buy", "long"}
_SHORT = {"sell", "short"}


def canon_direction(direction: Any) -> Optional[str]:
    """Normalise a side to ``"long"`` / ``"short"`` (or ``None``)."""
    s = str(direction or "").strip().lower()
    if s in _LONG:
        return "long"
    if s in _SHORT:
        return "short"
    return None


def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_realized_pnl(
    *,
    entry_price: Any,
    exit_price: Any,
    qty: Any,
    direction: Any,
    contract_value_usd: float = 1.0,
) -> Optional[float]:
    """USD PnL for a fully-closed position.

    ``(exit - entry) * qty * contract_value_usd`` for a long, negated for a
    short. ``contract_value_usd`` is the USD value of a 1-point price move for
    one contract (1.0 for crypto perps; the futures multiplier otherwise).

    Returns ``None`` when any input is missing / non-numeric / non-positive
    qty so the caller can leave ``pnl`` NULL rather than write a bogus 0.
    """
    entry = _f(entry_price)
    exit_ = _f(exit_price)
    q = _f(qty)
    cvu = _f(contract_value_usd) or 1.0
    side = canon_direction(direction)
    if entry is None or exit_ is None or q is None or side is None:
        return None
    if q <= 0:
        return None
    sign = 1.0 if side == "long" else -1.0
    return round((exit_ - entry) * q * cvu * sign, 6)


# ``compute_unrealized_pnl`` is identical maths against the current mark; named
# separately so call sites read clearly (open position vs closed trade).
def compute_unrealized_pnl(
    *,
    entry_price: Any,
    mark_price: Any,
    qty: Any,
    direction: Any,
    contract_value_usd: float = 1.0,
) -> Optional[float]:
    """USD unrealised PnL of an open position marked at ``mark_price``."""
    return compute_realized_pnl(
        entry_price=entry_price,
        exit_price=mark_price,
        qty=qty,
        direction=direction,
        contract_value_usd=contract_value_usd,
    )


def compute_pnl_percent(
    *,
    pnl: Any,
    entry_price: Any,
    qty: Any,
    contract_value_usd: float = 1.0,
) -> Optional[float]:
    """PnL as a percentage of position notional (``entry * qty * cvu``).

    Multiplier-correct: for a clean win/loss the multiplier cancels, so this
    matches the legacy ``pnl / (entry*qty) * 100`` for crypto (cvu=1) and is
    finally correct for futures (cvu>1).
    """
    p = _f(pnl)
    entry = _f(entry_price)
    q = _f(qty)
    cvu = _f(contract_value_usd) or 1.0
    if p is None or entry is None or q is None:
        return None
    notional = entry * q * cvu
    if notional <= 0:
        return None
    return round(p / notional * 100.0, 4)


def contract_value_usd_for(symbol: Any) -> float:
    """USD-per-point contract value for *symbol* (1.0 default).

    Thin re-export of the canonical resolver in
    :mod:`src.core.profile_loader` (single source: ``config/instruments.yaml``)
    so PnL callers don't import the sizing module directly. Best-effort: any
    failure falls back to 1.0 (the crypto-perp value), never raises.
    """
    sym = str(symbol or "").strip()
    if not sym:
        return 1.0
    try:
        from src.core.profile_loader import contract_value_usd_for as _cvu
        return float(_cvu(sym))
    except Exception:  # noqa: BLE001 — best-effort; default keeps crypto correct
        return 1.0


# ---------------------------------------------------------------------------
# Last market mark — broker-independent exit / mark price for the local calc.
# ---------------------------------------------------------------------------
# Fetched from the SAME canonical feed the signal builders + /api/bot/candles
# use (Bybit for crypto, IBKR for MES/MGC/MHG via src.runtime.market_data).
# A short per-symbol cache keeps the monitor sweep + the positions endpoint
# from issuing a fresh (possibly blocking IBKR) fetch per row each tick.

_MARK_CACHE: Dict[str, tuple[float, Optional[float]]] = {}
_MARK_TTL_S = 60.0
_MARK_INTERVAL = "5m"


def last_mark_price(
    symbol: Any,
    *,
    settings: Optional[Dict[str, Any]] = None,
    ttl_s: float = _MARK_TTL_S,
) -> Optional[float]:
    """Most recent close for *symbol* from the bot's own exchange feed.

    Returns ``None`` (cached) on any failure — a logged-out IB Gateway or an
    unknown symbol shouldn't be re-fetched for every row. Best-effort.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    now = time.monotonic()
    cached = _MARK_CACHE.get(sym)
    if cached is not None and (now - cached[0]) < ttl_s:
        return cached[1]

    price: Optional[float] = None
    try:
        from src.runtime.market_data import connector_for_symbol, fetch_candles

        cfg = settings if isinstance(settings, dict) else {}
        client = connector_for_symbol(sym, cfg)
        df = fetch_candles(sym, _MARK_INTERVAL, settings=cfg, limit=3,
                           exchange_client=client)
        if df is not None and len(df) > 0:
            price = _f(df.iloc[-1].get("close"))
    except Exception as exc:  # noqa: BLE001 — best-effort market read
        logger.warning("last_mark_price: fetch failed for %s: %s", sym, exc)
        price = None

    _MARK_CACHE[sym] = (now, price)
    return price
