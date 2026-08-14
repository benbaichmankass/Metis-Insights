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


def test_window_excludes_older_rows(tmp_path):
    db = _db(tmp_path, [("bybit_1", "old", "exchange_rejected", 30, 9)])
    assert audit(db, days=7)["legs_graded"] == 0
    assert audit(db, days=60)["dead_legs"] == 1


def test_backtest_rows_are_excluded(tmp_path):
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
    conn.commit()
    conn.close()

    assert audit(str(p), days=7)["legs_graded"] == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
