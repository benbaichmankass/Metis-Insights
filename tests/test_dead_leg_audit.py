"""The dead-leg audit must separate "never placed" from "never observed".

WHY (docs/research/WORKPLAN-2026-08-14.md Lane 0). A leg that evaluates, signals,
and has every order refused is invisible to both existing checks: `/health-review`'s
strategy-silence check measures `*_eval` events (it is not silent — it is loudly
failing at the last step) and `account_reachability_alert` probes `positions()`
(the account answers). This audit closes that gap, so its verdicts have to keep
the states apart or it reproduces the bug it exists to find.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.ops.dead_leg_audit import audit


def _db(tmp_path, rows):
    """rows: (account_id, strategy_name, status, days_ago, n)"""
    p = tmp_path / "j.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "strategy_name TEXT, status TEXT, is_backtest INT, created_at TEXT, "
        "timestamp TEXT)"
    )
    for acct, strat, status, days_ago, n in rows:
        for _ in range(n):
            conn.execute(
                "INSERT INTO trades (account_id, strategy_name, status, is_backtest, "
                "created_at, timestamp) VALUES (?,?,?,0, datetime('now', ?), NULL)",
                (acct, strat, status, f"-{days_ago} days"),
            )
    conn.commit()
    conn.close()
    return str(p)


def _leg(report, strategy):
    return next(r for r in report["legs"] if r["strategy"] == strategy)


def test_all_orders_refused_is_flagged_signalled_never_placed(tmp_path):
    """THE case: the AVAX shape — rows exist, none reached the exchange."""
    db = _db(tmp_path, [("bybit_1", "ict_scalp_avax_5m", "exchange_rejected", 1, 12)])
    report = audit(db, days=7)

    assert report["dead_legs"] == 1
    leg = _leg(report, "ict_scalp_avax_5m")
    assert leg["verdict"] == "signalled_never_placed"
    assert leg["placed"] == 0 and leg["refused"] == 12


def test_a_leg_with_no_rows_is_absent_not_healthy(tmp_path):
    """"We did not observe it" must never render as "it is fine". A quiet leg
    simply does not appear, and the population string says so."""
    db = _db(tmp_path, [("bybit_1", "noisy", "closed", 1, 3)])
    report = audit(db, days=7)

    assert [r["strategy"] for r in report["legs"]] == ["noisy"]
    assert "ABSENT, not healthy" in report["population"]


def test_healthy_and_dead_are_distinguishable_in_one_window(tmp_path):
    db = _db(tmp_path, [
        ("bybit_2", "good", "closed", 1, 5),
        ("bybit_1", "bad", "rejected", 1, 7),
    ])
    report = audit(db, days=7)

    assert _leg(report, "good")["verdict"] == "healthy"
    assert _leg(report, "bad")["verdict"] == "signalled_never_placed"
    assert report["dead_legs"] == 1


def test_partial_refusal_is_its_own_verdict_with_a_rate(tmp_path):
    """A leg that places SOME orders is not dead — collapsing it into either
    bucket would either hide a real degradation or cry wolf."""
    db = _db(tmp_path, [
        ("bybit_2", "mixed", "closed", 1, 3),
        ("bybit_2", "mixed", "exchange_rejected", 1, 7),
    ])
    report = audit(db, days=7)
    leg = _leg(report, "mixed")

    assert leg["verdict"] == "partially_refused"
    assert leg["refusal_rate"] == 0.7
    assert report["dead_legs"] == 0


def test_orphaned_counts_as_placed(tmp_path):
    """An orphan IS a position the journal lost — a different bug. Folding it
    into 'never placed' would blame order construction for a reconciler fault."""
    db = _db(tmp_path, [("ib_paper", "mgc_trend_1h", "orphaned", 1, 4)])
    report = audit(db, days=7)

    assert _leg(report, "mgc_trend_1h")["verdict"] == "healthy"
    assert report["dead_legs"] == 0


def test_unrecognised_status_is_not_silently_bucketed(tmp_path):
    """A new status must not quietly change every leg's verdict. It lands in
    `other` and gets its own verdict rather than being read as placed OR refused."""
    db = _db(tmp_path, [("bybit_1", "novel", "some_new_status", 1, 5)])
    report = audit(db, days=7)
    leg = _leg(report, "novel")

    assert leg["other"] == 5
    assert leg["placed"] == 0 and leg["refused"] == 0
    assert leg["verdict"] == "no_placed_rows_unrecognised_status_only"
    # Crucially NOT counted as a dead leg — we do not know that it failed.
    assert report["dead_legs"] == 0


def test_empty_journal_is_a_hard_stop_not_a_clean_report(tmp_path):
    """An EMPTY journal must refuse, never render as "no dead legs".

    Not hypothetical. Measured on the trainer VM 2026-08-14: the db-puller
    writes the real journal to `<repo>/data/trade_journal.db` (4649 rows) while
    `trade_journal_db_path()` there resolves to `<repo>/trade_journal.db` —
    ZERO rows, stale since 2026-08-02. This script trusts that resolver by
    design, so on that box it would query the empty file and print
    "legs graded: 0 / No signalled-never-placed legs" — a confident all-clear
    derived from the wrong file. That is the unasserted-denominator shape, and
    an audit that reports "nothing wrong" because it read nothing is worse than
    no audit at all.
    """
    db = _db(tmp_path, [])  # schema present, zero rows — the stray-journal shape

    with pytest.raises(SystemExit) as exc:
        audit(db, days=7)

    msg = str(exc.value)
    # Must name the path, so the reader can tell WHICH file was wrong...
    assert db in msg
    # ...and point at the actual trap rather than just saying "empty".
    assert "canonical-db-resolver" in msg
    assert "--db" in msg


def test_populated_journal_reports_its_denominator(tmp_path):
    """The negative-licensing count travels in the payload, not just the CLI.

    A JSON consumer that only sees `dead_legs: 0` cannot tell a clean system
    from an empty read; the denominator is what separates them.
    """
    db = _db(tmp_path, [("bybit_1", "s", "closed", 1, 3),
                        ("bybit_1", "old", "closed", 400, 2)])
    report = audit(db, days=7)

    # Counts the WHOLE table, not the window — it licenses the window's negatives.
    assert report["nonbacktest_rows_in_db"] == 5
    assert report["legs_graded"] == 1


def test_window_excludes_older_rows(tmp_path):
    db = _db(tmp_path, [("bybit_1", "old", "exchange_rejected", 30, 9)])
    assert audit(db, days=7)["legs_graded"] == 0
    assert audit(db, days=60)["dead_legs"] == 1


def test_backtest_rows_are_excluded(tmp_path):
    """A backtest row is not graded — proven against a LIVE row in the same DB.

    This test used to seed ONLY the backtest row and assert `legs_graded == 0`.
    That passed for an ambiguous reason: zero graded legs is equally what an
    EMPTY database produces, so the assertion could not distinguish "the
    backtest row was correctly excluded" from "nothing was read at all" — the
    same unasserted-denominator shape the audit's own hard-stop now guards
    against. Seeding a real row beside it makes the exclusion the only
    explanation for the result.
    """
    p = tmp_path / "j.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "strategy_name TEXT, status TEXT, is_backtest INT, created_at TEXT, "
        "timestamp TEXT)"
    )
    conn.execute(
        "INSERT INTO trades (account_id, strategy_name, status, is_backtest, "
        "created_at, timestamp) VALUES ('bybit_1','bt','exchange_rejected',1, "
        "datetime('now','-1 days'), NULL)"
    )
    # The positive control: a live row, so the probe is demonstrably able to
    # grade something in this window.
    conn.execute(
        "INSERT INTO trades (account_id, strategy_name, status, is_backtest, "
        "created_at, timestamp) VALUES ('bybit_1','live_leg','closed',0, "
        "datetime('now','-1 days'), NULL)"
    )
    conn.commit()
    conn.close()

    report = audit(str(p), days=7)

    graded = [r["strategy"] for r in report["legs"]]
    assert graded == ["live_leg"], "the backtest leg must not be graded"
    # And the denominator counts only the non-backtest row, so a reader can see
    # the exclusion happened rather than inferring it from an absence.
    assert report["nonbacktest_rows_in_db"] == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
