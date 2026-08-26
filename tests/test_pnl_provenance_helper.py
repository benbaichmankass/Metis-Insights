"""`src/web/api/_pnl_provenance.py` — one owner for journal-`pnl` coverage.

GATE 0 / G3. Four routers call this (`/api/bot/stats`, `/api/pnl/history`,
`/api/bot/strategy/attribution`, `/api/bot/strategies`, `/api/pnl`), so the
definitions live in ONE place: the fourth bespoke copy is how two surfaces end
up disagreeing about the same population.

The states this pins are the ones that were collapsed elsewhere in this repo
often enough to earn a canonical rule (`CLAUDE-RULES-CANONICAL` § "Collapsed
states"): *we could not look* must stay distinguishable from *we looked and
found nothing*, and both from *we looked and nothing was measured*.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.web.api._pnl_provenance import (
    KEYS,
    block_for_query,
    block_for_rows,
    could_not_look,
    fetch_rows,
    looked_and_found_nothing,
)


def _row(pnl, src):
    return {"pnl": pnl, "notes": json.dumps({"exit_price_source": src} if src else {})}


def test_the_count_and_the_sum_are_over_different_populations():
    """MEASURED-only count, MEASURED+ESTIMATED sum — copied from /performance,
    where the R4 gate depends on exactly this asymmetry. Neither may be
    harmonised to the other."""
    b = block_for_rows([
        _row(10.0, "bybit_closed_pnl"),   # MEASURED
        _row(100.0, "candle_at_close"),   # ESTIMATED
        _row(500.0, "local_markprice"),   # FABRICATED
        _row(7.0, None),                  # UNVERIFIED
    ])
    assert b["pnlMeasuredCount"] == 1
    assert b["pnlEstimatedCount"] == 1
    assert b["pnlCoverage"] == pytest.approx(0.25), "MEASURED-only"
    assert b["totalPnLMeasured"] == pytest.approx(110.0), "MEASURED+ESTIMATED"


def test_the_three_states_are_distinguishable():
    empty = looked_and_found_nothing()
    unknown = could_not_look()
    nothing_measured = block_for_rows([_row(1.0, "local_markprice")])

    # All three agree the ratio is absent-or-zero, so the COUNTS are what
    # separate them. That is why the counts are not None in `empty`.
    assert empty["pnlMeasuredCount"] == 0 and empty["pnlCoverage"] is None
    assert unknown["pnlMeasuredCount"] is None and unknown["pnlCoverage"] is None
    assert nothing_measured["pnlCoverage"] == 0.0

    assert empty != unknown, "'we looked' must not equal 'we could not look'"
    assert nothing_measured != empty, "0.0 coverage is not an empty population"


def test_every_shape_carries_every_key():
    """A key that vanishes makes a consumer branch on absence, and absence is
    not one of the states."""
    for shape in (looked_and_found_nothing(), could_not_look(),
                  block_for_rows([_row(1.0, "bybit_closed_pnl")])):
        assert set(KEYS) <= set(shape), shape


def test_an_ungradeable_journal_is_could_not_look_not_zeros(tmp_path):
    """No `notes` column: we cannot grade at all. Reporting zeros would assert
    an observation nobody made."""
    db = tmp_path / "j.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, pnl REAL, status TEXT);"
        "INSERT INTO trades (pnl, status) VALUES (1.0, 'closed');")
    conn.commit()
    conn.close()
    assert fetch_rows(db, "1=1") is None
    assert block_for_query(db, "1=1") == could_not_look()


def test_a_missing_db_gets_the_caller_s_declared_reading(tmp_path):
    """Callers genuinely differ, so the reading is a parameter rather than a
    guess. `/stats` already treats a missing file as 'no trades yet on a fresh
    install'; a caller with no such convention says we could not look."""
    missing = tmp_path / "nope.db"
    assert block_for_query(missing, "1=1") == could_not_look()
    assert block_for_query(missing, "1=1", missing_db_is_empty=True) == \
        looked_and_found_nothing()


def test_reads_are_strictly_read_only(tmp_path):
    """This is a read path and must never be able to write."""
    db = tmp_path / "j.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, pnl REAL, notes TEXT);"
        "INSERT INTO trades (pnl, notes) VALUES (1.0, '{}');")
    conn.commit()
    conn.close()
    before = db.read_bytes()
    assert block_for_query(db, "1=1")["pnlMeasuredCount"] == 0
    assert db.read_bytes() == before


def test_an_unparseable_pnl_does_not_lose_the_whole_block():
    """One bad row must not take the caveat down for the population."""
    b = block_for_rows([
        _row(10.0, "bybit_closed_pnl"),
        {"pnl": "not-a-number", "notes": json.dumps(
            {"exit_price_source": "bybit_closed_pnl"})},
    ])
    assert b["pnlMeasuredCount"] == 2, "both rows still GRADE as measured"
    assert b["totalPnLMeasured"] == pytest.approx(10.0), "only the sum skips it"
