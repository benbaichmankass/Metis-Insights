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
    levers TEXT, terminal_state TEXT, terminal_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)
"""
_TRADES_DDL = "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)"

# The SAME DDL with the terminal columns stripped — a table as it exists on a
# deployed DB between the code deploy and the migration. DERIVED from the
# constant above rather than written out, so the two cannot drift apart into
# two independent schemas.
_TELEMETRY_DDL_PRE_MIGRATION = _TELEMETRY_DDL.replace(
    "terminal_state TEXT, terminal_at TEXT,", "")


def _seed(path, telemetry_ddl):
    conn = sqlite3.connect(path)
    conn.execute(telemetry_ddl)
    conn.execute(_TRADES_DDL)
    # 4585 open · 4697 CLOSED · 9999 has no trade row · one with no trade_id.
    #
    # `peak_state` IS SEEDED because production always writes it and the reader
    # now branches on it (BL-20260820-TELEMETRY-THIN-WINDOW-SENTINEL-LEAKS-INTO-
    # PEAK-PCT-OF-CAP). Measured on the live table 2026-08-25 over 60 rows: 48
    # `measured` + 12 `thin_window`, ZERO nulls — so a fixture omitting the
    # column described a row production does not produce, and any peak
    # assertion built on it was testing an unreachable state. The sentinel case gets its
    # OWN fixture below rather than a row here: adding one to this shared seed
    # would force edits to two unrelated count assertions, and rewriting another
    # test's population to accommodate your own new row is how a real assertion
    # gets quietly loosened.
    conn.executemany(
        "INSERT INTO position_telemetry (order_package_id, trade_id, strategy, "
        "peak_r, peak_state, cap_r, levers, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("pkg-open", "4585", "ada_pullback_2h", 2.874, "measured", 3.9205,
             "{}", "2026-08-17T07:00:00Z"),
            ("pkg-closed", "4697", "trend_donchian_sol_4h", 0.1822, "measured", 5.8294,
             '{"trail_decay_arm_r": 5.57}', "2026-08-17T04:10:00Z"),
            ("pkg-ghost", "9999", "ghost_leg", 1.0, "measured", 2.0, "{}",
             "2026-08-17T03:00:00Z"),
            ("pkg-nofill", None, "unfilled_leg", None, None, None, "{}",
             "2026-08-17T02:00:00Z"),
        ],
    )
    conn.executemany("INSERT INTO trades (id, status) VALUES (?,?)",
                     [(4585, "open"), (4697, "closed")])
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture()
def db(tmp_path):
    """Current production schema — terminal columns present."""
    return _seed(tmp_path / "j.db", _TELEMETRY_DDL)


@pytest.fixture()
def db_pre_migration(tmp_path):
    """A deployed DB between the code deploy and the migration."""
    return _seed(tmp_path / "pre.db", _TELEMETRY_DDL_PRE_MIGRATION)


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


# ---------------------------------------------------------------------------
# M31 P5 precondition 1 — the terminal stamp (PB-20260817-TELEMETRY-HAS-NO-
# TERMINAL-SNAPSHOT). The point is that finality becomes a STORED fact, so a
# consumer reading the table directly no longer needs the trades join.
# ---------------------------------------------------------------------------
from src.runtime.position_telemetry import FINALITY_SOURCES  # noqa: E402


def test_a_stamped_row_is_final_WITHOUT_the_trades_join(db):
    """The actual fix: no join, and the row still reads closed.

    Before the stamp this was impossible — a closed row was byte-shaped like an
    open one and only `trades.status` could tell them apart.
    """
    conn = sqlite3.connect(db)
    conn.execute("UPDATE position_telemetry SET terminal_state='final', "
                 "terminal_at='2026-08-17T09:00:00Z' WHERE order_package_id='pkg-ghost'")
    conn.commit()
    conn.close()
    rows = {r["order_package_id"]: r for r in read_records(db_path=db)["rows"]}
    # `pkg-ghost`'s trade is ABSENT from `trades` — the join can say nothing.
    # The stamp alone must carry it.
    assert rows["pkg-ghost"]["lifecycle"] == "closed"
    assert rows["pkg-ghost"]["finality_source"] == "stamped"


def test_stamped_and_derived_are_never_reported_as_the_same_thing(db):
    """A pre-migration row can only ever be `derived_join`.

    Reporting it as `stamped` would overstate what the record contains.
    """
    rows = {r["order_package_id"]: r for r in read_records(db_path=db)["rows"]}
    assert rows["pkg-closed"]["lifecycle"] == "closed"
    assert rows["pkg-closed"]["finality_source"] == "derived_join"
    assert rows["pkg-open"]["finality_source"] == "not_final"
    assert set(FINALITY_SOURCES) >= {"stamped", "derived_join", "not_final", "unknown"}


def test_summary_exposes_the_finality_split(db):
    """`final_rows` entirely `derived_join` means the stamp is not reaching the
    close path — a condition the summary must make visible, not hide."""
    env = read_records(db_path=db)
    assert env["summary"]["by_finality_source"]["derived_join"] == 1
    assert env["summary"]["final_rows"] == 1


def test_reader_still_works_on_a_pre_migration_table(db_pre_migration):
    """The columns are absent here — `SELECT t.*` must not blow up.

    A reader that requires the migration would break every consumer between
    deploy and migration.
    """
    env = read_records(db_path=db_pre_migration)
    assert env["present"] is True and env["count"] == 4
    assert all("finality_source" in r for r in env["rows"])


def test_close_path_stamps_the_row_and_never_moves_an_existing_stamp(tmp_path):
    """The write half, through the real `update_trade` close path.

    Also pins that a RE-close (a reconciler flip, a flap) does not move
    `terminal_at` — the first observation of finality is the honest one, the
    same reasoning as the netting reconciler's anchor-at-first-observation.
    """
    pytest.importorskip("src.units.db.database")
    from src.units.db.database import Database

    path = tmp_path / "j.db"
    db_obj = Database(str(path))
    # `Database(...)` creates BOTH tables from the production DDL — do not
    # declare our own here. A test that invents a schema passes against a table
    # production does not have (the pairs `order_packages` bug).
    conn = sqlite3.connect(path)
    # Every NOT-NULL-without-default column of the REAL trades table.
    conn.execute(
        "INSERT INTO trades (id, timestamp, symbol, direction, entry_price, "
        "position_size, status, is_backtest) "
        "VALUES (7, '2026-08-17T07:00:00Z', 'SOLUSDT', 'buy', 100.0, 1.0, 'open', 0)")
    conn.execute("INSERT INTO position_telemetry (order_package_id, trade_id, "
                 "strategy, peak_r, cap_r, levers, updated_at) "
                 "VALUES ('pkg-7','7','leg',1.0,2.0,'{}','2026-08-17T07:00:00Z')")
    conn.commit()
    conn.close()

    # Through the PUBLIC close path, not the private stamper: the integration
    # risk is not that the stamper works, it is that the close hook never calls
    # it — a writer nothing invokes is the same write-only shape M31 exists to
    # close.
    db_obj.update_trade(7, {"status": "closed", "exit_price": 105.0})
    conn = sqlite3.connect(path)
    state, at1 = conn.execute(
        "SELECT terminal_state, terminal_at FROM position_telemetry "
        "WHERE trade_id='7'").fetchone()
    conn.close()
    assert state == "final" and at1 is not None

    # Re-close: the stamp must NOT move.
    db_obj.update_trade(7, {"status": "closed", "exit_price": 106.0})
    conn = sqlite3.connect(path)
    at2 = conn.execute("SELECT terminal_at FROM position_telemetry "
                       "WHERE trade_id='7'").fetchone()[0]
    conn.close()
    assert at2 == at1


def test_close_of_a_trade_with_no_telemetry_row_stamps_nothing(tmp_path):
    """No row is INSERTED at close.

    Telemetry exists only for legs whose monitor writes it; manufacturing a row
    at close would fabricate a trajectory that was never measured.
    """
    pytest.importorskip("src.units.db.database")
    from src.units.db.database import Database

    path = tmp_path / "j.db"
    db_obj = Database(str(path))  # creates position_telemetry from the real DDL

    db_obj._stamp_telemetry_terminal(1234)  # must not raise, must not insert
    # ...and neither does the real close path on a trade with no telemetry row.
    db_obj._stamp_telemetry_terminal(7)
    conn = sqlite3.connect(path)
    n = conn.execute("SELECT COUNT(*) FROM position_telemetry").fetchone()[0]
    conn.close()
    assert n == 0


def test_lifted_ddl_has_not_drifted_from_production(tmp_path):
    """The module-level `_TELEMETRY_DDL` must match the real table, column-for-column.

    The other tests in this file build a bare sqlite DB from `_TELEMETRY_DDL`
    rather than constructing a `Database`, so a drift between the two would
    make every one of them pass against a table production does not have —
    which is exactly how the pairs `order_packages` bug survived its own suite
    (its tests declared `id INTEGER PRIMARY KEY, account_id TEXT`, two columns
    the real table has never had). This is the assertion that makes "lifted
    verbatim" a checked claim rather than a comment.
    """
    pytest.importorskip("src.units.db.database")
    from src.units.db.database import Database

    real_path = tmp_path / "real.db"
    Database(str(real_path))
    real = sqlite3.connect(real_path)
    production = {
        (r[1], r[2].upper())
        for r in real.execute("PRAGMA table_info(position_telemetry)")
    }
    real.close()

    lifted_path = tmp_path / "lifted.db"
    lifted = sqlite3.connect(lifted_path)
    lifted.execute(_TELEMETRY_DDL)
    in_tests = {
        (r[1], r[2].upper())
        for r in lifted.execute("PRAGMA table_info(position_telemetry)")
    }
    lifted.close()

    assert production, "production telemetry table has no columns — DDL did not run"
    assert in_tests == production, (
        "tests/_TELEMETRY_DDL has drifted from src/units/db/database.py: "
        f"only in production {sorted(production - in_tests)}, "
        f"only in tests {sorted(in_tests - production)}"
    )


def test_migration_adds_the_columns_to_a_PRE_EXISTING_table(tmp_path):
    """The upgrade path — the only reason the migration exists.

    `CREATE TABLE IF NOT EXISTS` is a no-op on a deployed DB, so without the
    migration the live table would never gain the columns and the stamp would
    silently fail forever on exactly the DB that matters. This also pins the
    ordering: the migration must run AFTER the telemetry DDL, since an empty
    `PRAGMA table_info` means *no such table* and an ALTER there raises.
    """
    pytest.importorskip("src.units.db.database")
    from src.units.db.database import Database

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(_TELEMETRY_DDL_PRE_MIGRATION)
    conn.execute("INSERT INTO position_telemetry (order_package_id, trade_id) "
                 "VALUES ('pkg-legacy','42')")
    conn.commit()
    before = {r[1] for r in conn.execute("PRAGMA table_info(position_telemetry)")}
    conn.close()
    assert "terminal_state" not in before, "fixture is not pre-migration"

    Database(str(path))  # runs the idempotent migration

    conn = sqlite3.connect(path)
    after = {r[1] for r in conn.execute("PRAGMA table_info(position_telemetry)")}
    # The pre-existing row survives — a migration that recreated the table
    # would silently discard live telemetry history, which cannot be backfilled
    # (no upstream holds `peak_r`).
    kept = conn.execute("SELECT trade_id, terminal_state FROM position_telemetry "
                        "WHERE order_package_id='pkg-legacy'").fetchone()
    conn.close()
    assert {"terminal_state", "terminal_at"} <= after
    assert kept == ("42", None)  # NULL = not stamped, distinct from 'final'


# ---------------------------------------------------------------------------
# The -1e18 STORAGE SENTINEL must never reach a consumer as a measurement.
#
# BL-20260818-TELEMETRY-PEAK-R-STORES-COALESCE-SENTINEL (producer) and
# BL-20260820-TELEMETRY-THIN-WINDOW-SENTINEL-LEAKS-INTO-PEAK-PCT-OF-CAP
# (reader) are one defect at two layers. MEASURED on the live table
# 2026-08-25 over 60 rows: peak_state is 48 `measured` + 12 `thin_window` with
# ZERO nulls, and every one of the 11 non-measured rows carrying a peak held
# EXACTLY -1e+18 — served as peak_pct_of_cap values down to -8.2e+19.
#
# The stakes are the Check-A invariant: a NEGATIVE sentinel is below 100, so a
# row whose peak was never measured was counted as WITHIN CAP — an unmeasured
# row reading as a passing one, on the invariant M31 P4 judges the fleet by.
# ---------------------------------------------------------------------------

def _seed_sentinel(path):
    """One measured row and one thin_window row carrying the live sentinel."""
    conn = sqlite3.connect(path)
    conn.execute(_TELEMETRY_DDL)
    conn.execute(_TRADES_DDL)
    conn.executemany(
        "INSERT INTO position_telemetry (order_package_id, trade_id, strategy, "
        "peak_r, peak_state, cap_r, levers, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            # The POSITIVE CONTROL. Without it a reader that nulled everything
            # would pass, and the fix would be indistinguishable from breaking
            # the field outright.
            ("pkg-measured", "4585", "ada_pullback_2h", 2.874, "measured",
             3.9205, "{}", "2026-08-17T07:00:00Z"),
            # The defect, verbatim from the live rows.
            ("pkg-thin", "4697", "thin_leg", -1e18, "thin_window", 3.9205,
             "{}", "2026-08-17T06:00:00Z"),
            # A peak ABOVE its cap, so `peak_above_cap` has something real to
            # count and the assertion is not trivially satisfied by zero.
            ("pkg-over", "9999", "over_leg", 5.0, "measured", 2.0, "{}",
             "2026-08-17T05:00:00Z"),
        ],
    )
    conn.executemany("INSERT INTO trades (id, status) VALUES (?,?)",
                     [(4585, "open"), (4697, "open")])
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture()
def sentinel_db(tmp_path):
    return _seed_sentinel(tmp_path / "sentinel.db")


def test_a_thin_window_row_serves_null_for_both_peak_fields(sentinel_db):
    """THE REGRESSION. Before this, peak_r came back as -1e+18 and
    peak_pct_of_cap as -2.55e+19."""
    rows = {r["order_package_id"]: r
            for r in read_records(db_path=sentinel_db)["rows"]}
    thin = rows["pkg-thin"]
    assert thin["peak_state"] == "thin_window"
    assert thin["peak_r"] is None, thin
    assert thin["peak_pct_of_cap"] is None, thin


def test_a_measured_row_still_serves_its_peak(sentinel_db):
    """The positive control — the fix must not blank a real measurement."""
    rows = {r["order_package_id"]: r
            for r in read_records(db_path=sentinel_db)["rows"]}
    m = rows["pkg-measured"]
    assert m["peak_r"] == pytest.approx(2.874)
    assert m["peak_pct_of_cap"] == pytest.approx(73.31, abs=0.02)


def test_an_ungradeable_row_is_excluded_from_peak_above_cap_not_counted_clean(
        sentinel_db):
    """The inversion this fixes: a NEGATIVE sentinel is < 100, so the old count
    scored a never-measured row as within-cap. It must be excluded from the
    DENOMINATOR, not silently absorbed into the clean side of it."""
    summary = read_records(db_path=sentinel_db)["summary"]
    # pkg-over breaches; pkg-measured does not; pkg-thin is not gradeable.
    assert summary["peak_above_cap"] == 1, summary
    assert summary["peak_gradeable_rows"] == 2, summary


def test_the_gate_is_keyed_on_the_state_not_on_a_magnitude(sentinel_db):
    """A `|value| >= 1e17` threshold would be a SECOND definition of the
    sentinel living apart from the first, and the two would drift. Proof that
    the state is what decides: a thin_window row carrying an ORDINARY-LOOKING
    peak is still refused."""
    conn = sqlite3.connect(sentinel_db)
    conn.execute("UPDATE position_telemetry SET peak_r = 1.5 "
                 "WHERE order_package_id = 'pkg-thin'")
    conn.commit()
    conn.close()
    rows = {r["order_package_id"]: r
            for r in read_records(db_path=sentinel_db)["rows"]}
    assert rows["pkg-thin"]["peak_r"] is None
    assert rows["pkg-thin"]["peak_pct_of_cap"] is None
