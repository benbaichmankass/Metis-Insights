"""INV-2 must stop pressuring fabrication (2026-07-30).

As originally written, INV-2 flagged every closed row with a NULL ``pnl`` past
the sweep grace and never asked what KIND of number would clear it. The only
satisfying move was to put *something* in ``pnl`` — so
``_sweep_local_pnl_for_unpriced`` substituted a mark price taken hours after the
close, and the check went green on +$247,683.78 of manufactured money while a
correct, honest NULL would have stayed red forever.

An invariant whose only satisfying move is to invent data is not a safety net;
it is a forcing function pointed the wrong way.

These tests pin the corrected contract:

* silence (an undeclared NULL) still ALERTS — the relaxation is not a weakening;
* an EXPLICIT ``unmeasured`` declaration clears INV-2;
* every declared row is still COUNTED by INV-2b, so the marker can never be used
  to quietly mute the check — which is the loophole the design has to survive.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone


from src.runtime.provenance import UNMEASURED_MARKER


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_db_integrity", "scripts/check_db_integrity.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CDI = _load_module()

_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    account_id TEXT,
    symbol TEXT,
    status TEXT,
    pnl REAL,
    is_backtest INTEGER DEFAULT 0,
    account_class TEXT,
    order_package_id INTEGER,
    closed_at TEXT,
    created_at TEXT,
    timestamp TEXT,
    notes TEXT
);
CREATE TABLE order_packages (
    order_package_id TEXT PRIMARY KEY,
    linked_trade_id INTEGER,
    status TEXT,
    updated_at TEXT
);
"""

_NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
# Well past the default grace window so every row is in the alertable set.
_OLD = (_NOW - timedelta(hours=48)).isoformat()


_DB_SEQ = [0]


def _mk_db(tmp_path, rows):
    """rows = [(pnl, pnl_source_or_None)] — all closed, all past grace.

    A fresh file per call (the seq counter) so a test that builds several
    databases in a loop doesn't re-`executescript` over an existing one.
    """
    _DB_SEQ[0] += 1
    db = tmp_path / f"j{_DB_SEQ[0]}.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for i, (pnl, pnl_source) in enumerate(rows, start=1):
        notes = json.dumps({"pnl_source": pnl_source}) if pnl_source else "{}"
        conn.execute(
            "INSERT INTO trades (id, account_id, symbol, status, pnl, "
            "is_backtest, account_class, order_package_id, closed_at, "
            "created_at, timestamp, notes) "
            "VALUES (?,?,?,'closed',?,0,'real_money',?,?,?,?,?)",
            (i, "bybit_2", "BTCUSDT", pnl, i, _OLD, _OLD, _OLD, notes),
        )
        conn.execute(
            "INSERT INTO order_packages "
            "(order_package_id, linked_trade_id, status, updated_at)"
            " VALUES (?,?,'filled',?)", (str(i), i, _OLD),
        )
    conn.commit()
    conn.close()
    return db


def _checks(tmp_path, rows):
    report = CDI.run_checks(str(_mk_db(tmp_path, rows)),
                            window_hours=24 * 365, now=_NOW)
    return {c["id"]: c for c in report["checks"]}


# ------------------------------------------------------- silence still alerts
def test_undeclared_null_pnl_still_alerts(tmp_path):
    """The relaxation must NOT weaken the check. A NULL with no explanation is
    exactly what INV-2 exists to catch."""
    inv2 = _checks(tmp_path, [(None, None)])["INV-2"]
    assert inv2["total_count"] == 1
    assert inv2["alert"] is True


def test_unrecognised_pnl_source_does_not_clear_it(tmp_path):
    """Only the ONE canonical marker clears. A near-miss spelling must not."""
    for bogus in ("unmeasurable", "not_measured", "UNMEASURED", "unknown"):
        inv2 = _checks(tmp_path, [(None, bogus)])["INV-2"]
        assert inv2["total_count"] == 1, f"{bogus!r} wrongly cleared INV-2"


# --------------------------------------------------- declaration clears INV-2
def test_explicit_unmeasured_clears_inv2(tmp_path):
    """'We could not measure this, and we are saying so' is strictly better
    information than a plausible fabricated figure — and must not alert."""
    checks = _checks(tmp_path, [(None, UNMEASURED_MARKER)])
    assert checks["INV-2"]["total_count"] == 0
    assert checks["INV-2"]["alert"] is False


def test_a_populated_pnl_is_untouched_by_either_check(tmp_path):
    checks = _checks(tmp_path, [(12.5, "bybit_closed_pnl")])
    assert checks["INV-2"]["total_count"] == 0
    assert checks["INV-2b"]["total_count"] == 0


