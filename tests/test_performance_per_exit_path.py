"""`/api/bot/performance` publishes PnL coverage per EXIT PATH.

WHY (2026-08-25, BL-20260825-EXIT-PROVENANCE-IS-STRUCTURED-BY-EXIT-PATH-SIX-
PATHS-AT-ZERO). Coverage was published per STRATEGY and nowhere per exit path,
so a path that had NEVER been measured could not be told from one merely below
a floor. Measured over all 1,347 closed non-backtest rows in the live journal
on 2026-08-25, SIX paths sat at 0.0% broker truth across 267 closes -- the whole
pairs sleeve (pairs_revert 44 / pairs_stop 40 / pairs_half_open_cleanup 31), the
whole intent-reduce path (intent_reduce 37, intent_reduce_executed 70 at 2.9%),
netting_attributed (22) and reconciler_incomplete (93) -- while the global
figure read 42.9%. An average of a 66.9% path and a 0.0% path describes neither.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.web.api.routers.performance import _aggregate, _empty, _query

# Same schema + harness as tests/test_performance_pnl_coverage.py, deliberately:
# it drives the REAL SQL and the REAL aggregation against a synthetic journal
# rather than mocking, so the optional-column select and the notes-JSON read are
# both actually exercised.
_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY, account_id TEXT, strategy_name TEXT, symbol TEXT,
    direction TEXT, entry_price REAL, stop_loss REAL, position_size REAL,
    pnl REAL, status TEXT, is_backtest INTEGER DEFAULT 0,
    is_demo INTEGER DEFAULT 0, account_class TEXT, setup_type TEXT,
    reconcile_status TEXT, exit_reason TEXT, closed_at TEXT, timestamp TEXT,
    notes TEXT
);
CREATE TABLE order_packages (
    order_package_id TEXT PRIMARY KEY, linked_trade_id INTEGER, updated_at TEXT
);
"""


def _mk_db(tmp_path, rows, with_exit_reason=True):
    """rows = [(strategy, pnl, exit_reason, exit_price_source)]"""
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    schema = _SCHEMA if with_exit_reason else _SCHEMA.replace(
        "    reconcile_status TEXT, exit_reason TEXT, closed_at TEXT",
        "    reconcile_status TEXT, closed_at TEXT")
    conn.executescript(schema)
    cols = ("id, account_id, strategy_name, symbol, direction, entry_price, "
            "stop_loss, position_size, pnl, status, is_backtest, is_demo, "
            "account_class, closed_at, timestamp, notes")
    ph = "?,?,?,?,'long',100.0,99.0,1.0,?,'closed',0,0,'real_money',?,?,?"
    if with_exit_reason:
        cols += ", exit_reason"
        ph += ",?"
    for i, (strategy, pnl, exit_reason, src) in enumerate(rows, start=1):
        notes = json.dumps({"exit_price_source": src}) if src else "{}"
        vals = [i, "bybit_2", strategy, "BTCUSDT", pnl,
                f"2026-07-{10 + i:02d}T12:00:00Z",
                f"2026-07-{10 + i:02d}T11:00:00Z", notes]
        if with_exit_reason:
            vals.append(exit_reason)
        conn.execute(f"INSERT INTO trades ({cols}) VALUES ({ph})", vals)
    conn.commit()
    conn.close()
    return db


def _agg(tmp_path, rows, **kw):
    return _aggregate(_query(_mk_db(tmp_path, rows, **kw), since=None), "all", None)


def _row(agg, path):
    return next(e for e in agg["perExitPath"] if e["exitPath"] == path)


@pytest.fixture()
def paths(tmp_path):
    return _agg(tmp_path, [
        # a MEASURED path (broker truth)
        ("vwap",  10.0, "sl_cross",   "bybit_closed_pnl"),
        ("vwap",  -5.0, "sl_cross",   "exchange_fill"),
        # a path that has NEVER been measured -- the finding
        ("pairs", -2.0, "pairs_stop", "candle_at_close"),
        ("pairs", -3.0, "pairs_stop", "candle_at_close"),
        ("pairs", -1.0, "pairs_stop", None),
    ])


