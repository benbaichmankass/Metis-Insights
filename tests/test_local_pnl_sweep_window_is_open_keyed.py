"""`_sweep_local_pnl_for_unpriced` bounds its scan by the OPEN, not the CLOSE —
so a position held longer than the window can never be priced.

MI-127 (A). The reproduction for the mechanism behind the pnl-NULL closes that
`MI-124` attributed to luck.

THE DEFECT, IN ONE LINE
-----------------------
The sweep's scan query bounds itself with::

    AND datetime(created_at) >= datetime('now', '-14 days')

`created_at` is when the position was OPENED. The thing the sweep exists to
price is the CLOSE. For any position held longer than 14 days the row is
ALREADY outside the window at the moment it closes, so the sweep never selects
it, never attempts an anchor, and never reaches the
`UNMEASURED_MARKER` declaration branch either.

The result is the one state `src/runtime/provenance.py` is built to prevent: a
**silent** NULL. Not "we asked and could not measure it" (`pnl_source:
unmeasured`, which INV-2 accepts and INV-2b counts) but no provenance key at
all — indistinguishable, to every downstream consumer, from a row nobody ever
looked at. Because that is exactly what it is.

MEASURED ON THE LIVE JOURNAL (population stated)
------------------------------------------------
`trade_journal.db::trades`, all 5,493 rows, read 2026-09-05 against trader
`git_sha 5eb59917`; non-backtest `status='closed'`, `intent_reduce` legs
excluded, hold duration computable → **1,373 rows**.

    hold bucket      n     pnl NULL    null %
    < 1 day        1103          22      2.0%
    1-7 d           196           3      1.5%
    7-14 d           47          12     25.5%
    > 14 days        22          20     90.9%

And the split that names the mechanism, over the 57 pnl-NULL rows in that
population:

                      declared unmeasured    SILENT (no pnl_source)
    held <= 14 d                       15                        22
    held  > 14 d                        0                        20

**Not one row held longer than the window has ever been declared** — 0 of 20.
Not because the anchor failed, but because the sweep never asked. The two rows
held >14d that DO carry a pnl were filled by other paths entirely
(`pairs_half_open_cleanup`, `bybit_closed_pnl`), never by this sweep.

WHY THIS WAS INVISIBLE
----------------------
The same 14-day window is already documented one file over, in
`tests/test_sweep_no_mark_fabrication.py`, as a "TIME BOMB, DEFUSED
2026-08-13" — twice it aged a fixture out of the window and reddened main.
Both times it was read as a *test* problem and the fixture was made relative.
Nobody asked what the same predicate does to a production row that is
long-lived by design rather than by accident.

THE PROPOSED FIX (NOT APPLIED HERE — `src/**` is outside `TIER1_SURFACE`)
-------------------------------------------------------------------------
Key the window on the close, falling back to the open for rows that have no
`closed_at` (the `orphaned` rows this sweep also selects)::

    -    AND datetime(created_at) >= datetime('now', '-14 days')
    +    AND datetime(COALESCE(closed_at, created_at)) >= datetime('now', '-14 days')

⚠️ Deploying that will make recently-closed long-held rows eligible on the next
tick, and the sweep will price them from a close-time anchor. That is a
BACK-FILL and it is the operator's decision, not this unit's — see the
diagnosis for the five rows it would reach and what evidence each value would
rest on.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import order_monitor as om

# See the NOTE in tests/test_sweep_no_mark_fabrication.py: a fixture MISSING a
# production column does not fail loudly — sqlite raises `no such column`, the
# sweep's broad `except` swallows it into `scan query failed`, and every
# behavioural assertion below then reports a false negative.
# BL-20260823-FIXTURE-MISSING-A-PRODUCTION-COLUMN-FAILS-SILENTLY.
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

# Both rows CLOSE at the same recent moment. The ONLY thing that differs is how
# long each was held — which is the whole point: hold duration must not decide
# whether a close is priceable.
_CLOSED_DAYS_AGO = 2
_SHORT_HOLD_DAYS = 3     # a scalp/intraday shape — inside the window either way
_LONG_HOLD_DAYS = 38     # a `_1d` trend/pullback leg — the real fleet shape

_SHORT_ID, _LONG_ID = 1, 2


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


def _fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Two closed, unpriced rows differing ONLY in hold duration."""
    path = tmp_path / "j.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)

    closed = datetime.now(timezone.utc) - timedelta(days=_CLOSED_DAYS_AGO)
    for tid, held in ((_SHORT_ID, _SHORT_HOLD_DAYS), (_LONG_ID, _LONG_HOLD_DAYS)):
        created = closed - timedelta(days=held)
        conn.execute(
            "INSERT INTO trades (id, account_id, symbol, direction, "
            "position_size, entry_price, exit_price, pnl, status, is_backtest, "
            "exit_reason, closed_at, created_at, timestamp, notes) VALUES "
            "(?,'alpaca_paper','SPY','long',10.0,600.0,NULL,NULL,'closed',0,"
            "'exchange_flat_reconciled',?,?,?,'{}')",
            (tid, _fmt(closed), _fmt(created), _fmt(created)),
        )
    conn.commit()
    conn.close()
    # No account configs -> no broker-reader deferral, no options deferral.
    monkeypatch.setattr(om, "_load_account_cfgs_for_reconcile", lambda: {})
    return _DB(path)