# ------------------------------------------------- the loophole must not exist
def test_declared_rows_are_still_counted_by_inv2b(tmp_path):
    """THE anti-loophole test. If declaring `unmeasured` made rows vanish from
    the report entirely, the marker would be a mute button and a growing
    unmeasured population would be invisible — which is exactly how the
    fabricated share reached 64.9% unnoticed."""
    checks = _checks(tmp_path, [(None, UNMEASURED_MARKER)] * 7)
    assert checks["INV-2"]["total_count"] == 0     # cleared…
    assert checks["INV-2b"]["total_count"] == 7    # …but fully visible
    assert checks["INV-2b"]["sample_ids"], "INV-2b must carry example ids"


def test_inv2b_reports_but_never_alerts(tmp_path):
    """An honest declaration is not a defect — it must not page anyone."""
    inv2b = _checks(tmp_path, [(None, UNMEASURED_MARKER)] * 3)["INV-2b"]
    assert inv2b["alert"] is False


def test_mixed_population_splits_correctly(tmp_path):
    checks = _checks(tmp_path, [
        (None, None),                  # undeclared -> INV-2
        (None, None),                  # undeclared -> INV-2
        (None, UNMEASURED_MARKER),     # declared   -> INV-2b
        (5.0, "bybit_closed_pnl"),     # measured   -> neither
    ])
    assert checks["INV-2"]["total_count"] == 2
    assert checks["INV-2b"]["total_count"] == 1


# ------------------------------------------------------------ robustness
def test_rows_with_no_notes_are_not_treated_as_declared(tmp_path):
    """`json_extract` returns NULL on missing/unparseable notes; that must read
    as 'undeclared', never as a declaration."""
    db = tmp_path / "j.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for i, notes in enumerate((None, "", "not json", "[1,2]"), start=1):
        conn.execute(
            "INSERT INTO trades (id, account_id, symbol, status, pnl, "
            "is_backtest, account_class, order_package_id, closed_at, "
            "created_at, timestamp, notes) "
            "VALUES (?,?,'BTCUSDT','closed',NULL,0,'real_money',?,?,?,?,?)",
            (i, "bybit_2", i, _OLD, _OLD, _OLD, notes),
        )
        conn.execute(
            "INSERT INTO order_packages "
            "(order_package_id, linked_trade_id, status, updated_at)"
            " VALUES (?,?,'filled',?)", (str(i), i, _OLD),
        )
    conn.commit()
    conn.close()
    report = CDI.run_checks(str(db), window_hours=24 * 365, now=_NOW)
    checks = {c["id"]: c for c in report["checks"]}
    assert checks["INV-2"]["total_count"] == 4
    assert checks["INV-2b"]["total_count"] == 0


def test_marker_has_exactly_one_spelling():
    """A second spelling would split the population and hide half of it."""
    assert CDI._DECLARED_UNMEASURED_MARKER == UNMEASURED_MARKER


def test_other_invariants_still_run(tmp_path):
    """The INV-2 edit must not have disturbed the rest of the report."""
    report = CDI.run_checks(str(_mk_db(tmp_path, [(1.0, "bybit_closed_pnl")])),
                            window_hours=24 * 365, now=_NOW)
    ids = {c["id"] for c in report["checks"]}
    for expected in ("INV-1", "INV-2", "INV-2b", "INV-3", "INV-4"):
        assert expected in ids, f"{expected} missing from the report"


def test_malformed_notes_do_not_abort_the_report(tmp_path):
    """REGRESSION: SQLite's `json_extract` RAISES on malformed JSON rather than
    returning NULL, so an unguarded extract in the INV-2 predicate aborted the
    ENTIRE integrity report with an OperationalError — turning a data-quality
    check into an outage. The live journal does contain such rows.

    Caught by the tests above during development; pinned here because the
    failure mode is silent in review (the predicate reads correct) and total in
    production.
    """
    db = tmp_path / "malformed.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for i, notes in enumerate(("", "not json", "{unclosed", "\x00"), start=1):
        conn.execute(
            "INSERT INTO trades (id, account_id, symbol, status, pnl, "
            "is_backtest, account_class, order_package_id, closed_at, "
            "created_at, timestamp, notes) "
            "VALUES (?,?,'BTCUSDT','closed',NULL,0,'real_money',?,?,?,?,?)",
            (i, "bybit_2", i, _OLD, _OLD, _OLD, notes),
        )
    conn.commit()
    conn.close()
    report = CDI.run_checks(str(db), window_hours=24 * 365, now=_NOW)
    checks = {c["id"]: c for c in report["checks"]}
    # Did not raise, AND the bad-notes rows read as UNDECLARED (the safe
    # direction — they stay in the alert set rather than being silently cleared).
    assert checks["INV-2"]["total_count"] == 4
    assert checks["INV-2b"]["total_count"] == 0
