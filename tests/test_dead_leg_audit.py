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

from scripts.ops.dead_leg_audit import _render, audit


def _db(tmp_path, rows, evals=None, pkgs=None):
    """rows: (account_id, strategy_name, status, days_ago, n[, entry_reason])

    *evals* seeds the `signals` dual-write: (strategy, days_ago, n[, side]).
    Left as None the table is ABSENT, which is the `unknown` eval-liveness case
    — the default here on purpose, so every pre-existing test keeps exercising
    the "we could not look" branch rather than silently acquiring a healthy one.

    ``side`` defaults to ``None`` (a non-actionable eval tick), so every
    pre-existing caller keeps meaning exactly what it meant: those rows are
    evaluations, not order requests, and must not start counting as actionable
    signals on the signal-vs-journal axis.

    *pkgs* seeds `order_packages`: (strategy_name, days_ago, n). Left as None
    the table is ABSENT, which makes the signal-vs-journal axis UNREADABLE
    rather than "zero packages" — the same we-did-not-look default.
    """
    p = tmp_path / "j.db"
    conn = sqlite3.connect(p)
    conn.execute(
        # `entry_reason` is REAL — it is in production's `trades` and the audit
        # reads it to separate a declared policy skip from a real refusal. A
        # test schema that omits a production column passes against a table
        # that does not exist (the shape that let the pairs `order_packages`
        # tests go green on a fictional PK for weeks).
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "strategy_name TEXT, status TEXT, entry_reason TEXT, is_backtest INT, "
        "created_at TEXT, timestamp TEXT)"
    )
    for row in rows:
        acct, strat, status, days_ago, n = row[:5]
        reason = row[5] if len(row) > 5 else None
        for _ in range(n):
            conn.execute(
                "INSERT INTO trades (account_id, strategy_name, status, entry_reason, "
                "is_backtest, created_at, timestamp) "
                "VALUES (?,?,?,?,0, datetime('now', ?), NULL)",
                (acct, strat, status, reason, f"-{days_ago} days"),
            )
    if evals is not None:
        conn.execute(
            "CREATE TABLE signals (id INTEGER PRIMARY KEY, logged_at_utc TEXT, "
            "strategy TEXT, symbol TEXT, side TEXT, qty REAL, status TEXT, "
            "reason TEXT, meta TEXT)"
        )
        for ev in evals:
            strat, days_ago, n = ev[:3]
            side = ev[3] if len(ev) > 3 else None
            for _ in range(n):
                conn.execute(
                    "INSERT INTO signals (logged_at_utc, strategy, side) VALUES "
                    "(datetime('now', ?) || '.0+00:00', ?, ?)",
                    (f"-{days_ago} days", strat, side),
                )
    if pkgs is not None:
        conn.execute(
            "CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY, "
            "strategy_name TEXT, created_at TEXT, status TEXT)"
        )
        for i, (strat, days_ago, n) in enumerate(pkgs):
            for j in range(n):
                conn.execute(
                    "INSERT INTO order_packages (order_package_id, strategy_name, "
                    "created_at) VALUES (?, ?, datetime('now', ?))",
                    (f"pkg-{i}-{j}", strat, f"-{days_ago} days"),
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
        # `entry_reason` is REAL — it is in production's `trades` and the audit
        # reads it to separate a declared policy skip from a real refusal. A
        # test schema that omits a production column passes against a table
        # that does not exist (the shape that let the pairs `order_packages`
        # tests go green on a fictional PK for weeks).
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "strategy_name TEXT, status TEXT, entry_reason TEXT, is_backtest INT, "
        "created_at TEXT, timestamp TEXT)"
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


# ---------------------------------------------------------------------------
# The offline report and the LIVE alert must grade the same rows the same way.
# ---------------------------------------------------------------------------

def test_declared_dry_run_skip_is_not_graded_a_dead_leg(tmp_path):
    """The divergence this whole module exists to prevent, found in its own sibling.

    `src/runtime/dead_leg.py` says the vocabulary lives in one place so the
    report cannot call a leg healthy while the alert calls it dead. But
    `bucket_for` grew a second parameter on 2026-08-24 (the declared-policy-skip
    bucket) and only ONE of the two callers was updated: the live alert passed
    `entry_reason`, this report did not — so it could never reach
    `policy_skipped`, and graded a deliberately-shelved `dry_run` account
    `signalled_never_placed`, the most alarming verdict it has, wearing a
    `real_money` label. Measured on the live journal 2026-08-25: 156 of
    `alpaca_live`'s 312 refusals carry the `dry_run_sizing_skip` token.
    """
    db = _db(tmp_path, [(
        "alpaca_live", "spy_pullback_1h", "rejected", 1, 9,
        "REJECTED: dry_run_sizing_skip: zero_balance: gate_balance=0.00 USD",
    )])
    leg = _leg(audit(db, days=7), "spy_pullback_1h")

    assert leg["verdict"] == "refusing_by_declaration"
    assert leg["policy_skipped"] == 9 and leg["refused"] == 0
    assert audit(db, days=7)["dead_legs"] == 0


def test_a_real_refusal_mixed_in_with_declared_skips_still_counts(tmp_path):
    """Suppression is per-ROW, never per-account. A switched-off account that
    also hits a genuine venue refusal must not have it swallowed."""
    db = _db(tmp_path, [
        ("alpaca_live", "spy_pullback_1h", "rejected", 1, 9,
         "REJECTED: dry_run_sizing_skip: zero_balance"),
        ("alpaca_live", "spy_pullback_1h", "exchange_rejected", 1, 2,
         "REJECTED: order_qty > max_qty"),
    ])
    leg = _leg(audit(db, days=7), "spy_pullback_1h")

    assert leg["policy_skipped"] == 9
    assert leg["refused"] == 2, "a genuine refusal must survive the suppression"
    assert leg["verdict"] == "signalled_never_placed"


def test_by_status_accumulates_across_distinct_reasons(tmp_path):
    """`entry_reason` joined the GROUP BY, so one status now spans several rows.

    The pre-existing `by_status[status] = n` kept only the LAST reason's count
    and silently under-reported every status with more than one reason — a
    regression the change itself would have introduced.
    """
    db = _db(tmp_path, [
        ("bybit_1", "x", "exchange_rejected", 1, 4, "reason A"),
        ("bybit_1", "x", "exchange_rejected", 1, 7, "reason B"),
    ])
    leg = _leg(audit(db, days=7), "x")

    assert leg["by_status"]["exchange_rejected"] == 11
    assert leg["refused"] == 11 and leg["total_rows"] == 11


# ---------------------------------------------------------------------------
# Evaluation liveness — a SEPARATE axis from the order verdict.
# ---------------------------------------------------------------------------

def test_absent_signals_table_is_unknown_never_healthy(tmp_path):
    """`SIGNAL_DUAL_WRITE_DISABLED` is a supported configuration. Reading its
    absence as "no leg ever evaluated" would alarm on the whole fleet; reading
    it as "evaluating" would report legs healthy off a table nobody read."""
    report = audit(_db(tmp_path, [("bybit_1", "x", "closed", 1, 3)]), days=7)

    assert report["eval_liveness_present"] is False
    assert _leg(report, "x")["eval_state"] == "unknown"
    assert report["strategies_not_evaluating"] == []


def test_eval_state_is_orthogonal_to_the_order_verdict(tmp_path):
    """The AVAX shape: a leg can be running perfectly AND placing nothing."""
    report = audit(
        _db(tmp_path,
            [("bybit_1", "ict_scalp_avax_5m", "exchange_rejected", 1, 12,
              "REJECTED: order_qty > max_qty")],
            evals=[("ict_scalp_avax_5m", 1, 40)]),
        days=7,
    )
    leg = _leg(report, "ict_scalp_avax_5m")

    assert leg["verdict"] == "signalled_never_placed"
    assert leg["eval_state"] == "evaluating"
    assert leg["evals_in_window"] == 40


def test_a_strategy_that_stopped_evaluating_is_found_with_no_trade_rows(tmp_path):
    """THE class the leg table structurally cannot reach.

    Legs are built from `trades` rows, so a strategy that stopped running
    produces no row, no leg and no line — it simply vanishes from the report,
    which is byte-identical to a strategy that ran all week and found no setup.
    This list is sourced from `signals` instead.
    """
    report = audit(
        _db(tmp_path,
            [("bybit_1", "live_leg", "closed", 1, 3)],
            evals=[("live_leg", 1, 20), ("ghost_leg_1h", 30, 60)]),
        days=7,
    )

    assert [s["strategy"] for s in report["strategies_not_evaluating"]] == ["ghost_leg_1h"]
    assert "ghost_leg_1h" not in [leg["strategy"] for leg in report["legs"]], (
        "the point of the list is that this strategy has NO leg line"
    )
    assert _leg(report, "live_leg")["eval_state"] == "evaluating"


def test_never_evaluated_is_kept_apart_from_stopped_evaluating(tmp_path):
    """Different owners: never-ran is a wiring question, stopped is a runtime one."""
    report = audit(
        _db(tmp_path,
            [("bybit_1", "wired_but_silent", "closed", 1, 2)],
            evals=[("someone_else", 1, 5)]),
        days=7,
    )

    assert _leg(report, "wired_but_silent")["eval_state"] == "never_evaluated"
    # never_evaluated is NOT reported as "stopped" — it never started.
    assert report["strategies_not_evaluating"] == []


def test_window_boundary_does_not_swallow_a_day_on_the_iso_separator(tmp_path):
    """`signals.logged_at_utc` is ISO (`T`, offset); `datetime('now',?)` is not.

    Compared raw, the two agree on the date and disagree on character 11
    (`T` 0x54 vs space 0x20), so every row on the boundary DATE would sort as
    in-window whatever its time. Both sides are normalised to the same 19-char
    shape; this pins that.
    """
    report = audit(
        _db(tmp_path,
            [("bybit_1", "old", "closed", 1, 2)],
            evals=[("old", 30, 10)]),
        days=7,
    )
    assert _leg(report, "old")["eval_state"] == "not_evaluating"
    assert _leg(report, "old")["evals_in_window"] == 0


# ---------------------------------------------------------------------------
# THIRD AXIS — "it signalled; did it journal anything?" (Lane P / P2)
#
# WHY THIS EXISTS, measured on the live journal 2026-08-30. `trend_donchian_sol`
# is enabled/live and routed to `bybit_1`. It emitted 144 actionable buy signals
# between 08-02 and 08-29; its most recent journal row of ANY kind is 06-29, two
# months earlier, and every one of its 7 trade rows is on `breakout_1` — it has
# never written a row on `bybit_1`. Nothing alerted for two months, because each
# existing check answers a DIFFERENT question and all three come back clean:
# `/health-review`'s silence check reads `*_eval` (it evaluates), the per-ACCOUNT
# refusal alert sees `bybit_1` placing fine for its other legs, and this audit's
# own leg table is built from `trades` rows so the leg is simply absent.
# ---------------------------------------------------------------------------


def test_a_leg_that_signals_into_a_void_is_found(tmp_path):
    """THE case this axis exists for — actionable signals, zero journal rows."""
    db = _db(
        tmp_path,
        [("bybit_1", "other_leg", "closed", 1, 3)],
        evals=[("trend_donchian_sol", 1, 144, "buy"),
               ("other_leg", 1, 5, "buy")],
        pkgs=[("other_leg", 1, 3)],
    )
    report = audit(db, days=7)

    found = {r["strategy"]: r for r in report["signal_journal"]}
    assert list(found) == ["trend_donchian_sol"]
    assert found["trend_donchian_sol"]["state"] == "signals_never_journaled"
    assert found["trend_donchian_sol"]["actionable_signals_in_window"] == 144
    assert found["trend_donchian_sol"]["trade_rows_in_window"] == 0
    assert found["trend_donchian_sol"]["order_package_rows_in_window"] == 0
    # It must be absent from every OTHER section — that absence is the bug.
    assert "trend_donchian_sol" not in [r["strategy"] for r in report["legs"]]


def test_the_silent_leg_is_invisible_to_the_order_verdict_axis(tmp_path):
    """Regression guard on the GAP, not just on the new grader.

    If a future change ever makes a zero-row leg appear in `legs`, this test
    fails and the author has to decide deliberately — rather than the two axes
    silently starting to double-report the same leg.
    """
    db = _db(tmp_path, [("bybit_1", "filler", "closed", 1, 2)],
             evals=[("silent", 1, 10, "buy")], pkgs=[])
    report = audit(db, days=7)

    assert [r["strategy"] for r in report["legs"]] == ["filler"]
    assert report["dead_legs"] == 0
    assert [r["strategy"] for r in report["signal_journal"]] == ["silent"]


def test_a_refused_leg_is_not_reported_here_it_has_an_owner(tmp_path):
    """A refusal IS a journal record. Reporting it here would re-report every
    refusing leg and bury the one leg that writes nothing at all."""
    db = _db(
        tmp_path,
        [("bybit_1", "refusing", "rejected", 1, 9)],
        evals=[("refusing", 1, 9, "buy")],
        pkgs=[],
    )
    report = audit(db, days=7)

    assert report["signal_journal"] == []
    assert _leg(report, "refusing")["verdict"] == "signalled_never_placed"


def test_order_packages_alone_count_as_journaling(tmp_path):
    """A leg that journals its DECISION and stops has reached the journal. It is
    a different question from this one, so it must not be flagged here."""
    db = _db(tmp_path, [("bybit_1", "filler", "closed", 1, 2)],
             evals=[("pkg_only", 1, 7, "sell")], pkgs=[("pkg_only", 1, 7)])
    report = audit(db, days=7)

    assert report["signal_journal"] == []


def test_non_actionable_evals_are_not_actionable_signals(tmp_path):
    """A breakout leg sitting inside its channel evaluates constantly and asks
    for nothing. `no_actionable_signals` is NOT health and is NOT a finding."""
    db = _db(tmp_path, [("bybit_1", "filler", "closed", 1, 2)],
             evals=[("in_channel", 1, 500)], pkgs=[])
    report = audit(db, days=7)

    assert report["signal_journal"] == []
    assert report["signal_journal_strategies_graded"] == 1


def test_actionable_signals_outside_the_window_do_not_flag(tmp_path):
    db = _db(tmp_path, [("bybit_1", "filler", "closed", 1, 2)],
             evals=[("old_leg", 90, 30, "buy")], pkgs=[])
    report = audit(db, days=7)

    assert report["signal_journal"] == []


def test_absent_order_packages_makes_the_axis_unreadable_not_clean(tmp_path):
    """`we could not look` must never render as `no leg signalled into a void`.

    Reading a missing table as "zero packages" would flag every signalling leg
    at once — a detector that cries wolf on its own blind spot.
    """
    db = _db(tmp_path, [("bybit_1", "filler", "closed", 1, 2)],
             evals=[("anything", 1, 10, "buy")], pkgs=None)
    report = audit(db, days=7)

    assert report["signal_journal_strategies_graded"] is None
    assert report["signal_journal"] == []
    assert "UNREADABLE" in _render(report)


def test_absent_signals_table_makes_the_axis_unreadable_not_clean(tmp_path):
    """The SIGNAL_DUAL_WRITE_DISABLED case. Reading its absence as "no leg
    signalled" would silence this axis for the entire fleet."""
    db = _db(tmp_path, [("bybit_1", "x", "closed", 1, 2)], evals=None, pkgs=[])
    report = audit(db, days=7)

    assert report["signal_journal_strategies_graded"] is None
    assert "UNREADABLE" in _render(report)


def test_the_denominator_is_reported_beside_the_finding(tmp_path):
    """A finding count over an unstated population is the error this repo keeps
    paying for. The render must carry `N of M`."""
    db = _db(tmp_path, [("bybit_1", "filler", "closed", 1, 2)],
             evals=[("silent", 1, 12, "buy"), ("quiet", 1, 4, None)], pkgs=[])
    report = audit(db, days=7)

    assert report["signal_journal_strategies_graded"] == 2
    assert "1 of 2 strategies graded" in _render(report)


def test_journaling_and_no_actionable_signal_are_counted_apart(tmp_path):
    """"Everything journals" and "almost nothing signalled" are opposite facts
    about how much this axis actually observed, and a bare finding count of
    zero looks identical in both. The render must separate them."""
    db = _db(tmp_path, [("bybit_1", "good", "closed", 1, 2)],
             evals=[("good", 1, 6, "buy"), ("in_channel", 1, 40, None)],
             pkgs=[("good", 1, 2)])
    report = audit(db, days=7)

    assert report["signal_journal_state_counts"] == {
        "journaling": 1, "no_actionable_signals": 1}
    rendered = _render(report)
    assert "1 journaling" in rendered
    assert "1 had no actionable signal" in rendered


def test_package_window_does_not_swallow_a_day_on_the_iso_separator(tmp_path):
    """`order_packages.created_at` is ISO-8601 with a `T`, while
    `datetime('now',?)` yields a space separator. Compared as RAW STRINGS they
    agree on the date and disagree at character 11 (`T` 0x54 vs space 0x20), so
    an OUT-OF-WINDOW package on the boundary DATE sorts as in-window.

    The direction matters and is why this is not cosmetic: an over-counted
    package makes a leg that journalled NOTHING in the window look like it
    journalled something, which SUPPRESSES the exact finding this axis exists
    to raise. Caught by `timestamp-comparison-guard` on the first draft.

    ⚠️ THE FIXTURE MUST SIT ON THE BOUNDARY DATE OR THE TEST IS VACUOUS. A
    package 30 or 90 days old is excluded by the buggy string compare too, so
    it pins nothing — verified by re-introducing the bug and watching such a
    test still pass. The row below is therefore placed ONE SECOND before the
    boundary instant, read back out of SQLite rather than computed here, so it
    shares the boundary's date whenever that date has any time-of-day at all.
    """
    db = _db(tmp_path, [("bybit_1", "filler", "closed", 1, 2)],
             evals=[("silent", 1, 20, "buy")], pkgs=[])
    conn = sqlite3.connect(db)
    boundary = conn.execute("SELECT datetime('now', '-7 days')").fetchone()[0]
    if boundary.endswith("00:00:00"):  # pragma: no cover - 1-in-86400 clock
        pytest.skip("boundary landed exactly on midnight; no same-date case exists")
    just_before = conn.execute(
        "SELECT datetime('now', '-7 days', '-1 seconds')").fetchone()[0]
    assert just_before[:10] == boundary[:10], "fixture must share the boundary date"
    conn.execute(
        "INSERT INTO order_packages (order_package_id, strategy_name, created_at) "
        "VALUES ('p-boundary', 'silent', ?)",
        (just_before.replace(" ", "T") + ".000000+00:00",),
    )
    conn.commit()
    conn.close()

    report = audit(db, days=7)

    found = {r["strategy"]: r for r in report["signal_journal"]}
    assert found["silent"]["order_package_rows_in_window"] == 0, (
        "an out-of-window package on the boundary DATE was counted as "
        "in-window, which would suppress this finding")
    assert found["silent"]["state"] == "signals_never_journaled"
