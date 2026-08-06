"""Account-scoped exchange-fills aggregates (WORKPLAN-2026-08-05 P0.2a).

The pooled `/api/bot/pnl/exchange` aggregate mixes every account's fills, so
its headline realized figure blends real-money crypto with paper equity and is
not attributable to any one book. Measured 2026-08-05 (relay #8496): the pooled
90d total realized read -$10,929, dominated by Alpaca *paper* equity legs — a
number that must never be quoted as a `bybit_2` real-money result.

`account_id` was already a NOT NULL column with an `(account_id, exec_time)`
index, so this is a read-side scope, not a schema or backfill change. These
tests pin BOTH halves of the contract: the scoped read separates the books, and
omitting the arg is byte-identical to the pre-change pooled behaviour.
"""
from __future__ import annotations

import pathlib
import sqlite3
from datetime import datetime, timezone

import pytest

from src.runtime import exchange_fills_store as S

_NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)

# bybit_2 (real money): +9.8 net of fees.  alpaca_paper: -1000.0.
# Opposite signs on purpose — pooling them hides the real-money result.
_ROWS = [
    ("e1", "bybit_2", "BTCUSDT", "buy", 100.0, 1.0, 0.1, "2026-08-01T00:00:00+00:00", "{}"),
    ("e2", "bybit_2", "BTCUSDT", "sell", 110.0, 1.0, 0.1, "2026-08-01T01:00:00+00:00", "{}"),
    ("e3", "alpaca_paper", "SPY", "buy", 500.0, 10.0, 0.0, "2026-08-01T02:00:00+00:00", "{}"),
    ("e4", "alpaca_paper", "SPY", "sell", 400.0, 10.0, 0.0, "2026-08-01T03:00:00+00:00", "{}"),
]


@pytest.fixture()
def fills_db(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "exchange_fills.sqlite"
    conn = sqlite3.connect(str(p))
    conn.executescript(S._SCHEMA)
    conn.executemany(
        "INSERT INTO exchange_fills "
        "(exec_id, account_id, symbol, side, price, qty, fee, exec_time, raw) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        _ROWS,
    )
    conn.commit()
    conn.close()
    return p


def test_fifo_scopes_to_one_account(fills_db: pathlib.Path) -> None:
    """The real-money book is readable on its own, not blended with paper."""
    byb = S.fifo_pnl_by_symbol(30, fills_db, now=_NOW, account_id="bybit_2")
    alp = S.fifo_pnl_by_symbol(30, fills_db, now=_NOW, account_id="alpaca_paper")

    assert [r["symbol"] for r in byb] == ["BTCUSDT"]
    assert [r["symbol"] for r in alp] == ["SPY"]
    assert byb[0]["realized_pnl"] == pytest.approx(9.8)
    assert alp[0]["realized_pnl"] == pytest.approx(-1000.0)


def test_pooled_read_is_unchanged_when_account_id_omitted(fills_db: pathlib.Path) -> None:
    """Omitting the arg keeps the pre-change behaviour — additive, not a break."""
    pooled = S.fifo_pnl_by_symbol(30, fills_db, now=_NOW)
    assert {r["symbol"] for r in pooled} == {"BTCUSDT", "SPY"}
    # And it is exactly the sum of the parts — the blend this endpoint used to
    # present as one headline.
    total = sum(r["realized_pnl"] for r in pooled)
    assert total == pytest.approx(9.8 - 1000.0)


def test_summary_and_by_symbol_scope_too(fills_db: pathlib.Path) -> None:
    """All three aggregates take the scope, not just the FIFO one."""
    assert S.aggregate_summary(30, fills_db, now=_NOW)["fill_count"] == 4
    scoped = S.aggregate_summary(30, fills_db, now=_NOW, account_id="bybit_2")
    assert scoped["fill_count"] == 2
    assert scoped["symbol_count"] == 1

    rows = S.aggregate_by_symbol(30, fills_db, now=_NOW, account_id="bybit_2")
    assert [r["symbol"] for r in rows] == ["BTCUSDT"]


def test_unknown_account_returns_empty_not_pooled(fills_db: pathlib.Path) -> None:
    """A typo'd account must read empty, never silently fall back to pooled.

    This is the failure mode that would re-introduce the original defect: an
    unrecognised scope quietly returning everything looks like a clean answer.
    """
    assert S.fifo_pnl_by_symbol(30, fills_db, now=_NOW, account_id="nope") == []
    assert S.aggregate_by_symbol(30, fills_db, now=_NOW, account_id="nope") == []
    assert S.aggregate_summary(30, fills_db, now=_NOW, account_id="nope")["fill_count"] == 0


def test_account_filter_binds_never_interpolates() -> None:
    """The scope is a bound parameter — a quote in the value cannot reach SQL."""
    sql, params = S._account_filter("a'; DROP TABLE exchange_fills; --")
    assert sql == " AND account_id = ?"
    assert params == ("a'; DROP TABLE exchange_fills; --",)
    assert S._account_filter(None) == ("", ())
    assert S._account_filter("") == ("", ())
