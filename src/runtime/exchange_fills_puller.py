"""Bybit fills puller (S-067 follow-up #6).

Pulls fills from Bybit V5's ``/v5/execution/list`` (via ccxt's
``fetch_my_trades`` wrapper) and writes them to the local
``runtime_state/exchange_fills.sqlite`` store. Idempotent — re-running
on overlapping windows just skips duplicate ``exec_id`` rows.

Read-only on the exchange side. Never places orders. The live-order
path is unaffected.

Wired into a daily cron / systemd timer by the operator after this
PR lands; the puller itself is a plain CLI entry-point and has no
side effects beyond the local sqlite write.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


def _ccxt_trade_to_fill_row(trade: Mapping[str, Any], account_id: str) -> dict[str, Any]:
    """Map a ccxt-shaped trade dict to the ``exchange_fills`` schema.

    The relevant ccxt fields (Bybit V5):
      ``id``       - Bybit ``execId``
      ``order``    - Bybit ``orderId``
      ``symbol``   - canonicalised (e.g. ``BTC/USDT:USDT``)
      ``side``     - ``buy``/``sell``
      ``price``    - execution price
      ``amount``   - execution qty
      ``fee.cost`` - fee amount
      ``fee.currency`` - fee currency
      ``timestamp`` - epoch ms (UTC)
      ``takerOrMaker`` - ``maker``/``taker``
      ``info``     - raw exchange payload (preserved for forensics)
    """
    fee = trade.get("fee") or {}
    ts_ms = trade.get("timestamp")
    exec_time = (
        datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat()
        if ts_ms is not None
        else trade.get("datetime") or ""
    )
    return {
        "exec_id": trade.get("id"),
        "account_id": account_id,
        "symbol": trade.get("symbol"),
        "side": trade.get("side"),
        "price": trade.get("price"),
        "qty": trade.get("amount"),
        "fee": fee.get("cost") or 0.0,
        "fee_currency": fee.get("currency"),
        "exec_time": exec_time,
        "order_id": trade.get("order"),
        "is_maker": (trade.get("takerOrMaker") == "maker"),
        "raw": trade.get("info"),
    }


def fetch_fills_window(
    fetch_my_trades,
    account_id: str,
    *,
    days: int,
    now: Optional[datetime] = None,
    symbols: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Pull fills for *account_id* over the last *days*.

    *fetch_my_trades* is a callable matching ccxt's
    ``exchange.fetch_my_trades(symbol, since, limit, params)``
    signature. The function itself is injected (rather than the
    connector) so unit tests can mock the network layer cleanly.

    Returns a list of fill rows ready for
    ``exchange_fills_store.upsert_fills``.

    *symbols* is optional; when omitted the puller queries Bybit
    without a symbol filter (V5 supports this on the unified account).
    Pass a tight allowlist (e.g. ``["BTC/USDT:USDT", "ETH/USDT:USDT"]``)
    to reduce the response size in production.
    """
    cutoff_dt = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    since_ms = int(cutoff_dt.timestamp() * 1000)
    out: list[dict[str, Any]] = []
    targets: list[Optional[str]] = list(symbols) if symbols else [None]
    for sym in targets:
        try:
            trades = fetch_my_trades(sym, since_ms, 200, {})
        except Exception as exc:  # noqa: BLE001
            # Read-side failure: log loudly, skip this symbol, continue.
            # The puller is best-effort; partial coverage is better
            # than no coverage. The next puller run will retry.
            logger.exception(
                "exchange_fills_puller: fetch_my_trades(%s) failed: %s",
                sym, exc,
            )
            continue
        for t in trades or ():
            row = _ccxt_trade_to_fill_row(t, account_id)
            if not row.get("exec_id"):
                # Bybit must return execId; missing = malformed payload.
                logger.warning(
                    "exchange_fills_puller: skipping fill without exec_id: %s",
                    {k: t.get(k) for k in ("symbol", "timestamp", "side")},
                )
                continue
            out.append(row)
    return out
