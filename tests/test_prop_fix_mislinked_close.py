"""Tests for the mis-linked prop-close repair (BL-20260706-PROP-CLOSE-MISLINK,
Option B): guarded, idempotent relink + status corrections on the prop journal.
"""
from __future__ import annotations

import sqlite3

from scripts.ops.prop_fix_mislinked_close import apply_plan, plan_repair


# The exact ETH incident parameters.
FILL_ID = 17
WRONG = "prop-manual-849ece101a3c"   # emitted signal the close wrongly hit
RIGHT = "prop-manual-5bc393741ec4"   # the real filled position


def _db(fill_ticket=WRONG, right_status="filled", wrong_status="closed"):
    """Prop journal in the post-incident (broken) state by default."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE prop_fills (id INTEGER PRIMARY KEY, ticket_id TEXT, "
                 "symbol TEXT, status TEXT, pnl REAL)")
    conn.execute("CREATE TABLE prop_tickets (ticket_id TEXT PRIMARY KEY, status TEXT, "
                 "symbol TEXT, direction TEXT)")
    conn.execute("INSERT INTO prop_fills VALUES (?,?,?,?,?)",
                 (FILL_ID, fill_ticket, "ETHUSDT", "closed", -71.66))
    conn.execute("INSERT INTO prop_tickets VALUES (?,?,?,?)",
                 (RIGHT, right_status, "ETHUSDT", "long"))
    conn.execute("INSERT INTO prop_tickets VALUES (?,?,?,?)",
                 (WRONG, wrong_status, "ETHUSDT", "long"))
    conn.commit()
    return conn


def _repair(conn):
    return plan_repair(
        conn, relink_fill_id=FILL_ID, relink_from_ticket=WRONG,
        relink_to_ticket=RIGHT, close_ticket=RIGHT, restore_ticket=WRONG,
        restore_status="expired",
    )


def _state(conn):
    fill = conn.execute("SELECT ticket_id FROM prop_fills WHERE id=?", (FILL_ID,)).fetchone()[0]
    right = conn.execute("SELECT status FROM prop_tickets WHERE ticket_id=?", (RIGHT,)).fetchone()[0]
    wrong = conn.execute("SELECT status FROM prop_tickets WHERE ticket_id=?", (WRONG,)).fetchone()[0]
    return fill, right, wrong


def test_plan_matches_all_three_ops_on_broken_state():
    conn = _db()
    ops = _repair(conn)
    actionable = [o for o in ops if not o.get("skip")]
    assert len(actionable) == 3
    kinds = {o["op"] for o in actionable}
    assert kinds == {"relink_fill", "close_position_ticket", "restore_phantom_ticket"}


def test_dry_run_changes_nothing():
    conn = _db()
    _repair(conn)  # planning is read-only
    assert _state(conn) == (WRONG, "filled", "closed")


def test_apply_repairs_to_clean_state():
    conn = _db()
    changed = apply_plan(conn, _repair(conn))
    assert changed == 3
    # fill relinked to the real position; real position closed; phantom restored.
    assert _state(conn) == (RIGHT, "closed", "expired")


def test_idempotent_second_apply_is_noop():
    conn = _db()
    apply_plan(conn, _repair(conn))
    # Re-plan against the now-repaired state: every op's precondition fails.
    ops2 = _repair(conn)
    assert all(o.get("skip") for o in ops2)
    assert apply_plan(conn, ops2) == 0
    assert _state(conn) == (RIGHT, "closed", "expired")


def test_guards_skip_when_fill_already_relinked():
    # Fill already points at the right ticket → relink is skipped, not re-applied.
    conn = _db(fill_ticket=RIGHT)
    ops = _repair(conn)
    relink = next(o for o in ops if o["op"] == "relink_fill")
    assert relink.get("skip")


def test_guard_skips_close_when_not_filled():
    # Real position ticket already closed → close op is skipped.
    conn = _db(right_status="closed")
    ops = _repair(conn)
    close = next(o for o in ops if o["op"] == "close_position_ticket")
    assert close.get("skip")


def test_missing_rows_skip_cleanly():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE prop_fills (id INTEGER PRIMARY KEY, ticket_id TEXT, "
                 "symbol TEXT, status TEXT, pnl REAL)")
    conn.execute("CREATE TABLE prop_tickets (ticket_id TEXT PRIMARY KEY, status TEXT, "
                 "symbol TEXT, direction TEXT)")
    conn.commit()
    ops = _repair(conn)
    assert all(o.get("skip") for o in ops)
    assert apply_plan(conn, ops) == 0
