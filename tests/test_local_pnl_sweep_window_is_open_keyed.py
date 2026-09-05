"""`_sweep_local_pnl_for_unpriced` must bound its scan by the CLOSE, not the
OPEN — or a position held longer than the window can never be priced.

MI-127 (A) diagnosed it; **MI-128 fixed it** (Tier-2, operator-approved
2026-09-05, `approved_with_conditions`: FORWARD-ONLY — the scan window was
re-keyed so future closes are priced, and the already-silent historical rows
were deliberately NOT back-filled). The file name still says
`window_is_open_keyed` because that is the defect it guards against; the
assertions below now pin the fixed behaviour.

⚠️ The filename is therefore the DEFECT NAME, not a description of current
behaviour. It is deliberately not renamed — the backlog row, the work object
and `MI-127`'s diagnosis all reference this path.

THE DEFECT THIS GUARDS AGAINST, IN ONE LINE
-------------------------------------------
The sweep's scan query used to bound itself with::

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

THE FIX, AS APPLIED (MI-128)
----------------------------
Key the window on the close, falling back to the open for rows that have no
`closed_at` (the `orphaned` rows this sweep also selects)::

    -    AND datetime(created_at) >= datetime('now', '-14 days')
    +    AND datetime(COALESCE(closed_at, created_at)) >= datetime('now', '-14 days')

The `ORDER BY` was re-keyed with it: it feeds `LIMIT 100`, so ordering by the
open would leave the same defect one level down.

RE-MEASURED AFTER THE FIX (same population, 2026-09-05)
-------------------------------------------------------
The re-key is **strictly additive** — measured against the full live journal,
9 rows gain eligibility and **0 lose it**, so the fix cannot remove coverage.
Every non-null `created_at` (5,494) and `closed_at` (1,625) parses under
sqlite `datetime()` (0 unparseable), so the `COALESCE` cannot silently drop a
row either.

⚠️ FORWARD-ONLY IS AN OPERATOR CONDITION, AND MERGING IS THE DEPLOY HERE.
Of those 9 newly-eligible rows, 4 already carry `pnl_source: unmeasured` (the
sweep will take the `already_unmeasured` branch and write nothing), leaving
**5 historical rows the first post-deploy tick would actually write** — the
same five the MI-127 diagnosis named. Each would be filled by a sanctioned
path (a recorded fill, a close-time anchor stamped ESTIMATED, or an explicit
`unmeasured` declaration); none can be fabricated, because the sweep has no
mark-to-market fallback left. That is nonetheless a back-fill of already-silent
rows and is the OPERATOR's call at merge time, not this unit's.
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


# ------------------------------------------------------------ structural pin
def test_the_window_predicate_keys_on_the_close(db):
    """Structural pin, so the SQL and the behavioural tests move together.

    Checked against the executing source rather than the docstring — the
    prose-vs-code confusion `test_sweep_no_mark_fabrication` had to correct.

    The source is normalised (comments dropped, string-literal quotes removed,
    whitespace collapsed) because the predicate is written across two adjacent
    literals to stay inside the line limit. A pin that matched only a
    contiguous substring would fail on a purely cosmetic re-wrap, which is the
    kind of brittleness that teaches people to delete pins.
    """
    import inspect
    import re

    src = "\n".join(
        line for line in
        inspect.getsource(om._sweep_local_pnl_for_unpriced).splitlines()
        if not line.lstrip().startswith("#")
    )
    normalised = re.sub(r"\s+", " ", src.replace('"', ""))

    assert (
        "AND datetime(COALESCE(closed_at, created_at)) "
        ">= datetime('now', '-14 days')"
    ) in normalised, (
        "the scan window no longer keys on the CLOSE — re-read MI-128 and "
        "update the behavioural expectations in this file together with it. "
        "Keying it on created_at is the defect this file exists to prevent: a "
        "position held longer than the window is already outside it when it "
        "closes, so it is never priced AND never declared unmeasured."
    )
    assert "datetime(created_at) >= datetime('now', '-14 days')" not in normalised, (
        "the open-keyed predicate is back in the scan query"
    )
    # The LIMIT-level half of the same defect: ordering by the OPEN starves a
    # just-closed long-held row behind 100 more-recently-opened ones.
    assert (
        "ORDER BY datetime(COALESCE(closed_at, created_at)) DESC" in normalised
    ), "the scan ORDER BY must key on the close too — it feeds LIMIT 100"


# ------------------------------------------------- the fix's acceptance test
def test_a_recent_close_is_priceable_regardless_of_hold_duration(db, anchored):
    """THE ACCEPTANCE CRITERION for the proposed fix.

    A close is a close. How long the position was held before it is a property
    of the STRATEGY, not of whether its realised PnL can be computed — and
    every `_1d` trend and pullback leg in the fleet is long-horizon by design.
    """
    summary = om._sweep_local_pnl_for_unpriced(db)

    assert summary["scanned"] == 2, (
        "both rows close at the same recent moment, so both must be scanned; "
        f"scanned={summary['scanned']} — the window is still keyed on the open"
    )

    long_row = _row(db, _LONG_ID)
    assert long_row["pnl"] is not None, (
        "a position closed 2 days ago must be priceable even though it was "
        "opened 40 days ago"
    )


def test_a_long_held_close_is_never_silent(db, anchored, monkeypatch):
    """The done-condition, asserted directly: a long-held close must reach a
    TERMINAL, PROVENANCED state — priced, or explicitly declared unmeasured.
    Never a silent NULL.

    `test_a_recent_close_is_priceable_regardless_of_hold_duration` above pins
    the anchored branch. This pins the OTHER outcome — and the distinction
    between them is the entire point of the fix, so it is asserted rather than
    left to follow from the first. With the venue offering no bar, the row must
    still come back carrying `pnl_source: unmeasured`; before the re-key it
    came back carrying nothing at all, which is indistinguishable from a row
    nobody ever looked at, because that is what it was.
    """
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: (None, "no_anchor"))

    summary = om._sweep_local_pnl_for_unpriced(db)

    long_row = _row(db, _LONG_ID)
    assert long_row["pnl"] is None, "no anchor means no price — nothing to book"
    notes = json.loads(long_row["notes"] or "{}")
    assert notes.get("pnl_source") == "unmeasured", (
        "a long-held close the venue cannot anchor must be DECLARED unmeasured, "
        f"not left silent; notes={notes!r}"
    )
    assert summary["declared_unmeasured"] >= 1
