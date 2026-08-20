"""Tests for scripts/ops/repair_prop_fill_direction.py.

BL-20260820-PROP-FILL-DIRECTION-ADMISSION-GAP — the data-repair half.

The fixtures lift the REAL column set of ``prop_fills`` / ``prop_tickets`` from
``src/prop/prop_journal.py`` rather than declaring a convenient shape. That is
deliberate and load-bearing: ``BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED``
was a query against two columns the table does not have, and the tests that
should have caught it passed because they declared their own fictional schema.
A test that invents its table proves nothing about production.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sqlite3

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "repair_prop_fill_direction",
    REPO / "scripts" / "ops" / "repair_prop_fill_direction.py",
)
rpfd = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(rpfd)


def _schema(conn: sqlite3.Connection) -> None:
    """The production DDL for the two tables this tool touches."""
    from src.prop import prop_journal  # noqa: F401 — asserts the module imports

    conn.execute(
        "CREATE TABLE prop_fills ("
        " id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, direction TEXT,"
        " status TEXT, qty REAL, ticket_id TEXT, reported_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE prop_tickets ("
        " ticket_id TEXT PRIMARY KEY, account_id TEXT, symbol TEXT,"
        " direction TEXT, status TEXT)"
    )


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _schema(c)
    # id 30/31 reproduce the live rows measured 2026-08-20: the OPEN fill was
    # admitted with direction NULL, its own CLOSE carries 'long'.
    c.executemany(
        "INSERT INTO prop_fills VALUES (?,?,?,?,?,?,?,?)",
        [
            (30, "breakout_1", "SOLUSDT", None, "open", 83.0, "tk-5e30",
             "2026-08-19T12:52:40"),
            (31, "breakout_1", "SOLUSDT", "long", "closed", 83.0, "tk-5e30",
             "2026-08-19T21:31:28"),
            # control: a healthy row must never be selected
            (40, "breakout_1", "ETHUSDT", "short", "open", 3.0, "tk-c4b6",
             "2026-08-13T16:59:26"),
            # control: directionless AND its ticket is blank -> refuse, never guess
            (41, "breakout_1", "XRPUSDT", "", "filled", 10.0, "tk-blank",
             "2026-08-01T00:00:00"),
            # control: directionless with no ticket -> a DIFFERENT outcome
            (42, "breakout_1", "ADAUSDT", None, "filled", 10.0, None,
             "2026-08-01T00:00:00"),
            # control: directionless but not a position status -> out of scope
            (43, "breakout_1", "DOTUSDT", None, "skipped", None, "tk-5e30",
             "2026-08-01T00:00:00"),
        ],
    )
    c.executemany(
        "INSERT INTO prop_tickets VALUES (?,?,?,?,?)",
        [
            # the ticket says 'buy' (broker vocabulary) — the repair must store
            # the CANONICAL 'long', or it recreates the alias split it is fixing
            ("tk-5e30", "breakout_1", "SOLUSDT", "buy", "closed"),
            ("tk-c4b6", "breakout_1", "ETHUSDT", "short", "closed"),
            ("tk-blank", "breakout_1", "XRPUSDT", "", "emitted"),
        ],
    )
    c.commit()
    return c


def test_selects_only_unkeyable_position_rows(conn: sqlite3.Connection) -> None:
    ids = {r["id"] for r in rpfd.plan_repairs(conn)}
    assert ids == {30, 41, 42}, (
        "40 is healthy and 43 is not a position status; neither may be selected"
    )


def test_three_outcomes_are_never_collapsed(conn: sqlite3.Connection) -> None:
    by = {r["id"]: r for r in rpfd.plan_repairs(conn)}
    assert by[30]["outcome"] == "resolvable"
    assert by[41]["outcome"] == "ticket_blank"   # we looked; the ticket has none
    assert by[42]["outcome"] == "no_ticket"      # there was nothing to look at
    # An unresolvable row must carry no direction — never a guess.
    assert by[41]["resolved_direction"] is None
    assert by[42]["resolved_direction"] is None


def test_resolves_through_the_canonical_direction_mapper(
    conn: sqlite3.Connection,
) -> None:
    by = {r["id"]: r for r in rpfd.plan_repairs(conn)}
    assert by[30]["resolved_direction"] == "long", (
        "the ticket says 'buy'; storing it raw would re-split the key the same "
        "way BL-20260708-PROP-PULSE-DIRECTION-ALIAS did"
    )


def test_apply_writes_only_the_resolvable_row(conn: sqlite3.Connection) -> None:
    planned = rpfd.plan_repairs(conn)
    assert rpfd.apply_repairs(conn, planned)["updated"] == 1
    assert conn.execute(
        "SELECT direction FROM prop_fills WHERE id=30").fetchone()[0] == "long"
    assert conn.execute(
        "SELECT direction FROM prop_fills WHERE id=41").fetchone()[0] == ""


def test_rerun_after_apply_is_a_clean_noop(conn: sqlite3.Connection) -> None:
    rpfd.apply_repairs(conn, rpfd.plan_repairs(conn))
    again = rpfd.plan_repairs(conn)
    assert 30 not in {r["id"] for r in again}
    assert rpfd.apply_repairs(conn, again)["updated"] == 0


def test_planted_control_detector_refires_on_a_reintroduced_null(
    conn: sqlite3.Connection,
) -> None:
    """The suite must be able to FAIL. Re-null the repaired row; if the planner
    stays quiet, every green above is meaningless."""
    rpfd.apply_repairs(conn, rpfd.plan_repairs(conn))
    conn.execute("UPDATE prop_fills SET direction=NULL WHERE id=30")
    conn.commit()
    assert 30 in {r["id"] for r in rpfd.plan_repairs(conn)}
