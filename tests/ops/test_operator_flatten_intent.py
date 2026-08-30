"""End-to-end proof of the durable operator-flatten marking chain.

The point of these tests is the SEAM, not the pieces. Each half was already
individually correct in the incident that motivated this: the flatten worked,
the reconciler worked, and the row still came out labelled as a strategy exit.
So the test that matters simulates all three hops — stamp on the OPEN row, a
reconciler-style close that MERGES notes, then derive the marking — because
that is where the defect lived.
"""
import json
import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.runtime.operator_flatten_intent import (  # noqa: E402
    INTENT_KEY, find_unmarked_intent_rows, stamp_intent,
)


def _db(tmp_path, notes="{}", status="open"):
    """Build the fixture from the CANONICAL schema, never a hand-rolled subset.

    The first draft declared its own nine-column ``trades`` and passed — until
    the write was routed through ``Database.update_trade``, which needs
    ``created_at``. A fixture that declares a schema production does not have
    is how the pairs tests passed against a fictional ``order_packages``
    (BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED). Calling ``create_tables``
    makes the fixture match production BY CONSTRUCTION rather than by my
    remembering to keep a column list in sync.
    """
    from src.units.db.database import Database
    p = tmp_path / "j.db"
    Database(str(p)).create_tables()
    c = sqlite3.connect(p)
    # Every NOT NULL column the canonical DDL declares (timestamp, symbol,
    # direction, entry_price, position_size, account_id) — the constraints are
    # part of what using the real schema buys.
    c.execute(
        "INSERT INTO trades (id, timestamp, symbol, direction, entry_price, "
        "position_size, account_id, status, exit_reason, notes, is_backtest) "
        "VALUES (4934, '2026-08-21T21:54:54+00:00', 'XRPUSDT', 'long', 1.4983, "
        "16.0, 'bybit_2', ?, '', ?, 0)",
        (status, notes))
    c.commit()
    return p, c


def test_stamp_then_reconciler_close_then_derive(tmp_path):
    """The whole chain. This is the test the incident needed."""
    p, c = _db(tmp_path, notes=json.dumps({"confidence": 0.53}))
    res = stamp_intent(str(p), "bybit_2", "XRPUSDT",
                       reason="bybit_2 hedge switch flat-symbol guard",
                       actor="flatten_bybit_position")
    assert res["state"] == "stamped" and res["stamped_ids"] == [4934]

    # Simulate the reconciler close EXACTLY as order_monitor does it:
    # decode the existing notes, add close-time keys, write back. If it
    # replaced instead of merging, the marker would be lost here — which is
    # the load-bearing assumption of the whole design.
    row = c.execute("SELECT notes FROM trades WHERE id=4934").fetchone()[0]
    notes = json.loads(row)
    notes.update({"closed_by": "monitor_reconciler",
                  "exit_price_source": "bybit_closed_pnl"})
    c.execute("UPDATE trades SET status='closed', exit_reason='reconciler_filled', "
              "notes=? WHERE id=4934", (json.dumps(notes),))
    c.commit()

    # The entry-time key, the marker, and the close-time key all coexist.
    after = json.loads(c.execute("SELECT notes FROM trades WHERE id=4934").fetchone()[0])
    assert after["confidence"] == 0.53
    assert after["closed_by"] == "monitor_reconciler"
    assert after[INTENT_KEY]["reason"] == "bybit_2 hedge switch flat-symbol guard"

    pending = find_unmarked_intent_rows(c)
    assert [r["id"] for r in pending] == [4934]
    assert pending[0]["exit_reason"] == "reconciler_filled"


def test_an_already_marked_row_is_not_rediscovered(tmp_path):
    p, c = _db(tmp_path,
               notes=json.dumps({INTENT_KEY: {"reason": "x"}, "closed_by_operator": True}),
               status="closed")
    assert find_unmarked_intent_rows(c) == []


def test_stamping_is_idempotent(tmp_path):
    p, _ = _db(tmp_path)
    a = stamp_intent(str(p), "bybit_2", "XRPUSDT", reason="r", actor="t")
    b = stamp_intent(str(p), "bybit_2", "XRPUSDT", reason="DIFFERENT", actor="t")
    assert a["stamped_ids"] == [4934]
    assert b["stamped_ids"] == [], "a re-run must not restamp or overwrite the reason"
    c = sqlite3.connect(p)
    notes = json.loads(c.execute("SELECT notes FROM trades WHERE id=4934").fetchone()[0])
    assert notes[INTENT_KEY]["reason"] == "r", "the FIRST observation is the honest one"


def test_no_open_rows_is_not_confused_with_unreadable(tmp_path):
    """The three states must stay apart: 'we looked and found nothing' is a
    real, ordinary outcome; 'we could not look' is not."""
    p, _ = _db(tmp_path, status="closed")          # nothing open
    got = stamp_intent(str(p), "bybit_2", "XRPUSDT", reason="r", actor="t")
    assert got["state"] == "no_open_rows"

    missing = stamp_intent(str(tmp_path / "nope.db"), "bybit_2", "XRPUSDT",
                           reason="r", actor="t")
    assert missing["state"] == "unreadable"
    assert missing["error"]


def test_stamp_never_raises_into_the_flatten(tmp_path):
    """A journal write must never turn a successful broker flatten into a
    reported failure."""
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite database at all")
    got = stamp_intent(str(bad), "bybit_2", "XRPUSDT", reason="r", actor="t")
    assert got["state"] == "unreadable"


def test_the_flatten_scripts_actually_CALL_it_not_merely_define_it():
    """Wiring, not behaviour — and asserted on the AST, not on a substring.

    A substring check is VACUOUS here: the helper's own ``def`` line contains
    the name, so deleting every call site still leaves the string present.
    Verified by planting exactly that (removing the call from the bybit script
    left a substring assertion green), which is the same presence-only trap
    the ``# data-wiring:`` marker carries. Assert a Call node.
    """
    import ast
    for name in ("flatten_bybit_position", "flatten_ib_position",
                 "flatten_alpaca_position"):
        path = REPO / "scripts" / "ops" / f"{name}.py"
        tree = ast.parse(path.read_text())
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_stamp_flatten_intent"
        ]
        assert calls, (
            f"{name} defines _stamp_flatten_intent but never CALLS it — "
            f"an operational flatten there is still indistinguishable from a "
            f"strategy exit")
