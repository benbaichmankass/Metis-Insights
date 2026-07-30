"""IB broker-truth PnL read-back — `exchange_fills_ib.closed_pnl_from_fills`.

The Tier-2 companion to the close-time anchoring change. Anchoring alone stops
IB rows being FABRICATED, but IBKR historical-candle coverage is 0%, so it
converts them into *declared unmeasured* gaps. This read-back is what makes them
MEASURED instead.

What these tests defend is the **refusal** behaviour, because every failure mode
here is a way to silently attribute the wrong money to a trade:

* a qty that doesn't match  -> another trade's fills bled into the window;
* a fill with no realizedPNL -> summing the subset that reported UNDER-counts;
* an unusable row           -> skipping it under-counts just as quietly.

All three must return ``None`` and send the row to the honest fallback, never a
partial record. No network and no ib_insync: the store is a real temp SQLite.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.runtime.exchange_fills_ib import (
    IB_EXIT_SOURCE,
    closed_pnl_from_fills,
)
from src.runtime.provenance import MEASURED, classify

_OPEN_MS = int(datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
_CLOSE_MS = _OPEN_MS + 3_600_000

_SCHEMA = """
CREATE TABLE exchange_fills (
    exec_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, price REAL NOT NULL, qty REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0, fee_currency TEXT, exec_time TEXT NOT NULL,
    order_id TEXT, is_maker INTEGER NOT NULL DEFAULT 0, raw TEXT,
    inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .isoformat().replace("+00:00", "Z")
    )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "fills.sqlite"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    def add(exec_id, side="sell", price=5010.0, qty=1.0, offset_ms=1_800_000,
            realized=125.0, symbol="MES", account="ib_paper"):
        raw = json.dumps({"source": "ib_reqExecutions", "realized_pnl": realized})
        c = sqlite3.connect(str(path))
        c.execute(
            "INSERT INTO exchange_fills (exec_id, account_id, symbol, side, "
            "price, qty, fee, exec_time, raw) VALUES (?,?,?,?,?,?,?,?,?)",
            (exec_id, account, symbol, side, price, qty, 0.5,
             _iso(_OPEN_MS + offset_ms), raw),
        )
        c.commit()
        c.close()

    def factory():
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    store_ns = type("S", (), {})()
    store_ns.add = add
    store_ns.factory = factory
    store_ns.path = path
    return store_ns


def _lookup(store, **kw):
    kw.setdefault("account_id", "ib_paper")
    kw.setdefault("symbol", "MES")
    kw.setdefault("direction", "long")
    kw.setdefault("opened_at_ms", _OPEN_MS)
    kw.setdefault("closed_at_ms", _CLOSE_MS)
    kw.setdefault("qty", 1.0)
    return closed_pnl_from_fills(conn_factory=store.factory, **kw)


# ------------------------------------------------------------------ happy path
def test_single_close_fill_is_measured_broker_truth(store):
    store.add("e1")
    rec = _lookup(store)
    assert rec is not None
    assert rec["avg_exit_price"] == 5010.0
    assert rec["closed_pnl"] == 125.0
    assert rec["qty"] == 1.0
    assert rec["side"] == "sell"
    assert rec["source"] == IB_EXIT_SOURCE


def test_the_source_constant_classifies_as_measured():
    """If these drift, an IB fill would read as unverified — the write-only
    failure mode this whole workstream exists to stop."""
    assert classify(IB_EXIT_SOURCE) == MEASURED


def test_a_short_trade_matches_buy_side_fills(store):
    store.add("e1", side="buy")
    rec = _lookup(store, direction="short")
    assert rec is not None and rec["side"] == "buy"


def test_multiple_partial_fills_are_qty_weighted_and_summed(store):
    store.add("e1", price=5000.0, qty=1.0, offset_ms=1_000_000, realized=50.0)
    store.add("e2", price=5020.0, qty=3.0, offset_ms=1_100_000, realized=150.0)
    rec = _lookup(store, qty=4.0)
    assert rec is not None
    assert rec["closed_pnl"] == 200.0
    assert rec["avg_exit_price"] == pytest.approx((5000 + 3 * 5020) / 4)


def test_entry_price_is_never_invented(store):
    """Close-side executions do not carry the position's entry. Reporting one
    would be a fabrication dressed as broker truth."""
    store.add("e1")
    assert _lookup(store)["avg_entry_price"] is None


def test_closed_at_is_the_last_matched_fill(store):
    store.add("e1", offset_ms=1_000_000)
    store.add("e2", offset_ms=1_200_000, qty=0.0001)
    rec = _lookup(store, qty=1.0)
    assert rec["closed_at"] == _iso(_OPEN_MS + 1_000_000)


# -------------------------------------------------------------------- refusals
def test_no_matching_fill_returns_none(store):
    assert _lookup(store) is None


def test_a_fill_without_realized_pnl_refuses_rather_than_undercounting(store):
    """THE important refusal. Summing only the fills that reported would look
    like a clean number and be quietly too small."""
    store.add("e1", qty=1.0, offset_ms=1_000_000, realized=50.0)
    store.add("e2", qty=1.0, offset_ms=1_100_000, realized=None)
    assert _lookup(store, qty=2.0) is None


def test_qty_mismatch_refuses(store):
    """A close-side fill in the window that is NOT this trade's close — e.g. a
    sibling netted trade — must not have its PnL attributed here."""
    store.add("e1", qty=9.0)
    assert _lookup(store, qty=1.0) is None


def test_qty_within_five_percent_still_matches(store):
    """Mirrors the Bybit reader's tolerance so both refuse alike."""
    store.add("e1", qty=0.97)
    assert _lookup(store, qty=1.0) is not None


def test_an_unusable_row_refuses_instead_of_being_skipped(store):
    store.add("e1", price=0.0, qty=1.0)
    assert _lookup(store, qty=1.0) is None


def test_fills_outside_the_window_are_not_matched(store):
    store.add("e1", offset_ms=10 * 3_600_000)     # long after closed_at
    assert _lookup(store) is None


def test_another_account_or_symbol_is_never_matched(store):
    store.add("e1", account="ib_live")
    store.add("e2", symbol="MGC")
    assert _lookup(store) is None


def test_the_opposite_side_is_never_matched(store):
    """A long's OPEN is a buy; matching it would price the exit at the entry."""
    store.add("e1", side="buy")
    assert _lookup(store, direction="long") is None


@pytest.mark.parametrize("kw", [
    {"direction": "sideways"}, {"direction": ""},
    {"symbol": ""}, {"account_id": ""},
    {"opened_at_ms": "nonsense"},
])
def test_hostile_input_returns_none(store, kw):
    store.add("e1")
    assert _lookup(store, **kw) is None


def test_end_before_start_returns_none(store):
    store.add("e1")
    assert _lookup(store, closed_at_ms=_OPEN_MS - 10_000_000) is None


def test_a_missing_store_returns_none_and_never_raises(tmp_path):
    def _boom():
        raise sqlite3.OperationalError("no such file")

    assert closed_pnl_from_fills(
        account_id="ib_paper", symbol="MES", direction="long",
        opened_at_ms=_OPEN_MS, closed_at_ms=_CLOSE_MS, qty=1.0,
        conn_factory=_boom,
    ) is None


def test_null_qty_target_accepts_whatever_the_window_holds(store):
    """When the journal row has no position_size there is nothing to check
    against — take the window's fills rather than refusing outright."""
    store.add("e1", qty=2.0)
    rec = _lookup(store, qty=None)
    assert rec is not None and rec["qty"] == 2.0
