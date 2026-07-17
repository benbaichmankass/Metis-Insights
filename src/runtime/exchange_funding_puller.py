"""Bybit perp-funding puller (Slice B / B1, MB-20260629-ALLOC-COSTCAP).

Pulls funding payments from Bybit V5 (via ccxt's ``fetch_funding_history``
wrapper) and writes them to the ``exchange_funding`` table of the local
``runtime_state/exchange_fills.sqlite`` store. Idempotent — re-running on
overlapping windows just skips duplicate ``funding_id`` rows.

Perp funding is NOT in the execution list (`/v5/execution/list`), so the fills
puller can't see it; this is the sibling that captures it so the broker-truth
cost sweep can attribute ``funding_paid_usd``. Read-only on the exchange side;
never places orders.

The ``fetch_funding_history`` callable is injected (not the connector) so unit
tests mock the network layer cleanly — same shape as the fills puller.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


def _ccxt_funding_to_row(entry: Mapping[str, Any], account_id: str) -> dict[str, Any]:
    """Map a ccxt funding-history entry to the ``exchange_funding`` schema.

    ccxt fields (Bybit V5): ``id`` (txn id), ``symbol`` (canonical),
    ``amount`` (signed funding payment), ``timestamp`` (epoch ms), ``info`` (raw).
    """
    ts_ms = entry.get("timestamp")
    funding_time = (
        datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat()
        if ts_ms is not None
        else entry.get("datetime") or ""
    )
    fid = entry.get("id")
    if not fid:
        # Fall back to a deterministic composite key so idempotency still holds.
        fid = f"{account_id}:{entry.get('symbol')}:{funding_time}"
    return {
        "funding_id": fid,
        "account_id": account_id,
        "symbol": entry.get("symbol"),
        "funding_usd": entry.get("amount"),
        "funding_time": funding_time,
        "raw": entry.get("info"),
    }


def fetch_funding_window(
    fetch_funding_history,
    account_id: str,
    *,
    days: int,
    now: Optional[datetime] = None,
    symbols: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Pull funding payments for *account_id* over the last *days*.

    *fetch_funding_history* matches ccxt's
    ``exchange.fetch_funding_history(symbol, since, limit, params)``. Returns rows
    ready for ``exchange_fills_store.upsert_funding``.
    """
    cutoff_dt = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    since_ms = int(cutoff_dt.timestamp() * 1000)
    out: list[dict[str, Any]] = []
    targets: list[Optional[str]] = list(symbols) if symbols else [None]
    for sym in targets:
        try:
            entries = fetch_funding_history(sym, since_ms, 200, {})
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "exchange_funding_puller: fetch_funding_history(%s) failed: %s",
                sym, exc,
            )
            continue
        for e in entries or ():
            out.append(_ccxt_funding_to_row(e, account_id))
    return out
