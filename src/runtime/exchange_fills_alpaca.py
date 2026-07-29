"""Alpaca fills adapter for the broker-truth cost store (rec #7).

Maps Alpaca ``/v2/account/activities`` **FILL** records to the ``exchange_fills``
row schema (`src/runtime/exchange_fills_store.py`) so the exchange-truth P&L
surface + the broker-truth cost sweep cover the live Alpaca accounts, not just
Bybit. The sibling of ``exchange_fills_puller`` (Bybit / ccxt).

Read-only, side-effect-free, network-injectable (the page fetcher is a
parameter), so the mapping + pagination are unit-testable without an Alpaca key.

**Fees:** Alpaca equity trading is commission-free; regulatory fees (SEC/TAF)
arrive as SEPARATE ``FEE``/``CFEE`` activity types, not on the FILL record — so a
fill row's ``fee`` is an honest ``0.0`` here. Capturing the separate fee
activities is a documented follow-up (they're small and per-account, not per-fill).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

# fetch_page(after: str, page_token: str | None) -> Sequence[activity dict]
FetchPage = Callable[..., Sequence[Dict[str, Any]]]


def _norm_side(raw: Any) -> Optional[str]:
    """Normalise an Alpaca fill side to ``buy``/``sell`` (``sell_short`` → sell)."""
    s = str(raw or "").strip().lower()
    if s.startswith("buy"):
        return "buy"
    if s.startswith("sell"):
        return "sell"
    return None


def alpaca_fill_activity_to_row(
    activity: Dict[str, Any],
    account_id: str,
) -> Optional[Dict[str, Any]]:
    """Map one Alpaca FILL activity → an ``exchange_fills`` row, or ``None``.

    Returns ``None`` (skip) when a required field (id / symbol / side / price /
    qty) is missing or unparseable — never a fabricated zero row.
    """
    if str(activity.get("activity_type", "")).upper() != "FILL":
        return None
    exec_id = activity.get("id")
    symbol = activity.get("symbol")
    side = _norm_side(activity.get("side"))
    if not exec_id or not symbol or side is None:
        return None
    try:
        price = float(activity["price"])
        qty = float(activity["qty"])
    except (KeyError, TypeError, ValueError):
        return None
    if qty <= 0:
        return None
    exec_time = activity.get("transaction_time")
    if not exec_time:
        return None
    return {
        "exec_id": str(exec_id),
        "account_id": account_id,
        "symbol": str(symbol),
        "side": side,
        "price": price,
        "qty": qty,
        # Equity fills are commission-free; regulatory fees are separate FEE
        # activities (a documented follow-up), so the fill-row fee is honest 0.
        "fee": 0.0,
        "fee_currency": "USD",
        "exec_time": str(exec_time),
        "order_id": activity.get("order_id"),
        "is_maker": 0,
    }


def _after_bound(days: int, now: Optional[datetime]) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    bound = (now - timedelta(days=max(1, days))).astimezone(timezone.utc)
    return bound.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_alpaca_fills(
    fetch_page: FetchPage,
    *,
    account_id: str,
    days: int,
    now: Optional[datetime] = None,
    page_size: int = 100,
    max_pages: int = 50,
) -> List[Dict[str, Any]]:
    """Fetch + map an account's FILL activities over the last *days*.

    *fetch_page* is injected — ``fetch_page(after=<rfc3339>, page_token=<id|None>)``
    returns the raw activity list for one page (empty when exhausted). Follows
    Alpaca's cursor pagination (``page_token`` = the last activity's id) until a
    short page, or ``max_pages`` (a runaway backstop). Deduping is the store's job
    (``exec_id`` PRIMARY KEY), so overlapping windows are safe.
    """
    after = _after_bound(days, now)
    rows: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    for _ in range(max_pages):
        activities = list(fetch_page(after=after, page_token=page_token) or [])
        if not activities:
            break
        for act in activities:
            row = alpaca_fill_activity_to_row(act, account_id)
            if row is not None:
                rows.append(row)
        if len(activities) < page_size:
            break
        page_token = str(activities[-1].get("id") or "")
        if not page_token:
            break
    return rows
