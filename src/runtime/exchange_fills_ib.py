"""IBKR executions adapter for the broker-truth fills store.

WHY THIS EXISTS
---------------
**State the population** (the rule this workstream produced, ``CLAUDE.md`` §
"Number provenance"). Measured against the live journal on 2026-07-30, IB's
exposure is real but is NOT what the briefing framed:

* **All-status population** (845 rows): ``ib_paper`` carries **+$284,084.92** of
  fabricated PnL — the figure behind the widely-quoted "+$247,683.78 net, the
  bulk of it IB". It is **4 ``orphaned`` rows**, which appear in neither
  Positions nor Trades.
* **Closed, non-backtest, ``pnl NOT NULL``** — the decision population any
  consumer actually aggregates (829 rows, 206 fabricated, **−$36,018.60**):
  ``ib_paper`` is **3 of 27 closed rows**. The concentration there is
  ``bybit_1`` (152/323) and ``bybit_portfolio`` (11/12).

So this module is not the biggest *closed-population* lever. It matters for a
different reason: the companion Tier-2 change stopped the sweep substituting a
mark, and IBKR historical-candle coverage is **0%**, so without a broker-truth
read every future IB close becomes a *declared unmeasured* gap. This module is
what converts that gap into a measurement instead.

⚠️ **THE PARAGRAPH BELOW DESCRIBES THE STATE THIS MODULE WAS WRITTEN FOR, WHICH
NO LONGER HOLDS. Do not read it as current** (corrected 2026-08-23). Measured
today, ``clients.BROKER_PNL_READER_EXCHANGES`` is
``{"bybit", "interactive_brokers", "alpaca"}`` — IBKR **is** a declared
broker-truth reader, wired since 2026-07-31 (#8111, commit ``e5e8656a``), and
``clients.account_closed_pnl_for_trade`` dispatches its closes to
``src.runtime.fills_pnl.exit_from_fills(require_realized=True)``, which reads
each fill's ``CommissionReport.realizedPNL`` and returns ``closed_pnl``. So an
IB close no longer falls through to the mark-priced sweep.

The stale text cost something real: it is what
``BL-20260818-IB-BROKER-PNL-READER-HAS-NO-CALLER`` reasoned from, filing a
CRITICAL on the inference that IBKR's realised PnL was "pulled hourly and never
read". The function ``closed_pnl_from_fills`` in THIS module does indeed have
zero production callers — but a DIFFERENT, wired function reads the same field,
so the data path was never blind. Measured on the live journal 2026-08-23 (a
small population, stated: 15 closed ``ib_paper`` rows in total, 6 of them closed
on or after the wiring date): **4 of those 6 grade ``measured``**, against a
pre-wiring mix of fabricated/unverified. Field beats comment — and a call count
on one accessor is not a claim about the data path.

The historical framing follows, kept as the record of why this module exists:
``interactive_brokers`` WAS absent from ``clients.BROKER_PNL_READER_EXCHANGES``,
there is no ``IBClient.fills()``, and so every IB close fell through to
``order_monitor._sweep_local_pnl_for_unpriced``, which prices a CONFIRMED CLOSE
off ``last_mark_price()`` — the market hours later — and then multiplies that
error by the futures contract multiplier.

IBKR **does** serve the truth. ``reqExecutions`` returns per-execution fills,
and each carries a ``CommissionReport`` with the broker's own ``commission``
**and ``realizedPNL``**. That is a measured number from the venue, so a row
sourced here is :data:`~src.runtime.provenance.MEASURED` — strictly better than
any reconstruction, however well validated (the candle-anchored estimator this
supersedes for IB measured median 1.33 bps against known fills, but an estimate
that is 1.33 bps off is still not a fill).

This module is the mapping half only: IB ``Fill`` → an ``exchange_fills`` row
(:mod:`src.runtime.exchange_fills_store`). It is the sibling of
:mod:`src.runtime.exchange_fills_alpaca` (Alpaca) and
:mod:`src.runtime.exchange_fills_puller` (Bybit / ccxt).

DESIGN CONSTRAINTS
------------------
* **No ``ib_insync`` import.** Everything is duck-typed via ``getattr`` and the
  fetcher is injected, so the mapping is unit-testable on a host with no IB
  dependency and no gateway (the same contract ``exchange_fills_alpaca`` keeps).
* **This module defines NO table.** It maps into, and reads back out of, the
  EXISTING ``exchange_fills`` table owned by
  :mod:`src.runtime.exchange_fills_store` — the canonical-store-projection rule,
  not a new parallel store.
* **Broker-truth ``realizedPNL`` rides in the row's ``raw`` JSON.** That store's
  schema is declared idempotently (create-if-absent), so a *new typed column*
  would silently not apply to the store already on the VM. ``raw`` is already a
  JSON blob on every row, so IB's extra broker-truth fields land there with
  **no migration** — read them back with :func:`realized_pnl_from_raw`.
* **Honest coverage, not an assumed window.** IBKR's execution history is
  short-lived (the API serves roughly the current trading day; TWS/Gateway
  discards on its nightly reset), which is why this is a **forward-accruing**
  daily pull and NOT a backfill. :func:`coverage_summary` reports how far back
  the venue *actually* reached on each run rather than asserting a window this
  module cannot verify — the same "measured, not asserted" rule the provenance
  work was built on. **It therefore cannot retroactively measure the 226
  historical rows**; those stay a relabelling problem (operator decision
  2026-07-30: historical pass is RELABEL ONLY, never re-price).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

# fetch_executions(since: str) -> Sequence[ib_insync.Fill]
FetchExecutions = Callable[..., Sequence[Any]]

__all__ = [
    "IB_EXEC_TIME_FORMAT",
    "ib_side_to_row_side",
    "ib_fill_to_row",
    "fetch_ib_executions",
    "realized_pnl_from_raw",
    "coverage_summary",
    "closed_pnl_from_fills",
    "IB_EXIT_SOURCE",
]

#: IBKR's ``ExecutionFilter.time`` wire format (UTC, no separators).
IB_EXEC_TIME_FORMAT = "%Y%m%d-%H:%M:%S"


def ib_side_to_row_side(raw: Any) -> Optional[str]:
    """Normalise an IB execution side to the store's ``buy``/``sell``.

    IB reports ``BOT``/``SLD`` on ``Execution.side``; some builds and most test
    stubs use ``BUY``/``SELL``. Anything else returns ``None`` so the caller
    SKIPS the row rather than guessing a direction — a mis-signed fill would
    corrupt every downstream FIFO pairing.
    """
    s = str(raw or "").strip().upper()
    if s in ("BOT", "BUY", "B"):
        return "buy"
    if s in ("SLD", "SELL", "S"):
        return "sell"
    return None


def _f(value: Any) -> Optional[float]:
    """Coerce to float, or ``None``. Never fabricates a zero."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _exec_time_iso(raw: Any) -> Optional[str]:
    """Normalise an IB execution timestamp to an ISO-8601 UTC string.

    ``Execution.time`` is a ``datetime`` on ib_insync; the raw API wire format
    (``YYYYMMDD-HH:MM:SS``) is accepted too so a stubbed/replayed fill maps
    identically. A naive datetime is treated as UTC — IB is configured to report
    UTC and the store indexes on ``datetime(exec_time)``, so a tz-less local
    stamp would silently mis-window. Unparseable ⇒ ``None`` ⇒ the row is skipped
    (an undatable fill cannot be attributed to a trade).
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(raw).strip()
    if not text:
        return None
    for parser in (
        lambda t: datetime.strptime(t, IB_EXEC_TIME_FORMAT).replace(tzinfo=timezone.utc),
        lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")),
    ):
        try:
            dt = parser(text)
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def _contract_multiplier(contract: Any) -> Optional[float]:
    """The futures contract multiplier, when IB declares one.

    Recorded (never applied) so a downstream consumer can tell a per-unit price
    from a contract-value figure without re-deriving it from
    ``config/instruments.yaml``. ``IBClient.positions`` divides by exactly this
    to undo IB's multiplier-inflated ``averageCost`` (BL-20260613-IBPOS).
    """
    return _f(getattr(contract, "multiplier", None))


def ib_fill_to_row(
    fill: Any,
    account_id: str,
) -> Optional[Dict[str, Any]]:
    """Map one IB ``Fill`` → an ``exchange_fills`` row, or ``None`` to skip.

    ``Fill`` is the ib_insync triple ``(contract, execution, commissionReport)``.
    Returns ``None`` — never a partially-fabricated row — when any field the
    store requires (exec id / symbol / side / price / qty / time) is missing or
    unparseable.

    The row's ``raw`` blob carries the IB-only broker-truth fields:

    ``realized_pnl``
        ``CommissionReport.realizedPNL`` — the venue's own realised PnL for this
        execution, already multiplier-correct. **This is the whole point of the
        module**: it is a MEASURED close price basis, so a journal row resolved
        from it leaves the ``local_markprice`` fabrication class entirely.
        ``None`` when IB did not attach a commission report (an opening fill
        realises nothing, so ``None`` here is normal and is NOT an error) — and
        ``None``, not ``0.0``, precisely so "IB reported no realised PnL" stays
        distinguishable from "IB reported break-even".
    ``multiplier`` / ``local_symbol`` / ``ib_account`` / ``order_ref`` / ``perm_id``
        Attribution context for joining a fill back to its journal trade.

    IB's ``commission`` is a POSITIVE cost; the store's ``fee`` column follows
    the same convention as the Bybit/Alpaca adapters (positive = paid), so it is
    carried through unchanged.
    """
    execution = getattr(fill, "execution", None)
    if execution is None:
        return None
    contract = getattr(fill, "contract", None)
    report = getattr(fill, "commissionReport", None)

    exec_id = getattr(execution, "execId", None) or getattr(execution, "exec_id", None)
    side = ib_side_to_row_side(getattr(execution, "side", None))
    # Trade by the GENERIC root (``MES``/``MHG``), the axis the journal,
    # reconciler and every other consumer speak — NOT ``localSymbol``, which
    # carries the expiry month code and can never join back (BL-20260613-IBPOS).
    symbol = getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None)
    price = _f(getattr(execution, "price", None))
    qty = _f(getattr(execution, "shares", None))
    if qty is None:
        qty = _f(getattr(execution, "cumQty", None))
    exec_time = _exec_time_iso(getattr(execution, "time", None))

    if not exec_id or not symbol or side is None:
        return None
    if price is None or qty is None or qty <= 0 or not exec_time:
        return None

    realized = _f(getattr(report, "realizedPNL", None)) if report is not None else None
    # IB uses a sentinel for "not applicable" on realizedPNL in some builds
    # rather than omitting the field. Treat it as absent, not as a real loss of
    # 1.8e308 — a fabricated number is exactly what this module exists to stop.
    if realized is not None and abs(realized) > 1e17:
        realized = None
    commission = _f(getattr(report, "commission", None)) if report is not None else None

    raw: Dict[str, Any] = {
        "source": "ib_reqExecutions",
        "realized_pnl": realized,
        "multiplier": _contract_multiplier(contract),
        "local_symbol": (
            str(getattr(contract, "localSymbol", "") or "") or None
        ),
        "sec_type": str(getattr(contract, "secType", "") or "") or None,
        "ib_account": str(getattr(execution, "acctNumber", "") or "") or None,
        "order_ref": str(getattr(execution, "orderRef", "") or "") or None,
        "perm_id": getattr(execution, "permId", None),
        "last_liquidity": getattr(execution, "lastLiquidity", None),
    }

    return {
        "exec_id": str(exec_id),
        "account_id": account_id,
        "symbol": str(symbol),
        "side": side,
        "price": price,
        "qty": qty,
        # ``None`` commission ⇒ honest 0.0 in the typed column (the schema is
        # NOT NULL), but the raw blob keeps the distinction: a consumer that
        # needs "was a commission actually reported" reads raw, not fee.
        "fee": commission if commission is not None else 0.0,
        "fee_currency": (
            str(getattr(report, "currency", "") or "") or None
        ) if report is not None else None,
        "exec_time": exec_time,
        "order_id": (
            str(getattr(execution, "orderId", "") or "") or None
        ),
        # IB's lastLiquidity: 1 = added liquidity (maker), 2 = removed (taker).
        "is_maker": 1 if getattr(execution, "lastLiquidity", None) == 1 else 0,
        "raw": raw,
    }


def _since_bound(days: int, now: Optional[datetime] = None) -> str:
    """IB ``ExecutionFilter.time`` wire string for *days* back from *now*."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    bound = (now - timedelta(days=max(1, int(days)))).astimezone(timezone.utc)
    return bound.strftime(IB_EXEC_TIME_FORMAT)


