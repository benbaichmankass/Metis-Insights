"""Per-signal account-context snapshots (S-MLOPT-S12 Part B, M14 Phase 2.4).

The `account_context` dataset family (`ml/datasets/families/account_context.py`)
wants to learn what account state — equity, daily PnL, drawdown, open-trade
count — was true **at signal-emit time**, so a meta-label / risk model can
say "this strategy fires N% acceptance when the account is already at -1.5%
intraday and holding 3 positions". The roadmap (`MB-20260604-003`) flagged
that those values are NOT recorded as-of signal-time today:

- `trade_journal.db::trades` only captures post-decision state.
- `daily_risk_state` carries end-of-interval daily totals — a post-hoc join
  against it leaks (the row reflects state AFTER the signal's own trade
  affected it).
- `balance_snapshots.json` is hourly, not per-signal.

This module is the per-signal snapshot writer. The coordinator hook
(`Coordinator.multi_account_execute`) calls `capture_snapshot()` once per
(strategy-signal, candidate-account) — BEFORE the per-account RiskManager
runs `position_size()` and BEFORE any order is sent — and writes one row
into a new SQLite table. The `account_context` family then LEFT JOINs that
table on `(order_package_id, account_id)` to attach the snapshot columns
to its existing trade rows.

**Best-effort, never raises**: the writer catches every exception and
logs, so a SQLite hiccup can never block the trading loop. The trader's
flow is unconditional.

**Default-off via flag** (the conservative pattern S13/S15b/S17 used):
the coordinator hook honours `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED` — set
truthy to short-circuit if a regression turns up. Both directions are
benign: extra rows in a separate table that the family ignores when its
`include_snapshots` kwarg is off; missing rows just mean the snapshot
columns serialize as NULL.

Schema (CREATE TABLE IF NOT EXISTS, idempotent):

    account_context_snapshots (
      id                    INTEGER PRIMARY KEY AUTOINCREMENT,
      captured_at_utc       TEXT NOT NULL,          -- ISO-8601, microsecond
      order_package_id      TEXT,                   -- joins to order_packages.id
      account_id            TEXT NOT NULL,
      strategy_name         TEXT,
      symbol                TEXT,
      direction             TEXT,
      equity                REAL,                   -- USD; per-account balance
      daily_pnl_realized    REAL,                   -- realized PnL today (UTC)
      daily_equity_high     REAL,                   -- intraday peak equity
      daily_drawdown_pct    REAL,                   -- (equity_high - equity) / equity_high
      open_trades_count     INTEGER,                -- across this account, status='open'
      writer_version        TEXT NOT NULL DEFAULT 'v1',
      UNIQUE(order_package_id, account_id)
    );

The unique-key idempotency means a re-fired evaluation against the same
account is a no-op rather than a duplicate row — important for the
coordinator that may retry per-account dispatch.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

WRITER_VERSION = "v1"

# Schema kept in module scope so tests can apply it to an in-memory DB
# without importing the production resolver.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS account_context_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at_utc       TEXT NOT NULL,
    order_package_id      TEXT,
    account_id            TEXT NOT NULL,
    strategy_name         TEXT,
    symbol                TEXT,
    direction             TEXT,
    equity                REAL,
    daily_pnl_realized    REAL,
    daily_equity_high     REAL,
    daily_drawdown_pct    REAL,
    open_trades_count     INTEGER,
    writer_version        TEXT NOT NULL DEFAULT 'v1',
    UNIQUE(order_package_id, account_id)
)
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO account_context_snapshots (
    captured_at_utc, order_package_id, account_id,
    strategy_name, symbol, direction,
    equity, daily_pnl_realized, daily_equity_high, daily_drawdown_pct,
    open_trades_count, writer_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# Index for the family's join path (looked up by order_package_id +
# account_id). The unique constraint already gives us this index for
# free, but naming it makes EXPLAIN QUERY PLAN readable.


@dataclass(frozen=True)
class AccountContextSnapshot:
    """The state-at-signal-time row the coordinator captures + the family reads.

    Equity / daily-PnL / equity-high / drawdown / open-trade fields are
    nullable because the trader can dispatch with partial state (e.g. a
    cached balance is unavailable, or the per-account `RiskManager`
    hasn't observed a closed trade yet on a fresh boot). Callers should
    pass ``None`` rather than ``0.0`` when a value is genuinely unknown,
    so the family can distinguish missing-vs-flat downstream.
    """

    captured_at_utc: datetime
    order_package_id: str | None
    account_id: str
    strategy_name: str | None = None
    symbol: str | None = None
    direction: str | None = None
    equity: float | None = None
    daily_pnl_realized: float | None = None
    daily_equity_high: float | None = None
    daily_drawdown_pct: float | None = None
    open_trades_count: int | None = None

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.captured_at_utc.isoformat(timespec="microseconds"),
            self.order_package_id,
            self.account_id,
            self.strategy_name,
            self.symbol,
            self.direction,
            self.equity,
            self.daily_pnl_realized,
            self.daily_equity_high,
            self.daily_drawdown_pct,
            self.open_trades_count,
            WRITER_VERSION,
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the table if absent. Idempotent."""
    conn.execute(_SCHEMA_SQL)
    conn.commit()


def write_snapshots(
    db_path: Path | str,
    snapshots: Iterable[AccountContextSnapshot],
) -> int:
    """Persist a batch of snapshots; return rows written.

    Best-effort: a SQLite error is logged and swallowed. Returns 0 when
    the batch is empty, when the DB is unreachable, or when every row
    collided with the unique key.
    """
    rows = [snap.to_row() for snap in snapshots]
    if not rows:
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        _LOGGER.warning(
            "account_context_snapshots: open(%s) failed (%s); dropping %d rows",
            db_path, exc, len(rows),
        )
        return 0
    try:
        ensure_schema(conn)
        cur = conn.executemany(_INSERT_SQL, rows)
        conn.commit()
        return cur.rowcount or 0
    except sqlite3.Error as exc:
        _LOGGER.warning(
            "account_context_snapshots: insertmany failed (%s); dropping %d rows",
            exc, len(rows),
        )
        return 0
    finally:
        conn.close()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def open_trades_count_for(
    conn: sqlite3.Connection, account_id: str,
) -> int | None:
    """Count of open trades for ``account_id`` from the live trade journal.

    Returns ``None`` on any SQLite error (the table may not exist on a
    test DB, or the schema may have drifted) so the snapshot lands with
    a NULL ``open_trades_count`` rather than a misleading 0.
    """
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM trades "
            "WHERE account_id = ? AND status = 'open' AND is_backtest = 0",
            (account_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row is not None else 0
    except sqlite3.Error:
        return None


def daily_state_for(
    conn: sqlite3.Connection, account_id: str, *, utc_date: str | None = None,
) -> tuple[float | None, float | None]:
    """``(daily_pnl_realized, daily_equity_high)`` for ``account_id`` from
    `daily_risk_state` — the running totals `RiskManager` already persists.

    Reads only — never writes. Returns ``(None, None)`` when the row is
    absent (a fresh-boot account, or a test DB without the table) so the
    snapshot stores NULLs rather than synthetic zeros.

    The leakage concern in `MB-20260604-003` is that
    ``daily_risk_state`` updates AFTER each closed trade — so a join from
    a closed trade row to the SAME-DAY ``daily_risk_state`` row is
    contaminated. The snapshot path is leak-safe by construction: we
    capture the value as it stood BEFORE this dispatch round mutates it.
    """
    if utc_date is None:
        utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        # The canonical daily_risk_state schema keys the day on `date`
        # (src/units/accounts/risk.py::_CREATE_DAILY_RISK_STATE, PRIMARY KEY
        # (account_id, date)). The prior `utc_date` here matched no production
        # column, so this SELECT raised OperationalError → swallowed → every
        # snapshot stored NULL daily_pnl/daily_equity_high/drawdown_pct. Field
        # beats the stale query: filter on the real `date` column.
        cur = conn.execute(
            "SELECT daily_pnl, daily_high_equity FROM daily_risk_state "
            "WHERE account_id = ? AND date = ?",
            (account_id, utc_date),
        )
        row = cur.fetchone()
        if row is None:
            return (None, None)
        pnl = float(row[0]) if row[0] is not None else None
        peak = float(row[1]) if row[1] is not None else None
        return (pnl, peak)
    except sqlite3.Error:
        return (None, None)


def drawdown_pct(equity: float | None, equity_high: float | None) -> float | None:
    """``(peak - equity) / peak`` — positive = below the peak. ``None`` when
    either input is missing or the peak is non-positive."""
    if equity is None or equity_high is None:
        return None
    if equity_high <= 0:
        return None
    return max(0.0, (float(equity_high) - float(equity)) / float(equity_high))
