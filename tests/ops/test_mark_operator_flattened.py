"""Tests for the operator-flatten marker.

The script carries its own `--self-test`, and the wrapper runs it as a
precondition. These tests exist because a self-test the WRAPPER runs is not the
same as one CI runs: if the wrapper's precondition line were ever dropped,
nothing would notice. So this asserts both the behaviour AND the wiring.
"""
import importlib.util
import json
import pathlib
import sqlite3
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ops" / "mark_operator_flattened.py"
WRAPPER = REPO / "scripts" / "ops" / "mark_operator_flattened_action.sh"


def _mod():
    spec = importlib.util.spec_from_file_location("mof", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _db(tmp_path):
    p = tmp_path / "j.db"
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT,
        exit_reason TEXT, notes TEXT, is_backtest INTEGER, symbol TEXT,
        account_id TEXT, pnl REAL, exit_price REAL)""")
    c.execute("INSERT INTO trades VALUES (4934,'closed','reconciler_filled',?,0,"
              "'XRPUSDT','bybit_2',-2.4509,1.3866)",
              (json.dumps({"exit_price_source": "bybit_closed_pnl",
                           "closed_by": "monitor_reconciler"}),))
    c.commit()
    return c


def test_the_script_self_test_passes():
    r = subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_wrapper_runs_the_self_test_as_a_precondition():
    """The wiring, not the behaviour. A dropped precondition is silent."""
    body = WRAPPER.read_text()
    assert "--self-test" in body, "wrapper no longer runs the self-test"


def test_marking_sets_the_reason_and_preserves_broker_truth(tmp_path):
    m = _mod()
    conn = _db(tmp_path)
    ups, refs = m.plan(conn, [4934], "hedge switch flat-symbol guard")
    assert refs == []
    m.apply(conn, ups)
    row = conn.execute("SELECT exit_reason, notes, pnl, exit_price FROM trades "
                       "WHERE id=4934").fetchone()
    assert row[0] == "operator_flatten_reconciled"
    notes = json.loads(row[1])
    assert notes["closed_by_operator"] is True
    assert notes["operator_close_reason"] == "hedge switch flat-symbol guard"
    assert notes["pre_mark_exit_reason"] == "reconciler_filled"
    # The PRICE's provenance is a different question from the CLOSE's cause.
    assert notes["exit_price_source"] == "bybit_closed_pnl"
    # And no monetary field moved.
    assert row[2] == -2.4509 and row[3] == 1.3866


def test_a_bad_id_refuses_the_whole_batch(tmp_path):
    m = _mod()
    conn = _db(tmp_path)
    ups, refs = m.plan(conn, [4934, 999999], "x")
    assert len(refs) == 1 and "no such trade" in refs[0]
    # The CLI must write nothing at all when any id is bad.
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--trade-ids", "4934,999999",
         "--reason", "x", "--db", str(tmp_path / "j.db"), "--apply"],
        capture_output=True, text=True)
    assert r.returncode == 2, r.stdout
    still = conn.execute("SELECT exit_reason FROM trades WHERE id=4934").fetchone()[0]
    assert still == "reconciler_filled", "a bad sibling id must not stop a write silently"


def test_the_fixture_columns_exist_in_the_real_ddl():
    """The self-test builds its own in-memory `trades`. A fixture declaring a
    schema production does not have is how the pairs tests passed against a
    fictional `order_packages` (BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED),
    so pin the fixture's columns as a SUBSET of the canonical DDL.

    This test is what makes the `# data-wiring:` annotation in the script true
    rather than merely present.
    """
    ddl = (REPO / "src" / "units" / "db" / "database.py").read_text()
    start = ddl.index("CREATE TABLE IF NOT EXISTS trades (")
    real = set()
    for line in ddl[start:start + 6000].splitlines()[1:]:
        st = line.strip()
        if st.startswith(")"):
            break
        tok = st.split()
        if tok and tok[0].isidentifier():
            real.add(tok[0])

    fixture = {"id", "status", "exit_reason", "notes", "is_backtest", "symbol",
               "account_id", "pnl", "exit_price"}
    missing = fixture - real
    assert not missing, (
        f"the self-test fixture declares column(s) production's trades table "
        f"does not have: {sorted(missing)} (known real columns: {len(real)})")
    # And the two columns the script actually WRITES must be among them.
    assert {"exit_reason", "notes"} <= real
