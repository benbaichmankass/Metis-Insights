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

# Per-request page size. Bybit's fetch_my_trades is called ONCE per target with
# this limit and there is NO pagination loop, so a window holding more than this
# many fills is silently truncated to the newest PAGE_LIMIT. That is fine for the
# 7-day operational pull (a low-volume account fits easily) and is a hard ceiling
# on any deeper historical pull: a result of exactly PAGE_LIMIT means "capped",
# NOT "that is all the venue has". `fetch_fills_window` logs the distinction
# rather than leaving the caller to assume completeness
# (BL-20260808-FILLS-WINDOW-TOO-SHORT-TO-REPAIR-HISTORY).
PAGE_LIMIT = 200

# Bybit V5 /v5/execution/list caps the queryable RANGE, not the retention:
#
#   "startTime and endTime are not passed, return 7 days by default"
#   "Only startTime is passed, return range between startTime and startTime+7 days"
#   "If both are passed, the rule is endTime - startTime <= 7 days"
#
# and the endpoint is titled "Get Trade History (2 years)".
#
# So a single call with since = now-90d does NOT return 90 days. It returns the
# 7-day slice [now-90d, now-83d] — the window is MOVED, not widened. Measured
# 2026-08-08: a `--days 90` pull returned candidates=0 on all three accounts
# while the same accounts returned 63 / 3 / 13 at `--days 7`. A 90-day window
# cannot hold fewer fills than the 7-day window nested inside it, and that
# monotonicity violation is the proof the range was never honoured — read as
# "Bybit has no history" it would have been exactly backwards
# (BL-20260808-FILLS-WINDOW-TOO-SHORT-TO-REPAIR-HISTORY).
#
# Hence: a deeper pull must WALK the range in <= 7-day chunks.
MAX_RANGE_DAYS = 7


class FillsWindowUnavailable(RuntimeError):
    """Every target in the window failed — the account has ZERO coverage.

    The distinction this exception exists to make: an empty result list can
    mean "this account had no fills" or "we could not read this account at
    all", and those demand opposite responses. Before this existed the caller
    could not tell them apart, so ``bybit_1`` / ``bybit_portfolio`` returned an
    empty list on ``retCode 10003 "API key is invalid"`` every night from at
    least 2026-08-04, the run summary printed ``ran=3/3 total_inserted=0``, and
    systemd reported the unit **successful** — the two demo accounts were
    silently 100% uncovered while ``/api/bot/pnl/exchange`` served their absence
    as clean zeros (BL-20260807-BYBIT-DEMO-FILLS-NEVER-PULLED).

    PARTIAL failure stays best-effort and is NOT raised: one bad symbol out of
    several is genuine partial coverage and the next run retries it. Only a
    total wipeout — every attempted target raised — is exceptional.
    """


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


def _range_chunks(
    start: datetime, end: datetime, *, max_days: int = MAX_RANGE_DAYS,
) -> list[tuple[datetime, datetime]]:
    """Split ``[start, end]`` into consecutive windows of at most *max_days*.

    Returns oldest-first. A range already inside the cap yields exactly one
    chunk, so the ordinary 7-day operational pull still issues ONE request per
    target and behaves byte-for-byte as before.
    """
    if end <= start:
        return [(start, end)]
    step = timedelta(days=max_days)
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        out.append((cursor, nxt))
        cursor = nxt
    return out


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

    A *days* deeper than ``MAX_RANGE_DAYS`` is **WALKED** in consecutive
    ``<= MAX_RANGE_DAYS`` chunks (oldest-first), because Bybit caps the
    queryable RANGE at 7 days while retaining 2 years — a single call with
    ``since = now-90d`` returns the 7-day slice ``[now-90d, now-83d]``, not 90
    days. Rows are deduped on ``exec_id`` across chunks. A range already inside
    the cap issues exactly ONE request per target, so the 7-day operational
    pull is unchanged.
    """
    end_dt = now or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    chunks = _range_chunks(start_dt, end_dt)
    targets: list[Optional[str]] = list(symbols) if symbols else [None]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    attempts = 0
    failed = 0
    last_exc: Optional[Exception] = None

    for sym in targets:
        for chunk_start, chunk_end in chunks:
            attempts += 1
            since_ms = int(chunk_start.timestamp() * 1000)
            # endTime is passed EXPLICITLY rather than relying on Bybit's
            # startTime+7d default: if a venue/ccxt version ignores it the
            # chunk is still <= MAX_RANGE_DAYS wide, so the walk tiles
            # correctly either way.
            params = {"endTime": int(chunk_end.timestamp() * 1000)}
            try:
                trades = fetch_my_trades(sym, since_ms, PAGE_LIMIT, params)
            except Exception as exc:  # noqa: BLE001
                # Read-side failure: log loudly, skip this chunk, continue.
                # The puller is best-effort; partial coverage is better
                # than no coverage. The next puller run will retry.
                failed += 1
                last_exc = exc
                logger.exception(
                    "exchange_fills_puller: fetch_my_trades(%s, %s..%s) failed: %s",
                    sym, chunk_start.isoformat(), chunk_end.isoformat(), exc,
                )
                continue
            n = len(trades or ())
            if n >= PAGE_LIMIT:
                # DECLARE the cap. Without this a chunk that returned a full
                # page reads identically to one that held exactly that many
                # fills, and any count built on it would be wrong in the
                # direction that looks like good news.
                logger.warning(
                    "exchange_fills_puller: %s target=%s window=%s..%s returned a "
                    "FULL page (%d >= limit %d) — result is PAGE-CAPPED, older "
                    "fills in THIS chunk were NOT fetched (no intra-chunk "
                    "pagination). Do not read this as the chunk's full history.",
                    account_id, sym or "(all symbols)",
                    chunk_start.isoformat(), chunk_end.isoformat(),
                    n, PAGE_LIMIT,
                )
            for t in trades or ():
                row = _ccxt_trade_to_fill_row(t, account_id)
                exec_id = row.get("exec_id")
                if not exec_id:
                    # Bybit must return execId; missing = malformed payload.
                    logger.warning(
                        "exchange_fills_puller: skipping fill without exec_id: %s",
                        {k: t.get(k) for k in ("symbol", "timestamp", "side")},
                    )
                    continue
                # Chunk boundaries can overlap by a millisecond, and a symbol
                # may be queried under several targets. Dedupe here so the
                # candidates count the caller logs is a real fill count.
                if exec_id in seen:
                    continue
                seen.add(exec_id)
                out.append(row)

    # ZERO coverage is not an empty book — say so rather than returning [].
    # `attempts` is never 0 (targets falls back to [None] and chunks to one
    # window), so this fires only when every attempted read raised.
    if failed and failed == attempts:
        raise FillsWindowUnavailable(
            f"{account_id}: all {failed} read(s) failed; last error: {last_exc}"
        ) from last_exc
    return out