def fetch_ib_executions(
    fetch_executions: FetchExecutions,
    *,
    account_id: str,
    days: int = 2,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Fetch + map an account's executions over the last *days*.

    *fetch_executions* is injected — ``fetch_executions(since=<IB time str>)``
    returns the raw ``Fill`` sequence. IB's ``reqExecutions`` is a single
    unpaginated request (unlike Alpaca's cursor), so there is no page loop.

    Deduping is the store's job (``exec_id`` PRIMARY KEY), so an overlapping
    window is safe and the default ``days=2`` deliberately over-samples the
    daily cadence — a missed run is picked up by the next one, to whatever
    extent the venue still holds the history.

    A fill that cannot be mapped cleanly is DROPPED, never coerced. Use
    :func:`coverage_summary` on the result to report how many were dropped and
    how far back the venue actually reached.
    """
    since = _since_bound(days, now)
    fills = list(fetch_executions(since=since) or [])
    rows: List[Dict[str, Any]] = []
    for fill in fills:
        row = ib_fill_to_row(fill, account_id)
        if row is not None:
            rows.append(row)
    return rows


def realized_pnl_from_raw(raw: Any) -> Optional[float]:
    """Read broker-truth ``realized_pnl`` back out of a stored row's ``raw``.

    Accepts the JSON string the store persists or an already-decoded mapping.
    Returns ``None`` when absent/unparseable — the honest "not measured" value,
    never ``0.0`` (see :mod:`src.runtime.provenance`: absence of a record is not
    evidence of a break-even fill).
    """
    if raw is None:
        return None
    blob: Any = raw
    if isinstance(blob, (str, bytes)):
        try:
            blob = json.loads(blob)
        except (ValueError, TypeError):
            return None
    if not isinstance(blob, Mapping):
        return None
    return _f(blob.get("realized_pnl"))


def coverage_summary(rows: Sequence[Mapping[str, Any]], *, raw_fill_count: int) -> Dict[str, Any]:
    """What the venue ACTUALLY served — the honest denominator for a pull.

    IBKR's execution retention is short and this module refuses to assert a
    window it cannot verify, so every run reports what it really got:
    ``oldest_exec_time`` / ``newest_exec_time`` are the true reach, and
    ``realized_pnl_count`` is how many fills carried broker-truth realised PnL
    (i.e. how many closes this pull can make MEASURED).

    ``dropped`` counts fills that failed mapping — a non-zero value is a real
    signal worth logging, not noise.
    """
    times = sorted(str(r.get("exec_time")) for r in rows if r.get("exec_time"))
    realized = sum(
        1 for r in rows if realized_pnl_from_raw(r.get("raw")) is not None
    )
    return {
        "mapped": len(rows),
        "raw_fills": int(raw_fill_count),
        "dropped": max(0, int(raw_fill_count) - len(rows)),
        "oldest_exec_time": times[0] if times else None,
        "newest_exec_time": times[-1] if times else None,
        "realized_pnl_count": realized,
    }


# --------------------------------------------------------------------------
# Read-back: the fills store -> a broker-truth closed-PnL record
# --------------------------------------------------------------------------
#: ``exit_price_source`` stamped on a journal row resolved from an IB execution.
#: Declared in ``provenance.MEASURED_SOURCES`` — this is a venue-reported fill.
IB_EXIT_SOURCE = "ib_execution"

#: Relative tolerance on the qty match. Mirrors the 5 % the Bybit closed-pnl
#: lookup uses (``account_closed_pnl_for_trade``'s ``qty`` filter) so both
#: readers reject a partial-close cycle the same way.
_QTY_TOLERANCE = 0.05

#: Slack on the open bound, absorbing sub-second skew between the bot's wall
#: clock and IB's execution timestamps. Same 60 s the Bybit lookup uses.
_OPEN_SLACK_MS = 60_000


def closed_pnl_from_fills(
    *,
    account_id: str,
    symbol: str,
    direction: str,
    opened_at_ms: int,
    closed_at_ms: Optional[int] = None,
    qty: Optional[float] = None,
    conn_factory: Optional[Callable[[], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """IB broker-truth close for one journal trade.

    Thin wrapper over the venue-neutral :func:`src.runtime.fills_pnl.exit_from_fills`
    with ``require_realized=True`` — IBKR is the one venue that serves its own
    per-fill realised PnL, so it is the one venue whose result carries
    ``closed_pnl`` directly. Kept as a named entry point because the module
    docstring, the puller and the tests all refer to it.
    """
    from src.runtime.fills_pnl import exit_from_fills

    return exit_from_fills(
        account_id=account_id,
        symbol=symbol,
        direction=direction,
        opened_at_ms=opened_at_ms,
        closed_at_ms=closed_at_ms,
        qty=qty,
        require_realized=True,
        conn_factory=conn_factory,
    )
