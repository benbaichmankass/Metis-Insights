"""M31 P3 — the telemetry READ half.

The defect these exist to pin is not "the query returns rows". It is that
`position_telemetry` **cannot say whether a row is final**: the table is
UPSERT-on-`order_package_id` with no status column, so when a trade closes its
row simply stops being updated and is byte-shaped like an open one. Measured on
the live table 2026-08-17: 14 rows, 13 open + 1 closed, and the closed one was
findable only by the join these readers perform.

So the assertions below are mostly about STATES, not about row counts.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime.position_telemetry import (
    ARM_REACH_STATES,
    LIFECYCLE_STATES,
    enrich_record,
    read_records,
    telemetry_by_trade_id,
)

# Lifted from src/units/db/database.py rather than hand-written: a test that
# declares its own schema passes against a table production does not have,
# which is exactly how the pairs `order_packages` bug survived its tests.
_TELEMETRY_DDL = """
CREATE TABLE position_telemetry (
    order_package_id TEXT PRIMARY KEY, trade_id TEXT, strategy TEXT, symbol TEXT,
    account_id TEXT, direction TEXT, entry REAL, risk_per_unit REAL,
    last_price REAL, open_r REAL, peak_r REAL, peak_state TEXT, giveback_r REAL,
    bars_held INTEGER, bars_since_peak INTEGER, cap_r REAL, pct_of_cap REAL,
    r_to_stop REAL, r_to_target REAL, rr_from_here REAL, peak_provenance TEXT,
    levers TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)
