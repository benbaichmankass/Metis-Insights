"""Tests for scripts/ops/close_stranded_journal_row.py — the guarded close of a
stranded open journal row whose broker position is already flat.

The broker read (_live_position) + the account loader are monkeypatched; the DB
is a real temp sqlite so the UPDATE path is exercised end-to-end. These verify
the safety gates (unreadable → abort, position-still-open → refuse, non-Alpaca →
abort), the dry-run/apply split, pnl computation, and idempotency.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "close_stranded_journal_row",
    Path(__file__).resolve().parents[1] / "scripts" / "ops" / "close_stranded_journal_row.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore


_ALPACA_ACCT = {"account_id": "alpaca_live", "exchange": "alpaca",
                "alpaca_env": "live", "api_key_env": "ALPACA_API_KEY_ID_LIVE"}


@pytest.fixture(autouse=True)
def _patch_account(monkeypatch):
    monkeypatch.setattr(mod, "_load_account",
                        lambda aid: _ALPACA_ACCT if aid == "alpaca_live" else None)


def _patch_live(monkeypatch, value):
    monkeypatch.setattr(mod, "_live_position", lambda cfg, symbol: value)


@pytest.fixture
def db(tmp_path):
    """A trade_journal.db with one open IEF row on alpaca_live."""
    p = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(p)
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,
            entry_price REAL, exit_price REAL, position_size REAL,
            status TEXT, exit_reason TEXT, pnl REAL, pnl_percent REAL,
            is_backtest INTEGER, strategy_name TEXT, account_id TEXT,
            created_at TEXT, timestamp TEXT, closed_at TEXT, notes TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO trades (id, symbol, direction, entry_price, position_size, "
        "status, is_backtest, strategy_name, account_id, notes) "
        "VALUES (1, 'IEF', 'long', 94.1078, 1.0, 'open', 0, 'bond_carry', "
        "'alpaca_live', '{}')"
    )
    conn.commit()
    conn.close()
    return str(p)


def _row(db_path, tid=1):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
    conn.close()
    return r


def test_unknown_account_aborts(db):
    r = mod.close_stranded("nope", "IEF", apply=True, exit_price=None,
                           reason="x", db_path=db)
    assert r["ok"] is False and "not found" in r["detail"]


def test_non_alpaca_refused(monkeypatch, db):
    monkeypatch.setattr(mod, "_load_account",
                        lambda aid: {"account_id": "bybit_2", "exchange": "bybit"})
    r = mod.close_stranded("bybit_2", "BTCUSDT", apply=True, exit_price=None,
                           reason="x", db_path=db)
    assert r["ok"] is False and "Alpaca" in r["detail"]


def test_unreadable_aborts(monkeypatch, db):
    _patch_live(monkeypatch, None)  # could-not-read
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=None,
                           reason="x", db_path=db)
    assert r["ok"] is False and r["action"] == "abort_unreadable"
    # row must remain open — the gate never wrote
    assert _row(db)["status"] == "open"


def test_refuses_when_position_still_open(monkeypatch, db):
    _patch_live(monkeypatch, {"symbol": "IEF", "side": "long", "size": 1.0,
                              "entry_price": 94.1, "unrealised_pnl": -0.4})
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=None,
                           reason="x", db_path=db)
    assert r["ok"] is False and r["action"] == "refused_position_open"
    assert _row(db)["status"] == "open"  # never closed a row for a live position


def test_noop_when_flat_and_no_open_row(monkeypatch, db):
    # close the row out from under it, then a flat broker read finds nothing to do
    conn = sqlite3.connect(db)
    conn.execute("UPDATE trades SET status = 'closed' WHERE id = 1")
    conn.commit()
    conn.close()
    _patch_live(monkeypatch, {})  # flat
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=None,
                           reason="x", db_path=db)
    assert r["ok"] is True and r["action"] == "noop_no_open_row"


def test_dry_run_previews_without_writing(monkeypatch, db):
    _patch_live(monkeypatch, {})  # flat
    r = mod.close_stranded("alpaca_live", "IEF", apply=False, exit_price=93.665,
                           reason="operator_flatten_reconciled", db_path=db)
    assert r["ok"] is True and r["action"] == "dry_run"
    assert r["broker_flat_confirmed"] is True
    assert len(r["rows"]) == 1
    assert _row(db)["status"] == "open"  # dry-run wrote nothing


def test_apply_closes_row_with_pnl(monkeypatch, db):
    _patch_live(monkeypatch, {})  # flat
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.665,
                           reason="operator_flatten_reconciled", db_path=db)
    assert r["ok"] is True and r["action"] == "closed" and r["rows_closed"] == 1
    row = _row(db)
    assert row["status"] == "closed"
    assert row["exit_reason"] == "operator_flatten_reconciled"
    assert abs(row["exit_price"] - 93.665) < 1e-6
    # long: pnl = (exit - entry) * size = (93.665 - 94.1078) * 1 = -0.4428
    assert abs(row["pnl"] - (-0.4428)) < 1e-3
    assert row["closed_at"] and row["closed_at"].isdigit()  # epoch-ms string
    notes = json.loads(row["notes"])
    assert notes["broker_flat_confirmed"] is True
    assert notes["pnl_source"] == "local_compute"
    assert notes["exit_price_source"] == "operator_flatten_fill"


def test_apply_without_exit_price_falls_back_to_entry(monkeypatch, db):
    _patch_live(monkeypatch, {})  # flat
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=None,
                           reason="operator_flatten_reconciled", db_path=db)
    assert r["ok"] is True and r["action"] == "closed"
    row = _row(db)
    assert abs(row["exit_price"] - 94.1078) < 1e-6  # entry fallback
    assert abs(row["pnl"] - 0.0) < 1e-9            # pnl 0 at entry
    assert json.loads(row["notes"])["exit_price_source"] == "entry_fallback_no_fill"


def test_apply_is_idempotent(monkeypatch, db):
    _patch_live(monkeypatch, {})  # flat
    first = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.665,
                               reason="operator_flatten_reconciled", db_path=db)
    assert first["rows_closed"] == 1
    second = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.665,
                                reason="operator_flatten_reconciled", db_path=db)
    # row is already closed → no open row remains → clean noop
    assert second["ok"] is True and second["action"] == "noop_no_open_row"


# ── row + exit_kind (PAPER-RESET, 2026-09-30) ─────────────────────────────────
# A netted symbol held one row whose own stop filled at the venue while a
# sibling row kept the symbol non-flat (alpaca_paper SPY 6131 vs 4347). The two
# must close differently: the filled row as a strategy `sl` at its venue fill,
# the sibling as an operator close. These pin that the row filter isolates one
# row and that exit_kind cannot be used without a row and a price.

def _add_sibling(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO trades (id, symbol, direction, entry_price, position_size, "
        "status, is_backtest, strategy_name, account_id, notes) "
        "VALUES (2, 'IEF', 'long', 95.0, 2.0, 'open', 0, 'other_leg', "
        "'alpaca_live', '{}')"
    )
    conn.commit()
    conn.close()


def test_row_with_exit_kind_closes_only_that_row_as_venue_fill(monkeypatch, db):
    _add_sibling(db)
    _patch_live(monkeypatch, {})  # flat
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.0,
                           reason="operator_flatten_reconciled", db_path=db,
                           row_id=2, exit_kind="sl")
    assert r["ok"] is True and r["rows_closed"] == 1
    closed, sibling = _row(db, 2), _row(db, 1)
    assert closed["status"] == "closed" and closed["exit_reason"] == "sl"
    assert abs(closed["pnl"] - (93.0 - 95.0) * 2.0) < 1e-9
    notes = json.loads(closed["notes"])
    assert notes["exit_price_source"] == "exchange_fill"
    assert notes["closed_by_venue_leg"] == "sl"
    assert sibling["status"] == "open"  # the other row is untouched


def test_row_not_open_is_refused_and_writes_nothing(monkeypatch, db):
    _patch_live(monkeypatch, {})
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=None,
                           reason="operator_flatten_reconciled", db_path=db, row_id=99)
    assert r["ok"] is False and r["action"] == "refused_row_not_open"
    assert _row(db)["status"] == "open"


@pytest.mark.parametrize("kw", [
    {"row_id": None, "exit_price": 93.0, "exit_kind": "sl"},   # no row
    {"row_id": 1, "exit_price": None, "exit_kind": "sl"},      # no venue price
    {"row_id": 1, "exit_price": 93.0, "exit_kind": "trail"},   # not in vocabulary
])
def test_exit_kind_needs_row_and_price(monkeypatch, db, kw):
    _patch_live(monkeypatch, {})
    r = mod.close_stranded("alpaca_live", "IEF", apply=True,
                           reason="operator_flatten_reconciled", db_path=db, **kw)
    assert r["ok"] is False and r["action"] == "refused_exit_kind"
    assert _row(db)["status"] == "open"


def test_exit_kind_still_requires_broker_flat(monkeypatch, db):
    _patch_live(monkeypatch, {"side": "long", "size": 1.0})
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.0,
                           reason="operator_flatten_reconciled", db_path=db,
                           row_id=1, exit_kind="sl")
    assert r["action"] == "refused_position_open"
    assert _row(db)["status"] == "open"


# ── netted row close while the symbol is still held (2026-09-30) ─────────────
# The operator kept alpaca_paper instead of resetting it, so SPY never goes
# flat while row 4347 holds 11 sh. Row 6131 (stopped out at the venue) may close
# only when the OTHER open rows exactly account for the live broker position.

def test_netted_row_closes_when_siblings_account_for_position(monkeypatch, db):
    _add_sibling(db)  # row 2: IEF long 2.0, sibling of row 1 (long 1.0)
    _patch_live(monkeypatch, {"side": "long", "size": 1.0})  # = row 1 alone
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.0,
                           reason="operator_flatten_reconciled", db_path=db,
                           row_id=2, exit_kind="sl")
    assert r["ok"] is True and r["rows_closed"] == 1
    assert r["broker_position_accounted_by_siblings"] is True
    assert _row(db, 2)["status"] == "closed" and _row(db, 2)["exit_reason"] == "sl"
    assert _row(db, 1)["status"] == "open"


@pytest.mark.parametrize("live", [
    {"side": "long", "size": 3.0},   # broker holds more than the siblings
    {"side": "long", "size": 0.5},   # broker holds less
    {"side": "short", "size": 1.0},  # side mismatch
])
def test_netted_row_refused_unless_exactly_accounted(monkeypatch, db, live):
    _add_sibling(db)
    _patch_live(monkeypatch, live)
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.0,
                           reason="operator_flatten_reconciled", db_path=db,
                           row_id=2, exit_kind="sl")
    assert r["action"] == "refused_position_open"
    assert _row(db, 2)["status"] == "open"


def test_netted_exception_needs_exit_kind(monkeypatch, db):
    _add_sibling(db)
    _patch_live(monkeypatch, {"side": "long", "size": 1.0})
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=None,
                           reason="operator_flatten_reconciled", db_path=db, row_id=2)
    assert r["action"] == "refused_position_open"
    assert _row(db, 2)["status"] == "open"


def test_netted_exception_refused_with_no_sibling(monkeypatch, db):
    _patch_live(monkeypatch, {"side": "long", "size": 1.0})
    r = mod.close_stranded("alpaca_live", "IEF", apply=True, exit_price=93.0,
                           reason="operator_flatten_reconciled", db_path=db,
                           row_id=1, exit_kind="sl")
    assert r["action"] == "refused_position_open"
    assert _row(db, 1)["status"] == "open"