@pytest.fixture
def anchored(monkeypatch):
    """A WORKING close-time anchor, so nothing below can be blamed on the venue.

    This is the control the finding needs: with pricing demonstrably available,
    any row that still ends NULL was never offered to the anchor at all.
    """
    # collapsed-state: anchored — this fixture returns ONLY `anchored` on
    # purpose. It is a positive control whose job is to hold pricing constant so
    # that hold duration is the single variable; branching it would reintroduce
    # the confound. The other two states of `bar_close_at` are NOT collapsed
    # away, they are covered where they belong, in
    # tests/test_sweep_no_mark_fabrication.py:
    #   `no_anchor` -> test_no_anchor_declares_unmeasured_instead_of_pricing
    #                  and test_declaring_is_idempotent
    #   `deferred`  -> test_deferred_neither_prices_nor_declares
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (610.0, "anchored"))


def _row(db_, tid):
    c = sqlite3.connect(db_.path)
    c.row_factory = sqlite3.Row
    r = dict(c.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone())
    c.close()
    return r


# ------------------------------------------------------------------ control
def test_short_hold_row_is_priced(db, anchored):
    """POSITIVE CONTROL. The anchor works and the sweep uses it — so a negative
    below is a real negative and not a broken fixture."""
    summary = om._sweep_local_pnl_for_unpriced(db)
    row = _row(db, _SHORT_ID)

    assert summary["filled"] >= 1
    assert row["pnl"] is not None, (
        "the positive control did not price — every other assertion in this "
        "file is meaningless until this one passes"
    )


# ------------------------------------------------------- the defect, as-built
def test_long_hold_row_is_never_even_scanned(db, anchored):
    """THE REPRODUCTION. Same close, same symbol, same working anchor — the
    only difference is that the position was held 38 days instead of 3.

    ⚠️ This test asserts the defect AS IT CURRENTLY BEHAVES, so it is green on
    `main`. When the window is re-keyed to the close it will go red, and that
    is the intended signal to delete it along with the xfail below.
    """
    summary = om._sweep_local_pnl_for_unpriced(db)

    assert summary["scanned"] == 1, (
        "expected the long-held row to be excluded by the created_at window; "
        f"scanned={summary['scanned']}"
    )

    row = _row(db, _LONG_ID)
    assert row["pnl"] is None

    # And the part that makes it a provenance defect rather than a coverage
    # gap: the row carries NO declaration. It is silent, not honest.
    notes = json.loads(row["notes"] or "{}")
    assert "pnl_source" not in notes, (
        "a row the sweep never scanned must not carry a provenance stamp"
    )
    assert summary["declared_unmeasured"] == 0, (
        "the row was never offered to the anchor, so it cannot have been "
        "declared unmeasured — if this fires, the mechanism has changed"
    )


def test_the_window_predicate_keys_on_the_open(db):
    """Structural pin, so the SQL and the behavioural tests move together.

    Checked against the executing source rather than the docstring — the
    prose-vs-code confusion `test_sweep_no_mark_fabrication` had to correct.
    """
    import inspect
    src = "\n".join(
        line for line in
        inspect.getsource(om._sweep_local_pnl_for_unpriced).splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "datetime(created_at) >= datetime('now', '-14 days')" in src, (
        "the scan window changed — re-read MI-127 (A) and update the "
        "behavioural expectations in this file together with it"
    )


# ------------------------------------------------- the fix's acceptance test
@pytest.mark.xfail(
    strict=True,
    reason=(
        "MI-127 (A): the sweep's scan window keys on created_at (the OPEN) "
        "instead of the close, so a position held longer than 14 days can "
        "never be priced. Proposed fix: "
        "datetime(COALESCE(closed_at, created_at)) >= datetime('now','-14 days'). "
        "strict=True on purpose — when the fix lands this XPASSes and fails "
        "the build, which is the prompt to delete both this marker and "
        "test_long_hold_row_is_never_even_scanned above."
    ),
)
def test_a_recent_close_is_priceable_regardless_of_hold_duration(db, anchored):
    """THE ACCEPTANCE CRITERION for the proposed fix.

    A close is a close. How long the position was held before it is a property
    of the STRATEGY, not of whether its realised PnL can be computed — and
    every `_1d` trend and pullback leg in the fleet is long-horizon by design.
    """
    om._sweep_local_pnl_for_unpriced(db)

    long_row = _row(db, _LONG_ID)
    assert long_row["pnl"] is not None, (
        "a position closed 2 days ago must be priceable even though it was "
        "opened 40 days ago"
    )