def test_the_block_exists_and_is_keyed_by_exit_path(paths):
    assert {e["exitPath"] for e in paths["perExitPath"]} == {"sl_cross", "pairs_stop"}


def test_a_never_measured_path_reports_zero_coverage_over_a_stated_denominator(paths):
    """0.0 over n=3 is the claim. 0.0 alone is not -- which is exactly what the
    global figure could not express."""
    p = _row(paths, "pairs_stop")
    assert p["pnlCoverage"] == 0.0
    assert p["trades"] == 3, "coverage without its denominator is unreadable"
    assert p["pnlMeasuredCount"] == 0


def test_a_measured_path_is_distinguishable_from_it(paths):
    m = _row(paths, "sl_cross")
    assert m["pnlCoverage"] == 1.0
    assert m["pnlMeasuredCount"] == 2 and m["trades"] == 2


def test_the_global_figure_hides_the_split_which_is_why_this_exists(paths):
    """The whole point: one number averaging a 1.0 path and a 0.0 path."""
    assert paths["pnlCoverage"] == pytest.approx(2 / 5)
    per = {e["exitPath"]: e["pnlCoverage"] for e in paths["perExitPath"]}
    assert per == {"sl_cross": 1.0, "pairs_stop": 0.0}


def test_worst_coverage_sorts_FIRST_not_best_pnl(paths):
    """The point of the breakdown is the UNMEASURED paths; sorting by PnL
    buries them under whatever happens to be profitable."""
    assert paths["perExitPath"][0]["exitPath"] == "pairs_stop"


def test_estimated_is_not_counted_as_covered_but_is_published(paths):
    """ESTIMATED is deliberately NOT 'covered', and is published separately so
    the count and the measured SUM -- over different populations -- reconcile
    instead of reading as a contradiction (the trend_donchian_avax_4h shape)."""
    p = _row(paths, "pairs_stop")
    assert p["pnlEstimatedCount"] == 2, "candle_at_close is ESTIMATED"
    assert p["pnlCoverage"] == 0.0, "ESTIMATED must not raise coverage"
    # totalPnlMeasured sums MEASURED+ESTIMATED, so it is NOT zero here even
    # though coverage is. That asymmetry is the contract, not a bug.
    assert p["totalPnlMeasured"] == pytest.approx(-5.0)
    assert p["totalPnl"] == pytest.approx(-6.0)


def test_an_unrecorded_exit_reason_buckets_honestly(tmp_path):
    agg = _agg(tmp_path, [("vwap", 1.0, None, "bybit_closed_pnl")])
    assert _row(agg, "(unrecorded)")["trades"] == 1


def test_exit_reason_was_ALREADY_required_so_this_adds_no_schema_demand(tmp_path):
    """The breakdown does not tighten the endpoint's schema requirement.

    I first wrote the select behind the `avail` guard that `notes` and the R
    inputs use, and a legacy-schema test proved the guard CANNOT FIRE: the
    reset-flat exclusion (`_clean_trades.exclude_reset_flat_predicate`, appended
    to every query) already references `t.exit_reason`, so a table without the
    column could never serve this endpoint. The guard was removed rather than
    kept — one that cannot fire advertises a degradation path that does not
    exist. This test pins the real constraint so nobody "restores" the guard.
    """
    db = _mk_db(tmp_path, [("vwap", 7.0, "sl_cross", "bybit_closed_pnl")],
                with_exit_reason=False)
    with pytest.raises(sqlite3.OperationalError, match="exit_reason"):
        _query(db, since=None)


def test_the_empty_envelope_carries_the_key():
    """A consumer must not have to branch on the key's absence."""
    assert _empty("all", None)["perExitPath"] == []