"""
_TRADES_DDL = "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "j.db"
    conn = sqlite3.connect(path)
    conn.execute(_TELEMETRY_DDL)
    conn.execute(_TRADES_DDL)
    # 4585 open · 4697 CLOSED · 9999 has no trade row · one with no trade_id.
    conn.executemany(
        "INSERT INTO position_telemetry (order_package_id, trade_id, strategy, "
        "peak_r, cap_r, levers, updated_at) VALUES (?,?,?,?,?,?,?)",
        [
            ("pkg-open", "4585", "ada_pullback_2h", 2.874, 3.9205, "{}", "2026-08-17T07:00:00Z"),
            ("pkg-closed", "4697", "trend_donchian_sol_4h", 0.1822, 5.8294,
             '{"trail_decay_arm_r": 5.57}', "2026-08-17T04:10:00Z"),
            ("pkg-ghost", "9999", "ghost_leg", 1.0, 2.0, "{}", "2026-08-17T03:00:00Z"),
            ("pkg-nofill", None, "unfilled_leg", None, None, "{}", "2026-08-17T02:00:00Z"),
        ],
    )
    conn.executemany("INSERT INTO trades (id, status) VALUES (?,?)",
                     [(4585, "open"), (4697, "closed")])
    conn.commit()
    conn.close()
    return str(path)


def test_all_four_lifecycle_states_are_reachable(db):
    """The closed row must be DISTINGUISHABLE — that is the whole point."""
    rows = {r["order_package_id"]: r for r in read_records(db_path=db)["rows"]}
    assert rows["pkg-open"]["lifecycle"] == "open"
    assert rows["pkg-closed"]["lifecycle"] == "closed"
    assert rows["pkg-ghost"]["lifecycle"] == "unknown_trade_absent"
    assert rows["pkg-nofill"]["lifecycle"] == "unknown_no_trade_id"
    assert {r["lifecycle"] for r in rows.values()} == set(LIFECYCLE_STATES)


def test_a_row_whose_trade_is_absent_is_returned_not_dropped(db):
    """A LEFT JOIN, deliberately.

    An inner join would drop `pkg-ghost`, making an UNATTRIBUTABLE row look like
    a row that does not exist — a silent shrink of the population.
    """
    env = read_records(db_path=db)
    assert env["count"] == 4
    assert any(r["order_package_id"] == "pkg-ghost" for r in env["rows"])


def test_unknown_states_are_not_folded_into_closed(db):
    """`unknown_*` must never be graded as final.

    Counting "not open" as closed would inflate the final-row population — the
    exact denominator M31 P4 Check B abstains on.
    """
    env = read_records(db_path=db)
    assert env["summary"]["final_rows"] == 1
    assert env["summary"]["by_lifecycle"]["closed"] == 1


def test_arm_reach_uses_this_rows_own_ceiling(db):
    """arm 5.57 under cap 5.8294 is REACHABLE on this row.

    The registry grades that leg `unmeasured` from an older 0/16 population;
    a per-row read is a different question and must answer it independently.
    """
    rows = {r["order_package_id"]: r for r in read_records(db_path=db)["rows"]}
    assert rows["pkg-closed"]["arm_r"] == 5.57
    assert rows["pkg-closed"]["arm_reach"] == "reachable"
    assert rows["pkg-open"]["arm_reach"] == "no_arm_declared"


def test_arm_above_cap_is_unreachable_and_missing_cap_is_unmeasured():
    """`unreachable` and `unmeasured` are different failures and must not merge."""
    over = enrich_record(
        {"trade_id": "1", "cap_r": 2.1258, "peak_r": 0.0669,
         "levers": '{"trail_decay_arm_r": 3.56}'}, "open", True)
    assert over["arm_reach"] == "unreachable"     # 167% of cap — qqq_trend_long_1d live
    nocap = enrich_record(
        {"trade_id": "1", "cap_r": None, "levers": '{"trail_decay_arm_r": 3.56}'},
        "open", True)
    assert nocap["arm_reach"] == "unmeasured"
    assert set(ARM_REACH_STATES) >= {"reachable", "unreachable",
                                     "no_arm_declared", "unmeasured"}


def test_peak_pct_of_cap_is_the_peak_not_the_current_position(db):
    """Distinct from the stored `pct_of_cap`, which is computed from `open_r`."""
    rows = {r["order_package_id"]: r for r in read_records(db_path=db)["rows"]}
    assert rows["pkg-open"]["peak_pct_of_cap"] == pytest.approx(73.31, abs=0.02)
    # No cap ⇒ no ratio. Never 0.0, which would assert a measured floor.
    assert rows["pkg-nofill"]["peak_pct_of_cap"] is None


def test_lower_bound_caveat_is_on_every_row_including_closed(db):
    """Closed does not mean final-exact: the last write precedes the close."""
    assert all(r["peak_r_is_lower_bound"] is True
               for r in read_records(db_path=db)["rows"])


def test_missing_table_is_present_false_not_an_empty_success(tmp_path):
    """"Table absent" and "table empty" are different facts.

    Collapsing them is what `/api/diag/journal` was fixed to stop doing.
    """
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    env = read_records(db_path=str(empty))
    assert env["present"] is False
    assert env["rows"] == [] and env["error"] is not None


def test_by_trade_id_map_keys_on_trade_and_skips_unfilled(db):
    m = telemetry_by_trade_id(db_path=db)
    assert set(m) == {"4585", "4697", "9999"}
    assert m["4697"]["lifecycle"] == "closed"


def test_r_block_absent_is_none_never_a_zeroed_block():
    """`None` says "this leg writes no telemetry"; zeros would assert a flat trade."""
    # Skips only where FastAPI is absent (a bare sandbox); CI installs it, so
    # this is a real assertion there rather than a permanently-green skip.
    pytest.importorskip("fastapi")
    from src.web.api.routers.dashboard import _r_block
    assert _r_block(None) is None
    blk = _r_block({"open_r": 2.3, "peak_r": 2.9, "cap_r": 3.9,
                    "peak_pct_of_cap": 74.4, "arm_reach": "no_arm_declared",
                    "peak_r_is_lower_bound": True})
    assert blk["peakR"] == 2.9 and blk["peakPctOfCap"] == 74.4
    assert blk["peakRIsLowerBound"] is True
    # A field the record does not carry is absent, not defaulted to 0.
    assert blk["rrFromHere"] is None
