"""A repair that mutates a live bracket must leave a trace on the TRADE ROW.

Operator-approved 2026-08-24. Before this, ``_attempt_naked_autoprotect``
returned a bool and logged only on FAILURE, and ``modify_protective`` stamped
nothing — so "which trades had their protective bracket repaired?" had no
answer from the journal, which is exactly why the historical population is
already lost.

The tests that matter here are the ones that could FAIL: that `first_at` does
not move on a second repair, that an unrecognised label degrades loudly rather
than being stored verbatim, that a `call_failed` repair is still counted, and
that a stamp for a trade that does not exist inserts nothing.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.units.db.database import Database


@pytest.fixture()
def db(tmp_path):
    return Database(str(tmp_path / "j.db"))


def _mk_trade(db, trade_id: int = 1) -> int:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO trades (id, timestamp, symbol, direction, "
            "entry_price, position_size, status) "
            "VALUES (?,?,?,?,?,?,'open')",
            (trade_id, "2026-08-24T00:00:00Z", "MES", "long", 100.0, 1.0),
        )
        conn.commit()
        return trade_id
    finally:
        conn.close()


def _row(db, trade_id: int) -> sqlite3.Row:
    conn = db.connect()
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT protection_repairs, protection_repair_first_at, "
            "protection_repair_last_at, protection_repair_last_kind, "
            "protection_repair_last_verified FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()


def test_columns_exist_on_a_fresh_db(db):
    """The CREATE TABLE and the migration must agree — a fresh DB has them."""
    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    finally:
        conn.close()
    for c in ("protection_repairs", "protection_repair_first_at",
              "protection_repair_last_at", "protection_repair_last_kind",
              "protection_repair_last_verified"):
        assert c in cols, c


def test_migration_adds_the_columns_to_a_pre_existing_db(tmp_path):
    """A journal created before this change must gain the columns, not error."""
    from src.units.db.database import _migrate_add_protection_repair

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.commit()
    cur = conn.cursor()
    # The falsifier: it is genuinely absent first, so the True below is real.
    assert "protection_repairs" not in {
        r[1] for r in cur.execute("PRAGMA table_info(trades)")}
    assert _migrate_add_protection_repair(cur) is True
    assert "protection_repairs" in {
        r[1] for r in cur.execute("PRAGMA table_info(trades)")}
    # Idempotent: a second run is a no-op, not a duplicate-column error.
    assert _migrate_add_protection_repair(cur) is False
    conn.close()


def test_an_unstamped_trade_is_null_not_zero(db):
    """NULL is 'no repair recorded'. A back-filled 0 would assert an
    observation nobody made — the collapsed-state failure this repo names."""
    _mk_trade(db)
    r = _row(db, 1)
    assert r["protection_repairs"] is None
    assert r["protection_repair_first_at"] is None


def test_one_repair_stamps_count_kind_and_both_timestamps(db):
    _mk_trade(db)
    assert db.stamp_protection_repair(1, "naked_rearm") is True
    r = _row(db, 1)
    assert r["protection_repairs"] == 1
    assert r["protection_repair_last_kind"] == "naked_rearm"
    # The naked sweep adds no read-back, so it may claim no more than this.
    assert r["protection_repair_last_verified"] == "unverified"
    assert r["protection_repair_first_at"] == r["protection_repair_last_at"]


def test_first_at_never_moves_on_a_second_repair(db):
    """Anchor at FIRST observation — the netting reconciler's rule. A later
    repair rewriting when the first one happened would erase the evidence."""
    _mk_trade(db)
    db.stamp_protection_repair(1, "naked_rearm")
    first = _row(db, 1)["protection_repair_first_at"]
    db.stamp_protection_repair(1, "reassert", "both_legs_resting")
    r = _row(db, 1)
    assert r["protection_repairs"] == 2
    assert r["protection_repair_first_at"] == first
    assert r["protection_repair_last_at"] >= first
    assert r["protection_repair_last_kind"] == "reassert"
    assert r["protection_repair_last_verified"] == "both_legs_resting"


def test_a_failed_repair_is_still_counted(db):
    """These paths CANCEL before they place, so a failed repair is the state a
    reader most needs to find. A counter that dropped it would be quietest
    exactly when it mattered most."""
    _mk_trade(db)
    assert db.stamp_protection_repair(1, "reassert", "call_failed") is True
    r = _row(db, 1)
    assert r["protection_repairs"] == 1
    assert r["protection_repair_last_verified"] == "call_failed"


def test_stop_only_is_not_recorded_as_success(db):
    """BL-20260823-REASSERT-REPORTS-APPLIED-OK-ON-A-HALF-ARMED-BRACKET: a
    stop-only re-arm leaves a position that can stop out or run and cannot take
    profit. It must be readable as such."""
    _mk_trade(db)
    db.stamp_protection_repair(1, "reassert", "stop_only")
    assert _row(db, 1)["protection_repair_last_verified"] == "stop_only"


def test_an_unrecognised_kind_degrades_loudly_not_silently(db, caplog):
    """Storing a typo verbatim would make WHERE last_kind='reassert' miss the
    row silently. The COUNT stays right; only the label is degraded."""
    _mk_trade(db)
    with caplog.at_level("WARNING"):
        assert db.stamp_protection_repair(1, "reasssert") is True
    r = _row(db, 1)
    assert r["protection_repair_last_kind"] == "unknown_kind"
    assert r["protection_repairs"] == 1
    assert any("unrecognised kind" in m for m in caplog.messages)


def test_an_unrecognised_verified_state_falls_back_to_unverified(db, caplog):
    """'we did not look' is the fail-safe reading of an unknown outcome —
    never a success we cannot substantiate."""
    _mk_trade(db)
    with caplog.at_level("WARNING"):
        db.stamp_protection_repair(1, "reassert", "totally_fine")
    assert _row(db, 1)["protection_repair_last_verified"] == "unverified"
    assert any("unrecognised verified" in m for m in caplog.messages)


def test_a_stamp_for_a_missing_trade_inserts_nothing(db):
    """Never manufacture a row — the _stamp_telemetry_terminal rule."""
    assert db.stamp_protection_repair(4242, "naked_rearm") is False
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM trades WHERE id=4242").fetchone()[0] == 0
    finally:
        conn.close()


def test_two_trades_do_not_share_a_stamp(db):
    """A netted contract holds several trades; a repair on one must not read as
    a repair on its siblings."""
    _mk_trade(db, 1)
    _mk_trade(db, 2)
    db.stamp_protection_repair(1, "naked_rearm")
    assert _row(db, 1)["protection_repairs"] == 1
    assert _row(db, 2)["protection_repairs"] is None


# ── The WIRING half ──────────────────────────────────────────────────────────
#
# A writer nobody calls is the `exit_price_source` shape this repo already paid
# for (written in 12 files, branched on in one). These assert the stamp is
# actually reached from the repair paths, and that a future call site cannot
# quietly forget to thread `db` through.


def test_naked_autoprotect_stamps_the_trade_row(db, monkeypatch):
    """The end-to-end claim: a successful re-arm leaves a mark on the row."""
    import sqlite3 as _sq

    from src.bot import data_loaders
    from src.runtime import order_monitor
    from src.units.accounts import clients

    _mk_trade(db)
    conn = db.connect()
    try:
        conn.row_factory = _sq.Row
        row = conn.execute(
            "SELECT id, account_id, symbol, direction, position_size "
            "FROM trades WHERE id=1").fetchone()
    finally:
        conn.close()

    monkeypatch.setattr(
        data_loaders, "list_accounts",
        lambda: [{"account_id": row["account_id"], "exchange": "alpaca"}])

    class _Stub:
        def place_protective(self, order):
            return {"retCode": 0, "result": {"orderId": "x"}}

    monkeypatch.setattr(clients, "alpaca_client_for", lambda acc: _Stub())

    assert order_monitor._attempt_naked_autoprotect(row, 90.0, 110.0, db=db)
    r = _row(db, 1)
    assert r["protection_repairs"] == 1
    assert r["protection_repair_last_kind"] == "naked_rearm"
    # It re-armed WITHOUT reading the legs back, so it may claim no more.
    assert r["protection_repair_last_verified"] == "unverified"


def test_a_repair_without_a_db_handle_still_repairs(db, monkeypatch):
    """Prime Directive: re-arming a naked live position outranks recording it.
    Losing the stamp is acceptable; refusing the repair is not."""
    import sqlite3 as _sq

    from src.bot import data_loaders
    from src.runtime import order_monitor
    from src.units.accounts import clients

    _mk_trade(db)
    conn = db.connect()
    try:
        conn.row_factory = _sq.Row
        row = conn.execute(
            "SELECT id, account_id, symbol, direction, position_size "
            "FROM trades WHERE id=1").fetchone()
    finally:
        conn.close()
    monkeypatch.setattr(
        data_loaders, "list_accounts",
        lambda: [{"account_id": row["account_id"], "exchange": "alpaca"}])
    monkeypatch.setattr(
        clients, "alpaca_client_for",
        lambda acc: type("S", (), {
            "place_protective": lambda self, o: {"retCode": 0}})())

    assert order_monitor._attempt_naked_autoprotect(row, 90.0, 110.0) is True
    assert _row(db, 1)["protection_repairs"] is None  # skipped, not crashed


def test_every_autoprotect_call_site_threads_db():
    """A call site that forgets `db=` silently loses the stamp for that whole
    path — and the column would then read 'never repaired' for trades that
    were. Structural, so a new call site cannot quietly omit it."""
    import ast
    import pathlib

    src = pathlib.Path("src/runtime/order_monitor.py").read_text()
    calls = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_attempt_naked_autoprotect"
    ]
    assert calls, "probe found no call sites at all — it cannot be trusted"
    missing = [
        n.lineno for n in calls
        if "db" not in {k.arg for k in n.keywords if k.arg}
    ]
    assert not missing, f"_attempt_naked_autoprotect without db= at {missing}"


def test_the_ordinary_trailing_amend_is_not_stamped():
    """A strategy moving its own stop is the exit WORKING, not a repair.
    Counting those would put dozens of increments on a healthy trade and
    destroy the signal the column exists to carry."""
    import ast
    import pathlib

    src = pathlib.Path("src/runtime/order_monitor.py").read_text()
    tree = ast.parse(src)
    stamped = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_stamp_repair"):
                stamped.add(fn.name)
    assert stamped, "probe found no stamp sites — it cannot be trusted"
    assert "_apply_update" not in stamped, (
        "_apply_update routes the ordinary strategy-driven SL/TP move; "
        "stamping it would count intended trailing as repair")


def test_a_db_failure_raises_here_and_is_swallowed_one_layer_up(db, monkeypatch):
    """silent-empty-guard's rule, applied deliberately rather than waived.

    The writer must NOT swallow: a direct caller has to learn its write did not
    land, and catching at both layers would make a persistently-failing stamp
    silent everywhere. The swallow belongs in `order_monitor._stamp_repair`,
    the layer that must never break a live repair.
    """
    import sqlite3 as _sq

    from src.runtime import order_monitor

    _mk_trade(db)

    def _boom():
        raise _sq.OperationalError("database is locked")

    monkeypatch.setattr(db, "connect", _boom)

    with pytest.raises(_sq.OperationalError):
        db.stamp_protection_repair(1, "naked_rearm")

    # ...and the adapter absorbs it, so a repair path is never broken by it.
    order_monitor._stamp_repair(db, {"id": 1}, "naked_rearm")  # must not raise
