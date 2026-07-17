"""Local sqlite store for Bybit fill records (S-067 follow-up #6).

Phase-1 of the exchange-fills P&L attribution feature: store raw fill
records pulled from Bybit ``GET /v5/execution/list`` (or ccxt's
``fetch_my_trades`` wrapper around the same endpoint). Read-path
endpoints can compute per-strategy / per-symbol fee totals + flow
volumes from this store rather than from
``trade_journal.db::trades``, insulating performance reads from any
local schema or state bug.

The store is intentionally separate from ``trade_journal.db``:

- Different lifecycle: trades are written by the runtime; fills are
  pulled out-of-band from the exchange.
- Different trust contract: when local + exchange disagree, exchange
  wins. The two stores must not share a connection or transaction.
- Different gitignore class: ``trade_journal.db`` lives at repo
  root (gitignored individually); fills live under ``runtime_state/``
  (gitignored as a directory) alongside ``prop_state.json``.

The :func:`upsert_fills` helper is **idempotent** — the same fill
inserted twice produces a single row, keyed by Bybit's ``exec_id``.
This makes the puller safe to re-run on overlapping windows.

Phase-2 (S-067 follow-up C) adds FIFO lot-matching P&L attribution
via :func:`fifo_pnl_by_symbol` / :func:`_fifo_match` — realised
matched-lot PnL plus unrealised mark-to-last-fill on residual open
lots. The Phase-1 aggregate helpers are unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from src.utils.paths import runtime_state_dir

logger = logging.getLogger(__name__)

_DEFAULT_FILLS_DB_PATH = runtime_state_dir() / "exchange_fills.sqlite"


def get_fills_db_path() -> Path:
    """Resolve the fills DB path. Override via ``EXCHANGE_FILLS_DB``."""
    env = os.environ.get("EXCHANGE_FILLS_DB")
    if env:
        return Path(env)
    return _DEFAULT_FILLS_DB_PATH


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------

# data-wiring: exchange_funding is the canonical store for exchange perp FUNDING
#              charges (Slice B / B1, MB-20260629-ALLOC-COSTCAP). No existing
#              table holds funding — it is NOT in the execution/fills list (a
#              separate Bybit fetch_funding_history stream), so nothing else is
#              the source of truth. Pulled by scripts/pull_exchange_funding.py
#              (idempotent on funding_id); read only by the offline broker-truth
#              cost sweep to attribute funding_paid_usd onto clean closed trades.
#              Never touches trade_journal.db or the order path. (exchange_fills,
#              its sibling, predates this and is likewise a standalone exchange-
#              truth store, not a projection of trade_journal.db.)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS exchange_fills (
    exec_id        TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL,
    price          REAL NOT NULL,
    qty            REAL NOT NULL,
    fee            REAL NOT NULL DEFAULT 0,
    fee_currency   TEXT,
    exec_time      TEXT NOT NULL,
    order_id       TEXT,
    is_maker       INTEGER NOT NULL DEFAULT 0,
    raw            TEXT,
    inserted_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exchange_fills_account_time
    ON exchange_fills (account_id, datetime(exec_time) DESC);
CREATE INDEX IF NOT EXISTS idx_exchange_fills_symbol_time
    ON exchange_fills (symbol, datetime(exec_time) DESC);

CREATE TABLE IF NOT EXISTS exchange_funding (
    funding_id   TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    funding_usd  REAL NOT NULL DEFAULT 0,
    funding_time TEXT NOT NULL,
    raw          TEXT,
    inserted_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exchange_funding_acct_time
    ON exchange_funding (account_id, datetime(funding_time) DESC);
"""


