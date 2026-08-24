"""`_sweep_local_pnl_for_unpriced` must never price a CONFIRMED CLOSE from a mark.

The Tier-2 remedy, tested at the level that matters: not "does the anchor module
work" (that's `test_exit_anchor`) but **"can this sweep still fabricate?"**

The regression being locked out: the sweep substituted `last_mark_price(symbol)`
— the market at SWEEP time, hours after the close — and booked `pnl` from it.
Matched-pair proof from the live journal: trade 4180 (real) −$4.00 vs its mirror
4181 −$2,589.78, same strategy/symbol/bracket/minute.

Verified structurally *and* behaviourally, because a behavioural test alone
could pass while a stray `last_mark_price` call remains on some other branch.
"""
from __future__ import annotations

import inspect
import json
import sqlite3

import pytest

from src.runtime import order_monitor as om
from src.runtime.provenance import (
    ESTIMATED, FABRICATED, UNMEASURED_MARKER, classify_pnl,
)

# NOTE on `exit_reason` below: it is NOT optional. Production `trades` has it
# and the sweep SELECTs it (it gates the exit-LABEL relabel on the row still
# carrying the generic reason). A fixture MISSING a production column does not
# fail loudly -- sqlite raises `no such column`, the sweep's broad `except`
# swallows it into `scan query failed`, and every behavioural test in this file
# then reports `assert 0 == 1` as if the production code were broken. That is
# the `order_packages.id` class (BL-20260810, a fixture declaring a schema
# production does NOT have) in its mirror image: a fixture missing a column
# production DOES have. Kept as a Python comment, not an SQL one, because
# `test-schema-fidelity-guard` tokenises the DDL body and reads prose words as
# column names. BL-20260823-FIXTURE-MISSING-A-PRODUCTION-COLUMN-FAILS-SILENTLY.
_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    account_id TEXT, symbol TEXT, direction TEXT,
    position_size REAL, entry_price REAL, exit_price REAL,
    pnl REAL, pnl_percent REAL, status TEXT,
    is_backtest INTEGER DEFAULT 0, setup_type TEXT,
    order_package_id TEXT, closed_at TEXT, created_at TEXT,
    exit_reason TEXT,
    timestamp TEXT, notes TEXT
);
"""


class _DB:
    """Minimal journal double with the two methods the sweep uses."""

    def __init__(self, path):
        self.path = str(path)
        self.updates = {}

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def update_trade(self, tid, updates):
        self.updates[int(tid)] = updates
        sets = ", ".join(f"{k} = ?" for k in updates)
        c = sqlite3.connect(self.path)
        c.execute(f"UPDATE trades SET {sets} WHERE id = ?",
                  (*updates.values(), int(tid)))
        c.commit()
        c.close()


# --- fixture clock -----------------------------------------------------------
#
# TIME BOMB, DEFUSED 2026-08-13. These dates were hardcoded as
# '2026-07-30T11:00:00Z'. The sweep's scan query bounds itself with
#
#     AND datetime(created_at) >= datetime('now', '-14 days')
#
# so the fixture row silently aged OUT of the window exactly 14 days later, at
# 2026-08-13T11:00:00Z, and 7 tests in this file went red without a single line
# of the code or the tests changing. The CI runs bracket the boundary to the
# minute: the last green full suite finished 10:38:48Z, the first red one
# 11:09:03Z, same day. Nothing in either diff was related.
#
# That is the SECOND time bomb to redden main in six days (#8778 fixed one in
# tests/test_exchange_fills_list_rows.py, where `list_fills` measured its cutoff
# from `datetime.now()`). That fix injected a fixed clock into the function
# under test, which is the better pattern where it is available — here the
# window is applied by SQLite inside the query as `datetime('now', ...)`, so
# there is no clock to inject without changing production SQL for the test's
# convenience.
#
# So the dates are RELATIVE instead. The property the tests need is not a
# particular calendar date, it is "this row is comfortably inside the sweep's
# retention window", and that is now true whenever the suite runs.
_ROW_AGE_DAYS = 2          # inside the 14-day scan window, and older than the
                           # 6h broker-reader defer, on any day it runs
_HOLD_SECONDS = 3630       # created -> closed, preserving the original 1h0m30s


def _fixture_times():
    from datetime import datetime, timedelta, timezone
    created = datetime.now(timezone.utc) - timedelta(days=_ROW_AGE_DAYS)
    closed = created + timedelta(seconds=_HOLD_SECONDS)
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return fmt(created), fmt(closed)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "j.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    created_at, closed_at = _fixture_times()
    conn.execute(
        "INSERT INTO trades (id, account_id, symbol, direction, position_size, "
        "entry_price, exit_price, pnl, status, is_backtest, closed_at, "
        "created_at, timestamp, notes) VALUES "
        "(1,'ib_paper','MES','long',1.0,5000.0,NULL,NULL,'closed',0,?,?,?,'{}')",
        (closed_at, created_at, created_at),
    )
    conn.commit()
    conn.close()
    # No account configs -> no broker-reader deferral, no options deferral.
    monkeypatch.setattr(om, "_load_account_cfgs_for_reconcile", lambda: {})
    return _DB(path)


def _row(db_, tid=1):
    c = sqlite3.connect(db_.path)
    c.row_factory = sqlite3.Row
    r = dict(c.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone())
    c.close()
    return r


# --------------------------------------------------------------- structural
def _code_only(fn) -> str:
    """Source with comment lines stripped.

    The first version of this test matched the explanatory COMMENT that names
    `last_mark_price` while describing why it was removed — the same
    prose-vs-code confusion that made the json-extract guard cry wolf on its own
    docstring. Check what executes, not what is written about it.
    """
    return "\n".join(
        line for line in inspect.getsource(fn).splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_sweep_no_longer_calls_last_mark_price():
    """A stray call on any branch would reintroduce the fabrication silently."""
    assert "last_mark_price" not in _code_only(om._sweep_local_pnl_for_unpriced), (
        "the mark-price substitution is back in the sweep — that is the exact "
        "fabrication this change removed"
    )


def test_the_sweep_uses_the_close_time_anchor():
    src = _code_only(om._sweep_local_pnl_for_unpriced)
    assert "bar_close_at" in src
    assert "UNMEASURED_MARKER" in src


# -------------------------------------------------------------- behavioural
def test_no_anchor_declares_unmeasured_instead_of_pricing(db, monkeypatch):
    """IBKR historical coverage is 0%, so an `ib_paper` close hits this path.
    The row must be DECLARED, not priced — and `pnl` must stay NULL."""
    monkeypatch.setattr(om, "bar_close_at", lambda *a, **k: (None, "no_anchor"),
                        raising=False)
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (None, "no_anchor"))

    summary = om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)

    assert row["pnl"] is None, "a row with no anchor must NOT be priced"
    notes = json.loads(row["notes"])
    assert notes["pnl_source"] == UNMEASURED_MARKER
    assert notes["unmeasured_reason"] == "no_close_time_anchor"
    assert summary["declared_unmeasured"] == 1


def test_declaring_is_idempotent(db, monkeypatch):
    """A second sweep must not re-write or double-count an already-declared row."""
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (None, "no_anchor"))
    om._sweep_local_pnl_for_unpriced(db)
    second = om._sweep_local_pnl_for_unpriced(db)
    assert second["declared_unmeasured"] == 0
    assert second["already_unmeasured"] == 1


def test_deferred_neither_prices_nor_declares(db, monkeypatch):
    """Budget spent / transient failure: we did not look, so we may not declare."""
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (None, "deferred"))
    summary = om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)
    assert row["pnl"] is None
    assert json.loads(row["notes"]).get("pnl_source") != UNMEASURED_MARKER
    assert summary["still_pending"] == 1
    assert summary["declared_unmeasured"] == 0


def test_anchored_prices_and_stamps_estimated(db, monkeypatch):
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (5010.0, "anchored"))
    summary = om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)

    assert summary["filled"] == 1
    assert row["pnl"] is not None
    assert row["exit_price"] == 5010.0
    notes = json.loads(row["notes"])
    assert notes["exit_price_source"] == "candle_at_close"
    # And it classifies as ESTIMATED — better than a mark, still NOT a fill.
    bucket, _ = classify_pnl(row)
    assert bucket == ESTIMATED
    assert bucket != FABRICATED


def test_a_recorded_fill_is_still_preferred(db, monkeypatch):
    """Broker truth must win over the anchor — the anchor is a fallback only."""
    c = sqlite3.connect(db.path)
    c.execute("UPDATE trades SET exit_price = 5020.0, "
              "notes = '{\"exit_price_source\": \"bybit_closed_pnl\"}' WHERE id = 1")
    c.commit()
    c.close()

    called = []

    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at",
                        lambda *a, **k: (called.append(1), (9999.0, "anchored"))[1])
    om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)
    assert row["exit_price"] == 5020.0
    assert not called, "the anchor must not be consulted when a fill is recorded"
    # 2026-08-24: this asserted `recorded_exit_price`, i.e. it PINNED THE BUG.
    # The row already carried the broker's own stamp (`bybit_closed_pnl`) and the
    # sweep overwrote it with the weaker-provenance-but-stronger-sounding
    # `recorded_exit_price` — that unconditional overwrite is exactly how a
    # projection got laundered into a fill
    # (BL-20260824-RECORDED-EXIT-PRICE-OUTNUMBERS-ALL-BROKER-TRUTH-COMBINED).
    # The test name — "a recorded fill is still preferred" — was already right;
    # only the assertion disagreed with it. Broker truth is now PRESERVED.
    assert json.loads(row["notes"])["exit_price_source"] == "bybit_closed_pnl"


def test_anchoring_a_previously_declared_row_clears_the_marker(db, monkeypatch):
    """Otherwise INV-2b would keep counting a row that IS now measured."""
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (None, "no_anchor"))
    om._sweep_local_pnl_for_unpriced(db)
    assert json.loads(_row(db)["notes"])["pnl_source"] == UNMEASURED_MARKER

    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (5010.0, "anchored"))
    om._sweep_local_pnl_for_unpriced(db)
    notes = json.loads(_row(db)["notes"])
    assert notes["pnl_source"] == "local_compute"
    assert "unmeasured_reason" not in notes


def test_sweep_never_raises_on_a_broken_anchor(db, monkeypatch):
    import src.runtime.exit_anchor as EA

    def _boom(*a, **k):
        raise RuntimeError("network on fire")

    monkeypatch.setattr(EA, "bar_close_at", _boom)
    summary = om._sweep_local_pnl_for_unpriced(db)   # must not raise
    assert summary["errors"] >= 1
    assert _row(db)["pnl"] is None


# ------------------------------------------------------------------- budget
def test_fetch_budget_knob_defaults_to_three(monkeypatch):
    monkeypatch.delenv("EXIT_ANCHOR_FETCHES_PER_TICK", raising=False)
    assert om._exit_anchor_fetches_per_tick() == 3


def test_fetch_budget_knob_is_a_tuning_knob_not_a_gate(monkeypatch):
    """`0` pauses the NETWORK path (rows defer) — it must never re-enable
    fabrication, and a garbage value must fall back rather than crash the tick."""
    monkeypatch.setenv("EXIT_ANCHOR_FETCHES_PER_TICK", "0")
    assert om._exit_anchor_fetches_per_tick() == 0
    monkeypatch.setenv("EXIT_ANCHOR_FETCHES_PER_TICK", "-5")
    assert om._exit_anchor_fetches_per_tick() == 0
    monkeypatch.setenv("EXIT_ANCHOR_FETCHES_PER_TICK", "banana")
    assert om._exit_anchor_fetches_per_tick() == 3


def test_fixture_row_is_inside_the_sweep_scan_window(db):
    """THE BOMB-DEFUSAL REGRESSION — asserts the precondition every other test
    in this file silently depends on.

    All seven behavioural tests here assert on `summary["filled"]`/`declared`.
    When the fixture aged out of the sweep's 14-day window they did not report
    "the row was not scanned" — they reported `assert 0 == 1` and
    `KeyError: 'pnl_source'`, which read as a defect in the exit-anchor code.
    A whole session can be spent bisecting production code that never changed.

    This asserts `scanned` directly, so the next time the window and the fixture
    disagree the failure NAMES that, instead of being discovered by elimination.
    """
    summary = om._sweep_local_pnl_for_unpriced(db)
    # DISTINGUISH THE TWO CAUSES OF `scanned == 0`. This test used to name only
    # the window, so when the sweep's SELECT gained a column the fixture lacked
    # it confidently reported "the fixture clock has drifted" and sent the reader
    # to inspect a clock that was fine. A failing scan query is not a stale
    # window, and the swallowed `no such column` makes them look identical.
    _cols = {r[1] for r in db.connect().execute("PRAGMA table_info(trades)")}
    _need = {"exit_reason", "setup_type", "notes", "created_at", "position_size"}
    assert _need <= _cols, (
        "the fixture's `trades` table is MISSING column(s) the sweep SELECTs: "
        f"{sorted(_need - _cols)}. The sweep is not broken — the fixture has "
        "drifted from production DDL, sqlite's `no such column` is swallowed "
        "into `scan query failed`, and every other test in this file then fails "
        "as `assert 0 == 1`"
    )
    assert summary["scanned"] == 1, (
        "the fixture row fell outside the sweep's `datetime('now','-14 days')` "
        "scan window — the fixture clock has drifted, the sweep is not broken"
    )


def test_the_scan_window_check_can_fail(db):
    """The can-fail companion to the test above — a guard that cannot fail
    proves nothing.

    `test_fixture_row_is_inside_the_sweep_scan_window` is the important one, but
    on its own it would pass just as happily if `scanned` were hardcoded to 1,
    if the sweep silently stopped filtering by date, or if this file's fixture
    clock were later "simplified" back to a literal that happens to be inside
    the window on the day someone edits it. Age the row out ON PURPOSE and
    confirm the scan really does come back empty.

    It also reproduces the 2026-08-13 outage on demand, and pins the property
    that made it so expensive to diagnose: an aged-out row is SILENT, not loud.
    `scanned` and `declared_unmeasured` both read 0 — byte-identical to a
    correctly-behaving sweep over a clean book — which is why seven tests
    reported `assert 0 == 1` and `KeyError` instead of "the row was not
    scanned".
    """
    from datetime import datetime, timedelta, timezone  # local, as in _fixture_times

    c = sqlite3.connect(db.path)
    c.execute(
        "UPDATE trades SET created_at = ? WHERE id = 1",
        ((datetime.now(timezone.utc) - timedelta(days=_ROW_AGE_DAYS + 13))
         .strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    c.commit()
    c.close()

    summary = om._sweep_local_pnl_for_unpriced(db)
    assert summary["scanned"] == 0, (
        "a row 15 days old was still scanned — the 14-day window is not being "
        "applied, so the test above cannot detect a fixture that drifts out of it"
    )
    # The silence that made this expensive: nothing distinguishes it from clean.
    assert summary["declared_unmeasured"] == 0
    assert json.loads(_row(db)["notes"]).get("pnl_source") is None
