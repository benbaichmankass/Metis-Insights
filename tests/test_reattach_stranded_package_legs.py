"""Planted controls for the audit F-39 re-attach repair (Tier-2 money-DB write).

The repair flips three ``order_packages`` rows back to ``open`` so
``order_monitor`` can select them again. What makes that safe is not the write
-- it is the set of conditions under which the script REFUSES. So these tests
are mostly refusals, and each plants the condition rather than asserting the
happy path twice.

⚠️ **THE REFUSAL THAT MATTERS MOST IS THE VENUE ONE.** Re-opening a package
over a position that no longer exists at the broker would hand the exit path a
phantom. And *we could not read the account* must refuse too: collapsing an
unreadable venue into "flat" would turn a read failure into a repair decision,
which is the class ``collapsed-state-guard`` exists for.

Nothing here touches a broker. The script has no order path at all -- that is
the operator's stated scope on
``WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS``.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ops" / "reattach_stranded_package_legs.py"

# The live shape, transcribed from the 2026-09-10T01:0xZ reads.
_PKGS = [
    ("pkg-32a5162bbbdd4ef6", "spy_trend_long_1d", "SPY", "closed",
     4347, "exchange_flat_reconciled"),
    ("pkg-785d9a600c894c81", "tlt_pullback_1d", "TLT", "closed",
     5265, "exchange_flat_reconciled"),
    ("pkg-293021e2e84a48db", "xrp_pullback_2h", "XRPUSDT", "closed",
     5474, "reconciler_filled"),
]
_TRADES = [
    (4347, "alpaca_paper", "SPY", 11.0, "pkg-32a5162bbbdd4ef6"),
    (5265, "alpaca_paper", "TLT", 707.0, "pkg-785d9a600c894c81"),
    (5474, "bybit_2", "XRPUSDT", 58.5, "pkg-293021e2e84a48db"),
    (5475, "bybit_portfolio", "XRPUSDT", 11903.8, "pkg-293021e2e84a48db"),
]


def _db(tmp_path: Path) -> Path:
    """A fixture on the REAL schema, not a hand-rolled approximation.

    ``Database`` creates and migrates the canonical tables, so these tests
    exercise the same columns and constraints production has. A minimal
    hand-written schema passed the dry-run tests and then failed under
    ``--apply`` with ``no such column: created_at`` -- i.e. it was green while
    proving nothing about the write path, which is exactly the "a new table on
    an empty test DB always passes" warning in the generation-discipline rules.
    """
    sys.path.insert(0, str(REPO))
    from src.units.db.database import Database

    p = tmp_path / "journal.db"
    Database(str(p))  # creates + migrates the canonical schema
    c = sqlite3.connect(p)
    now = "2026-09-10T01:00:00+00:00"
    for pid, strat, sym, st, linked, reason in _PKGS:
        c.execute(
            "INSERT INTO order_packages (order_package_id, strategy_name, "
            " symbol, direction, entry, sl, tp, created_at, updated_at, status,"
            " linked_trade_id, close_reason) "
            "VALUES (?,?,?,'long',1.0,0.9,1.2,?,?,?,?,?)",
            (pid, strat, sym, now, now, st, linked, reason))
    for tid, acct, sym, size, pid in _TRADES:
        c.execute(
            "INSERT INTO trades (id, account_id, symbol, direction, status, "
            " position_size, order_package_id, is_backtest, created_at, "
            " timestamp, entry_price) "
            "VALUES (?,?,?,'long','open',?,?,0,?,?,1.0)",
            (tid, acct, sym, size, pid, now, now))
    c.commit()
    c.close()
    return p


def _venue(tmp_path: Path, **over) -> Path:
    accounts = {
        "alpaca_paper": [{"symbol": "SPY", "size": 11.0},
                         {"symbol": "TLT", "size": 707.0}],
        "bybit_2": [{"symbol": "XRPUSDT", "size": 58.5}],
        "bybit_portfolio": [{"symbol": "XRPUSDT", "size": 11903.8}],
    }
    accounts.update(over)
    payload = {"captured_at": "2026-09-10T01:10:41Z",
               "accounts": [{"account_id": k, "positions": v, "error": None}
                            if v is not None else
                            {"account_id": k, "positions": None,
                             "error": "could not read"}
                            for k, v in accounts.items()]}
    p = tmp_path / "exchange_positions.json"
    p.write_text(json.dumps(payload))
    return p


def _run(db: Path, venue: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--exchange-positions", str(venue), *extra],
        cwd=str(REPO), capture_output=True, text=True, timeout=120)


def test_plans_all_three_packages_and_writes_nothing(tmp_path: Path) -> None:
    db, venue = _db(tmp_path), _venue(tmp_path)
    r = _run(db, venue)
    assert r.returncode == 0, r.stderr
    assert "3 package(s) would be updated, 0 refused" in r.stdout
    assert "** REAL MONEY **" in r.stdout, "the bybit_2 leg must be called out"
    # DRY-RUN MEANS DRY: the DB is byte-unchanged.
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM order_packages "
                     "WHERE status='closed'").fetchone()[0] == 3
    c.close()


def test_refuses_a_leg_that_is_flat_at_the_venue(tmp_path: Path) -> None:
    """THE CENTRAL REFUSAL: a closed-at-the-venue leg is reconciled, not re-attached."""
    db = _db(tmp_path)
    venue = _venue(tmp_path, bybit_2=[])  # the real-money position is gone
    r = _run(db, venue)
    assert "FLAT at the venue" in r.stdout
    assert "reconciled as CLOSED, not" in r.stdout
    assert "2 package(s) would be updated, 1 refused" in r.stdout
    assert "pkg-293021e2e84a48db" not in r.stdout.split("REFUSED")[0]


def test_refuses_an_unreadable_account_rather_than_reading_it_as_flat(
        tmp_path: Path) -> None:
    """*We could not look* is not *there is no position*."""
    db = _db(tmp_path)
    venue = _venue(tmp_path, bybit_2=None)  # error set, positions absent
    r = _run(db, venue)
    assert "could NOT be read" in r.stdout
    assert "not evidence of flat" in r.stdout
    assert "1 refused" in r.stdout


def test_refuses_a_venue_size_that_disagrees_with_the_row(tmp_path: Path) -> None:
    db = _db(tmp_path)
    venue = _venue(tmp_path, bybit_2=[{"symbol": "XRPUSDT", "size": 1.0}])
    r = _run(db, venue)
    assert "but the venue holds 1" in r.stdout
    assert "1 refused" in r.stdout


def test_refuses_an_already_repaired_package(tmp_path: Path) -> None:
    """Safe to re-run: a package already back to `open` is refused, not rewritten."""
    db = _db(tmp_path)
    c = sqlite3.connect(db)
    c.execute("UPDATE order_packages SET status='open' "
              "WHERE order_package_id='pkg-785d9a600c894c81'")
    c.commit()
    c.close()
    r = _run(db, _venue(tmp_path))
    assert "already repaired or moved" in r.stdout
    assert "2 package(s) would be updated, 1 refused" in r.stdout


def test_refuses_when_the_open_legs_have_moved(tmp_path: Path) -> None:
    """If a sibling closed since this was written, the plan is stale — refuse."""
    db = _db(tmp_path)
    c = sqlite3.connect(db)
    c.execute("UPDATE trades SET status='closed' WHERE id=5475")
    c.commit()
    c.close()
    r = _run(db, _venue(tmp_path))
    assert "the book moved" in r.stdout
    assert "1 refused" in r.stdout


def test_apply_writes_and_preserves_the_prior_values(tmp_path: Path) -> None:
    db, venue = _db(tmp_path), _venue(tmp_path)
    r = _run(db, venue, "--apply")
    assert r.returncode == 0, r.stderr + r.stdout
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = {x["order_package_id"]: x for x in
            c.execute("SELECT * FROM order_packages")}
    c.close()
    for pid, *_ in _PKGS:
        assert rows[pid]["status"] == "open", pid
        assert rows[pid]["close_reason"] is None, pid
        meta = json.loads(rows[pid]["meta"])["reattach_repair"]
        # The record of what happened must survive the repair.
        assert meta["previous_status"] == "closed"
        assert meta["previous_close_reason"] in (
            "exchange_flat_reconciled", "reconciler_filled")
        assert meta["audit_finding"] == "F-39"
    assert json.loads(rows["pkg-293021e2e84a48db"]["meta"]
                      )["reattach_repair"]["reattached_open_legs"] == [5474, 5475]