def init_db(path: Optional[Path] = None) -> Path:
    """Create the store at *path* if it does not exist. Idempotent."""
    p = path or get_fills_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return p


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def _normalise_fill(row: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce a fill record (Bybit V5 / ccxt-shaped) into the store
    schema. The puller is responsible for picking the right field
    aliases for its source; this helper just casts and fills defaults.
    """
    return {
        "exec_id": str(row["exec_id"]),
        "account_id": str(row["account_id"]),
        "symbol": str(row["symbol"]),
        "side": str(row["side"]).lower(),
        "price": float(row["price"]),
        "qty": float(row["qty"]),
        "fee": float(row.get("fee") or 0.0),
        "fee_currency": row.get("fee_currency"),
        "exec_time": str(row["exec_time"]),
        "order_id": row.get("order_id"),
        "is_maker": 1 if row.get("is_maker") else 0,
        "raw": json.dumps(row.get("raw")) if row.get("raw") is not None else None,
    }


def upsert_fills(
    rows: Iterable[Mapping[str, Any]],
    path: Optional[Path] = None,
) -> int:
    """Idempotently insert fill records keyed by ``exec_id``.

    Returns the number of NEW rows inserted (existing exec_ids are
    silently ignored — re-running the puller on overlapping windows
    is safe by design).
    """
    p = init_db(path)
    inserted = 0
    conn = sqlite3.connect(str(p))
    try:
        for raw in rows:
            row = _normalise_fill(raw)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO exchange_fills (
                    exec_id, account_id, symbol, side, price, qty, fee,
                    fee_currency, exec_time, order_id, is_maker, raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["exec_id"], row["account_id"], row["symbol"],
                    row["side"], row["price"], row["qty"], row["fee"],
                    row["fee_currency"], row["exec_time"], row["order_id"],
                    row["is_maker"], row["raw"],
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def upsert_funding(
    rows: Iterable[Mapping[str, Any]],
    path: Optional[Path] = None,
) -> int:
    """Idempotently insert perp-funding records keyed by ``funding_id``.

    Slice B / B1 (MB-20260629-ALLOC-COSTCAP). ``funding_usd`` is SIGNED —
    negative = funding paid, positive = funding received. Returns the number of
    NEW rows inserted (existing funding_ids ignored — re-running is safe).
    """
    p = init_db(path)
    inserted = 0
    conn = sqlite3.connect(str(p))
    try:
        for raw in rows:
            fid = raw.get("funding_id")
            if not fid:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO exchange_funding "
                "(funding_id, account_id, symbol, funding_usd, funding_time, raw) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(fid), str(raw.get("account_id")), str(raw.get("symbol")),
                    float(raw.get("funding_usd") or 0.0), str(raw.get("funding_time")),
                    json.dumps(raw.get("raw")) if raw.get("raw") is not None else None,
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


# ---------------------------------------------------------------------------
# Aggregates (read-side, used by /api/bot/pnl/exchange)
# ---------------------------------------------------------------------------


def aggregate_by_symbol(
    days: int,
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Per-symbol fee + gross-volume aggregate over the last *days*.

    Phase-1 of S-067 follow-up #6 — fee totals + gross flow only.
    True P&L attribution lives in :func:`fifo_pnl_by_symbol`
    (Phase-2). The endpoint
    (``src/web/api/routers/pnl_exchange.py``) merges the two sets of
    fields into a single response.
    """
    if days <= 0:
        return []
    p = path or get_fills_db_path()
    if not p.exists():
        return []
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT symbol,
                   COUNT(*)                                    AS fill_count,
                   COALESCE(SUM(qty), 0)                       AS gross_qty,
                   COALESCE(SUM(qty * price), 0)               AS gross_notional,
                   COALESCE(SUM(fee), 0)                       AS total_fees,
                   MIN(exec_time)                              AS first_exec_time,
                   MAX(exec_time)                              AS last_exec_time
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            GROUP BY symbol
            ORDER BY symbol
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def aggregate_summary(
    days: int,
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Cross-symbol summary over the last *days*."""
    if days <= 0:
        return {"fill_count": 0, "total_fees": 0.0, "symbol_count": 0,
                "window_days": days}
    p = path or get_fills_db_path()
    if not p.exists():
        return {"fill_count": 0, "total_fees": 0.0, "symbol_count": 0,
                "window_days": days}
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(p))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(fee), 0),
                   COUNT(DISTINCT symbol)
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            """,
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()
    return {
        "fill_count": int(row[0] or 0),
        "total_fees": float(row[1] or 0.0),
        "symbol_count": int(row[2] or 0),
        "window_days": days,
    }


# ---------------------------------------------------------------------------
# FIFO lot-matching P&L (Phase-2 of S-067 follow-up #6)
# ---------------------------------------------------------------------------
#
# Walks the fills stream per-symbol in time order and pairs opposing-side
# fills FIFO (first buy lot is matched against the first sell, etc.).
# Realised P&L = sum((sell_price - buy_price) * matched_qty) for long lots,
# sum((short_price - cover_price) * matched_qty) for short lots, minus all
# fees in the window. Unrealised P&L marks remaining open lots against the
# last observed fill price for the symbol — a defensible mark-price proxy
# for the read-path; a real mark-price feed is not in this PR's scope.
#
# Wire-shape additions are strictly additive:
#   summary  ← total_realized_pnl, total_unrealized_pnl
#   by_symbol[i] ← realized_pnl, unrealized_pnl, open_qty_signed,
#                  last_price


_EPS = 1e-12  # qty rounding tolerance


def _fifo_match(
    fills: Iterable[tuple[str, float, float, float]],
) -> tuple[float, float, float, float]:
    """FIFO lot-matching engine for one symbol's fills stream.

    ``fills`` is an iterable of ``(side, price, qty, fee)`` tuples,
    sorted ascending by exec_time. Returns ``(realized_pnl,
    unrealized_pnl, open_qty_signed, last_price)`` where:

    * ``realized_pnl`` = matched buy/sell pair PnL minus all fees
      seen in the window. Fees are always realised (the operator
      pays them on every fill regardless of close timing).
    * ``unrealized_pnl`` = ``(last_price - lot_price) * lot_qty`` for
      each remaining open lot, summed. Long lots contribute
      positively when ``last_price > lot_price``; short lots
      (negative qty) contribute positively when
      ``last_price < lot_price``.
    * ``open_qty_signed`` = net residual position size (positive =
      long, negative = short, ~0 = flat).
    * ``last_price`` = the most recent fill price (mark proxy).
    """
    queue: list[list[float]] = []  # [signed_qty, price] FIFO; lists for in-place edits.
    realized = 0.0
    last_price = 0.0
    for side, price, qty, fee in fills:
        last_price = price
        # Fees reduce realised P&L on every fill regardless of close
        # timing — the operator pays them either way.
        realized -= fee
        signed = qty if side == "buy" else -qty
        # Match against queue head while the head has opposite sign.
        while queue and abs(signed) > _EPS and queue[0][0] * signed < 0:
            head_qty, head_price = queue[0]
            match = min(abs(signed), abs(head_qty))
            if head_qty > 0:
                # Long lot being closed by a sell.
                realized += (price - head_price) * match
            else:
                # Short lot being covered by a buy.
                realized += (head_price - price) * match
            new_head_qty = (
                head_qty - match if head_qty > 0 else head_qty + match
            )
            if abs(new_head_qty) < _EPS:
                queue.pop(0)
            else:
                queue[0][0] = new_head_qty
            signed = signed + match if signed < 0 else signed - match
        if abs(signed) > _EPS:
            queue.append([signed, price])

    open_qty_signed = sum(q for q, _ in queue)
    unrealized = sum((last_price - p) * q for q, p in queue)
    return realized, unrealized, open_qty_signed, last_price


def fifo_pnl_by_symbol(
    days: int,
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Per-symbol realised + unrealised P&L over the last *days*.

    Phase-2 of S-067 follow-up #6. Returns one row per symbol with
    fields keyed for additive merge into ``aggregate_by_symbol``'s
    output (the endpoint does the merge — see
    ``src/web/api/routers/pnl_exchange.py``).
    """
    if days <= 0:
        return []
    p = path or get_fills_db_path()
    if not p.exists():
        return []
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(p))
    try:
        cur = conn.execute(
            """
            SELECT symbol, side, price, qty, fee
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            ORDER BY symbol, datetime(exec_time), exec_id
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    by_symbol: dict[str, list[tuple[str, float, float, float]]] = {}
    for symbol, side, price, qty, fee in rows:
        by_symbol.setdefault(symbol, []).append(
            (str(side).lower(), float(price), float(qty), float(fee or 0.0))
        )

    out: list[dict[str, Any]] = []
    for symbol in sorted(by_symbol):
        realized, unrealized, open_qty, last_price = _fifo_match(by_symbol[symbol])
        out.append({
            "symbol": symbol,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "open_qty_signed": open_qty,
            "last_price": last_price,
        })
    return out


# ---------------------------------------------------------------------------
# Per-strategy net-of-fee attribution (cross-zero P3c)
# ---------------------------------------------------------------------------
#
# The "did we cross zero, per strategy?" measurement. For each strategy it
# reports the three numbers the loss-driver audit
# (docs/audits/strategy-loss-drivers-2026-05-23.md) made the headline:
#
#   gross_pnl          — FIFO matched P&L BEFORE fees
#   total_fees         — fees paid on every fill in the window
#   net_pnl            — gross_pnl - total_fees (the bottom line)
#   fee_pct_of_gross   — total_fees / gross_pnl * 100 (vwap's was 418%)
#
# Attribution requires a caller-supplied ``strategy_of_order_id`` map because
# ``exchange_fills`` stores the EXCHANGE order id, while the strategy name
# lives in ``trade_journal.db`` (order_packages / trades). Resolving that map
# from the live DBs is a separate, schema-specific concern (P3b) — this
# function is the pure, testable aggregation that consumes it. Fills whose
# order_id is absent from the map are grouped under ``"unattributed"`` rather
# than dropped, so the totals always reconcile.
#
# Caveat (documented, not hidden): the live book is ONE shared netted BTCUSDT
# position, so per-strategy FIFO matches a strategy's own fills against each
# other — an attribution approximation, the same one the audit uses. It is a
# read-path diagnostic, never an order-path input.


def fifo_pnl_by_strategy(
    days: int,
    strategy_of_order_id: Mapping[str, str],
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Per-strategy gross / fees / net / fee-%-of-gross over the last *days*.

    Parameters
    ----------
    days : int
        Look-back window in days. ``<= 0`` returns ``[]``.
    strategy_of_order_id : Mapping[str, str]
        ``exchange order_id -> strategy name``. Fills whose ``order_id`` is
        missing (or ``None``) are bucketed under ``"unattributed"``.
    path : Path, optional
        Fills DB path; defaults to :func:`get_fills_db_path`.
    now : datetime, optional
        Clock injection for deterministic tests.

    Returns
    -------
    list[dict]
        One row per strategy (sorted by name), each with ``strategy``,
        ``gross_pnl``, ``total_fees``, ``net_pnl``, ``fee_pct_of_gross``
        (``None`` when gross is ~0 — undefined, not infinite), and
        ``fill_count``.
    """
    if days <= 0:
        return []
    p = path or get_fills_db_path()
    if not p.exists():
        return []
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(p))
    try:
        cur = conn.execute(
            """
            SELECT order_id, side, price, qty, fee
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            ORDER BY datetime(exec_time), exec_id
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    # Partition fills by strategy, preserving time order within each bucket.
    by_strategy: dict[str, list[tuple[str, float, float, float]]] = {}
    fees_by_strategy: dict[str, float] = {}
    for order_id, side, price, qty, fee in rows:
        strat = strategy_of_order_id.get(str(order_id)) if order_id is not None else None
        strat = strat or "unattributed"
        by_strategy.setdefault(strat, []).append(
            (str(side).lower(), float(price), float(qty), float(fee or 0.0))
        )
        fees_by_strategy[strat] = fees_by_strategy.get(strat, 0.0) + float(fee or 0.0)

    out: list[dict[str, Any]] = []
    for strat in sorted(by_strategy):
        fills = by_strategy[strat]
        # ``_fifo_match`` returns realised P&L already NET of fees; add the
        # fees back to recover gross, so the three numbers reconcile exactly.
        realized_net, _unrealized, _open_qty, _last = _fifo_match(fills)
        fees = fees_by_strategy.get(strat, 0.0)
        gross = realized_net + fees
        fee_pct = (fees / gross * 100.0) if abs(gross) > _EPS else None
        out.append({
            "strategy": strat,
            "gross_pnl": gross,
            "total_fees": fees,
            "net_pnl": realized_net,
            "fee_pct_of_gross": fee_pct,
            "fill_count": len(fills),
        })
    return out
