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

-- bybit_transaction_log: the venue's OWN wallet ledger (/v5/account/transaction-log,
-- pybit get_transaction_log). Added 2026-08-31 by operator directive, replacing a
-- hand-pasted UM CSV export as the source of account-level wallet truth
-- (BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-CLOSES-UNRECONCILED: the
-- authoritative figure for a real-money account froze on 2026-07-13 while the
-- account kept trading). It lives HERE, beside exchange_fills, because this store
-- is already the standalone venue-truth store and is deliberately NOT a projection
-- of trade_journal.db -- so a reconciliation read can never contend with, or be
-- contaminated by, the money DB it exists to check.
--
-- `change` is the signed wallet delta for the row (the UM export's "Change"
-- column). It is stored VERBATIM alongside the raw payload; the P&L definition
-- -- which types count, which currencies -- lives in src/runtime/bybit_wallet_truth.py
-- so it is arguable in tests rather than baked into a schema.
CREATE TABLE IF NOT EXISTS bybit_transaction_log (
    txn_id       TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL,
    txn_type     TEXT NOT NULL,
    currency     TEXT,
    symbol       TEXT,
    change_usd   REAL,
    fee          REAL,
    funding      REAL,
    cash_balance REAL,
    txn_time     TEXT NOT NULL,
    txn_time_ms  INTEGER,
    raw          TEXT,
    inserted_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bybit_txnlog_acct_time
    ON bybit_transaction_log (account_id, txn_time_ms DESC);
CREATE INDEX IF NOT EXISTS idx_bybit_txnlog_type
    ON bybit_transaction_log (account_id, txn_type);
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


def upsert_transaction_log(
    rows: Iterable[Mapping[str, Any]],
    account_id: str,
    path: Optional[Path] = None,
) -> int:
    """Idempotent insert of Bybit transaction-log rows. Returns rows inserted.

    Keyed on the venue's own ``id``, so re-pulling an overlapping window is a
    no-op rather than a double-count -- which matters more here than for fills,
    because these rows are SUMMED into an account-level P&L figure and a
    duplicate would move it.
    """
    p = init_db(path)
    conn = sqlite3.connect(str(p))
    inserted = 0
    try:
        for row in rows:
            txn_id = str(row.get("id") or row.get("txn_id") or "").strip()
            if not txn_id:
                # A row we cannot key is a row we cannot de-duplicate; skipping
                # it is safer than minting a synthetic key that would let the
                # same money be counted twice on the next overlapping pull.
                continue
            ms = row.get("transactionTime") or row.get("txn_time_ms")
            try:
                ms_i = int(ms) if ms not in (None, "") else None
            except (TypeError, ValueError):
                ms_i = None
            iso = (
                datetime.fromtimestamp(ms_i / 1000.0, tz=timezone.utc).isoformat()
                if ms_i
                else str(row.get("txn_time") or "")
            )

            def _num(key: str):
                v = row.get(key)
                if v in (None, ""):
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            cur = conn.execute(
                "INSERT OR IGNORE INTO bybit_transaction_log ("
                "txn_id, account_id, txn_type, currency, symbol, change_usd, "
                "fee, funding, cash_balance, txn_time, txn_time_ms, raw"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    txn_id,
                    account_id,
                    str(row.get("type") or "").upper(),
                    str(row.get("currency") or "").upper() or None,
                    row.get("symbol") or None,
                    _num("change"),
                    _num("fee"),
                    _num("funding"),
                    _num("cashBalance"),
                    iso,
                    ms_i,
                    json.dumps(dict(row), default=str),
                ),
            )
            inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
    finally:
        conn.close()
    return inserted


