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
import json
import pathlib
import sqlite3
import subprocess
import sys

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

# --- The gap that let a real failure reach the live VM ------------------------
#
# Every test above imports the module through importlib with the repo root
# already on sys.path, and calls plan_repairs/apply_repairs directly. NONE of
# them called main(), so the module's own `src.*` import bootstrap was never
# exercised — and it was wrong: two dirname calls resolved to `scripts/` rather
# than the repo root, and the first live dry run died with
# `ModuleNotFoundError: No module named 'src'`, having written nothing.
#
# A test that hands the module a world it would not have in production proves
# the logic and nothing about the wiring. These run it the way the wrapper does.


def test_runs_as_a_script_the_way_the_wrapper_invokes_it(tmp_path) -> None:
    """`cd <repo> && python scripts/ops/repair_prop_fill_direction.py --db X`.

    python puts the SCRIPT's directory on sys.path, not the CWD, so this is the
    invocation that catches a wrong repo-root computation. Asserting on the
    absence of the import error specifically — a bare returncode check would
    pass on any other failure and hide a regression here.
    """
    db = tmp_path / "j.db"
    conn = sqlite3.connect(db)
    _schema(conn)
    conn.commit()
    conn.close()

    proc = subprocess.run(
        [sys.executable, "scripts/ops/repair_prop_fill_direction.py",
         "--db", str(db), "--account", "breakout_1"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert "ModuleNotFoundError" not in proc.stderr, (
        "the script cannot import its own dependencies when run as a script — "
        f"this is the failure that reached the live VM.\n{proc.stderr}"
    )
    assert proc.returncode == 0, (
        f"expected a clean no-candidate run.\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert json.loads(proc.stdout)["candidates"] == 0


def test_repo_root_bootstrap_actually_reaches_the_repo_root() -> None:
    """Direct check on the value the module computed, so a future edit that
    drops or adds a `dirname` fails here with a clear message rather than at
    dispatch on the VM."""
    assert hasattr(rpfd, "_REPO_ROOT"), (
        "the module no longer names its computed repo root, so nothing can "
        "assert it is right"
    )
    resolved = pathlib.Path(rpfd._REPO_ROOT)
    assert resolved == REPO, (
        f"the module resolves its repo root to {str(resolved)!r}, not "
        f"{str(REPO)!r}; `src` would not be importable from there"
    )
    assert (resolved / "src").is_dir()


# --------------------------------------------------------------------------
# THE WRAPPER MUST RESOLVE AND EXPORT THE DB PATH.
#
# Added 2026-08-20 after the SECOND live dry run (#10049) failed with
# `sqlite3.OperationalError: unable to open database file`. The python resolver
# order is TRADE_JOURNAL_DB -> $DATA_DIR/trade_journal.db -> repo-root, and a
# wrapper invoked over SSH inherits NEITHER (they live in the systemd unit's
# EnvironmentFile, not in an interactive shell). So it fell through to a
# repo-root path that does not exist on the live VM, and a `mode=ro` URI
# connection cannot create one.
#
# ~20 sibling wrappers already do this (backfill_closed_at_action.sh:34,76).
# DEVIATING FROM AN ESTABLISHED IDIOM IS THE DEFECT, so these assert against the
# SHIPPING wrapper text — a test embedding its own copy passes for ever after
# the original changes.
#
# Note what the existing suite could not catch: #10042 added a test that runs
# the script the way the wrapper does, but it runs it HERE, where the repo-root
# fallback finds a real file. The environment difference IS the bug, so the
# only durable assertion is on the wrapper's contract with the python process.
# --------------------------------------------------------------------------

_WRAPPER = REPO / "scripts" / "ops" / "repair_prop_fill_direction_action.sh"
_CONTROL_WRAPPER = REPO / "scripts" / "ops" / "backfill_closed_at_action.sh"


def test_the_wrapper_resolves_the_db_path_via_the_canonical_helper() -> None:
    body = _WRAPPER.read_text()
    assert "runtime_db_path" in body, (
        "the wrapper must resolve the DB path via _lib.sh::runtime_db_path, "
        "which calls load_runtime_env and so reads the SAME value the trader "
        "uses; without it the python resolver falls through to repo-root"
    )


def test_the_wrapper_exports_TRADE_JOURNAL_DB_to_the_python_process() -> None:
    """Resolving it is not enough — the python process must SEE it."""
    exec_lines = [
        line for line in _WRAPPER.read_text().splitlines()
        if "repair_prop_fill_direction.py" in line and line.strip().startswith("exec")
    ]
    assert exec_lines, "no exec line invoking the python script"
    for line in exec_lines:
        assert "TRADE_JOURNAL_DB=" in line, (
            f"the python invocation must carry TRADE_JOURNAL_DB; got: {line}"
        )


def test_the_control_sibling_still_uses_the_idiom_this_suite_enforces() -> None:
    """POSITIVE CONTROL, and the reason it is separate.

    If the canonical idiom ever moves, the two tests above would keep passing
    while enforcing a stale pattern. This one fails loudly instead, so the
    suite cannot quietly hold the wrapper to a convention the repo abandoned.
    """
    assert _CONTROL_WRAPPER.is_file(), f"control wrapper missing: {_CONTROL_WRAPPER}"
    control = _CONTROL_WRAPPER.read_text()
    assert "runtime_db_path" in control and "TRADE_JOURNAL_DB=" in control, (
        "the control sibling no longer uses the idiom these tests enforce — "
        "re-derive the canonical pattern before trusting them"
    )


def test_a_missing_db_names_the_path_instead_of_sqlites_bare_message(tmp_path) -> None:
    """`unable to open database file` names no path and no cause.

    The process knows both. Reporting neither is the unprovenanced-diagnostic
    class, and it cost a full dispatch cycle on #10049 to learn one fact the
    process already had.
    """
    missing = tmp_path / "definitely_not_here.db"
    proc = subprocess.run(
        [sys.executable,
         str(REPO / "scripts" / "ops" / "repair_prop_fill_direction.py"),
         "--db", str(missing)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert str(missing) in combined, combined
    assert "TRADE_JOURNAL_DB" in combined, combined
    assert "unable to open database file" not in combined, combined