def list_transaction_log(
    account_id: Optional[str] = None,
    since_ms: Optional[int] = None,
    until_ms: Optional[int] = None,
    path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Transaction-log rows for an account/window, shaped for
    ``bybit_wallet_truth.compute_wallet_truth``.

    Returns ``[]`` for a genuinely empty window. The CALLER decides whether an
    empty list means "flat" or "never pulled" -- this function only reports what
    the store holds, and conflating those is the collapse the wallet-truth
    module's states exist to prevent.
    """
    p = init_db(path)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        sql = ["SELECT * FROM bybit_transaction_log WHERE 1=1"]
        args: list[Any] = []
        if account_id:
            sql.append("AND account_id = ?")
            args.append(account_id)
        if since_ms is not None:
            sql.append("AND txn_time_ms >= ?")
            args.append(int(since_ms))
        if until_ms is not None:
            sql.append("AND txn_time_ms <= ?")
            args.append(int(until_ms))
        sql.append("ORDER BY txn_time_ms ASC")
        out = []
        for r in conn.execute(" ".join(sql), tuple(args)).fetchall():
            out.append({
                "id": r["txn_id"],
                "type": r["txn_type"],
                "currency": r["currency"],
                "symbol": r["symbol"],
                "change": r["change_usd"],
                "fee": r["fee"],
                "funding": r["funding"],
                "transactionTime": r["txn_time_ms"],
            })
        return out
    finally:
        conn.close()


def _account_filter(account_id: Optional[str]) -> tuple[str, tuple[Any, ...]]:
    """SQL fragment + bound params scoping an aggregate to one account.

    Returns ``("", ())`` when *account_id* is falsy, so every caller that
    omits it keeps byte-identical behaviour. The value is ALWAYS bound, never
    interpolated. Backed by the ``(account_id, exec_time)`` index declared in
    the schema, so the scoped read is not a table scan.

    Why this exists: the pooled cross-account aggregate mixes real-money
    crypto fills with paper equity fills, so its headline realized figure is
    not attributable to any one book and must not be quoted as one
    (WORKPLAN-2026-08-05 P0.2a).
    """
    if not account_id:
        return "", ()
    return " AND account_id = ?", (account_id,)


def list_fills(
    days: int,
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
    account_id: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """The individual fill ROWS, newest-first. No attribution, no netting.

    **Why a raw-row reader exists next to the aggregates.** Every other reader
    here returns a SUM. That is the right default, but it cannot answer "which
    of these trades does the discrepancy belong to" when several strategies
    trade one symbol on one account — and it is not a substrate you can
    hand-verify an attributor against. On 2026-08-07 a measured $4,266.32 gap
    between the journal and exchange truth across three AVAXUSDT/bybit_1 trades
    could not be split, because the only surfaces were per-symbol aggregates
    (BL-20260807-EXCHANGE-TRUTH-PER-STRATEGY-UNREACHABLE). That population was
    SIX fills; the rows answer it exactly.

    This deliberately does NOT attribute fills to strategies. An
    ``order_id -> strategy`` map can only cover ENTRIES: a broker SL/TP exit
    fills under an order id the bot never sees (see
    ``src/runtime/broker_cost_attribution.py``), so exits must be FIFO-paired
    against open entry lots. A per-strategy aggregate built on entry-only
    attribution would silently bucket every exit as ``unattributed`` and report
    a confident wrong split. Rows first; an attributor can be built and then
    CHECKED against them.

    ``limit`` is clamped to 1..1000. ``symbol`` matches the stored symbol
    exactly (venue form, e.g. ``AVAX/USDT:USDT``) and is always bound.
    """
    if days <= 0:
        return []
    p = path or get_fills_db_path()
    if not p.exists():
        return []
    lim = max(1, min(int(limit or 0), 1000))
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    _acct_sql, _acct_params = _account_filter(account_id)
    _sym_sql, _sym_params = ("", ())
    if symbol:
        _sym_sql, _sym_params = " AND symbol = ?", (symbol,)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT exec_id, account_id, symbol, side, price, qty, fee,
                   fee_currency, exec_time, order_id, is_maker
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            """ + _acct_sql + _sym_sql + """
            ORDER BY datetime(exec_time) DESC, exec_id DESC
            LIMIT ?
            """,
            (cutoff, *_acct_params, *_sym_params, lim),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def aggregate_by_symbol(
    days: int,
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
    account_id: Optional[str] = None,
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
    _acct_sql, _acct_params = _account_filter(account_id)
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
            """ + _acct_sql + """
            GROUP BY symbol
            ORDER BY symbol
            """,
            (cutoff, *_acct_params),
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
    account_id: Optional[str] = None,
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
    _acct_sql, _acct_params = _account_filter(account_id)
    conn = sqlite3.connect(str(p))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(fee), 0),
                   COUNT(DISTINCT symbol)
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            """ + _acct_sql + """
            """,
            (cutoff, *_acct_params),
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
    account_id: Optional[str] = None,
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
    _acct_sql, _acct_params = _account_filter(account_id)
    conn = sqlite3.connect(str(p))
    try:
        cur = conn.execute(
            """
            SELECT symbol, side, price, qty, fee
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            """ + _acct_sql + """
            ORDER BY symbol, datetime(exec_time), exec_id
            """,
            (cutoff, *_acct_params),
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
    account_id: Optional[str] = None,
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
    _acct_sql, _acct_params = _account_filter(account_id)
    conn = sqlite3.connect(str(p))
    try:
        cur = conn.execute(
            """
            SELECT order_id, side, price, qty, fee
            FROM exchange_fills
            WHERE datetime(exec_time) >= datetime(?)
            """ + _acct_sql + """
            ORDER BY datetime(exec_time), exec_id
            """,
            (cutoff, *_acct_params),
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
