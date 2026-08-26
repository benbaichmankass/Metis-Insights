"""`/api/bot/performance` says whether each exit path's own KEY is evidence.

WHY (GATE 0 / G1, `docs/claude/WORKPLAN-2026-08-26.md`).
`perExitPath` cuts PnL coverage BY EXIT PATH — and the path is
``trades.exit_reason``, the one field
``BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE`` shows wrong for the
majority of the rows it is applied to. The no-record close path hard-codes
``reconciler_filled`` before any price exists, and until #10262 nothing re-ran
the classifier once a price arrived. So the breakdown published a coverage
figure per bucket while the buckets themselves were partly fiction.

Measured on the live journal 2026-08-26 — population: ``trades`` rows with
``exit_reason='reconciler_filled'``, n=593, all ``status='closed'``, of 5,056
total rows — **562 of the 589 gradeable rows (95.4%) carried no
``exit_reason_source`` at all**, and only 53 rows in the whole table had ever
reached the classifier.

This publishes the four states as COUNTS. ⚠️ No ratio, deliberately: an AUTHORED
path (``sl_cross``, ``pairs_stop``, ``netting_attributed``) is written by the
producer that closed the trade and never reaches ``_classify_broker_exit``, so
unattested is the CORRECT state there. A single rate would imply one
denominator across paths that do not share one — the same read-a-number-off-the-
wrong-population error the gate exists to stop.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.web.api.routers.performance import _aggregate, _query

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


def _agg(tmp_path, rows):
    """rows = [(exit_reason, exit_price_source, exit_reason_source)]"""
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for i, (reason, px_src, label_src) in enumerate(rows, start=1):
        notes = {}
        if px_src:
            notes["exit_price_source"] = px_src
        if label_src:
            notes["exit_reason_source"] = label_src
        conn.execute(
            "INSERT INTO trades (id, account_id, strategy_name, symbol, direction,"
            " entry_price, stop_loss, position_size, pnl, status, is_backtest,"
            " is_demo, account_class, closed_at, timestamp, notes, exit_reason)"
            " VALUES (?,'bybit_2','s','BTCUSDT','long',100.0,99.0,1.0,1.0,"
            "'closed',0,0,'real_money',?,?,?,?)",
            [i, f"2026-07-{10 + i:02d}T12:00:00Z",
             f"2026-07-{10 + i:02d}T11:00:00Z", json.dumps(notes), reason],
        )
    conn.commit()
    conn.close()
    return _aggregate(_query(db, since=None), "all", None)


def _row(agg, path):
    return next(e for e in agg["perExitPath"] if e["exitPath"] == path)


@pytest.fixture()
def agg(tmp_path):
    return _agg(tmp_path, [
        # The reconciler-derived bucket: the classifier was MEANT to reach these.
        ("reconciler_filled", "bybit_closed_pnl", "price_vs_pkg_bracket"),
        ("reconciler_filled", "candle_at_close",  "price_vs_pkg_bracket_est_price"),
        ("reconciler_filled", "local_markprice",  "refused_unmeasured_price"),
        ("reconciler_filled", "bybit_closed_pnl", "unresolved"),
        ("reconciler_filled", "bybit_closed_pnl", None),   # never reached it
        ("reconciler_filled", "bybit_closed_pnl", None),
        # An AUTHORED path: its producer wrote the label; no classifier involved.
        ("sl_cross", "bybit_closed_pnl", None),
        ("sl_cross", "bybit_closed_pnl", None),
    ])


def test_the_four_states_are_reported_separately(agg):
    r = _row(agg, "reconciler_filled")
    assert r["labelAttestedCount"] == 2, "resolved, either price basis"
    assert r["labelRefusedCount"] == 1, "we LOOKED and declined"
    assert r["labelUnresolvedCount"] == 1, "we looked; price sat mid-bracket"
    assert r["labelUnattestedCount"] == 2, "the classifier never ran"


def test_the_partition_is_checkable_against_trades(agg):
    """The four sum to `trades` on every path, so a reader can verify the
    partition instead of trusting it."""
    for e in agg["perExitPath"]:
        total = (e["labelAttestedCount"] + e["labelRefusedCount"]
                 + e["labelUnresolvedCount"] + e["labelUnattestedCount"])
        assert total == e["trades"], e["exitPath"]


def test_refused_is_not_folded_into_unattested(agg):
    """The distinction the whole defect class was found through: 'we looked and
    declined' must stay separable from 'we never looked'. Collapsing them would
    destroy the absence semantics that made the 562/589 signature readable."""
    r = _row(agg, "reconciler_filled")
    assert r["labelRefusedCount"] == 1 and r["labelUnattestedCount"] == 2
    assert r["labelRefusedCount"] + r["labelUnattestedCount"] == 3, (
        "if these were ever merged this would be the only surviving number"
    )


def test_an_authored_path_is_fully_unattested_and_that_is_correct(agg):
    """`sl_cross` is written by the strategy monitor and never passes through
    `_classify_broker_exit`. 100% unattested here is the RIGHT answer, which is
    exactly why no ratio is published — a `labelCoverage` of 0.0 would read as
    a gap on a path that has none."""
    a = _row(agg, "sl_cross")
    assert a["labelUnattestedCount"] == a["trades"] == 2
    assert a["labelAttestedCount"] == 0
    assert "labelCoverage" not in a, "a rate would imply a denominator paths do not share"


def test_attestation_is_independent_of_pnl_coverage(agg):
    """The two grade different things: `pnlCoverage` grades the row's MONEY,
    the label counts grade the row's BUCKET KEY. Every row here carries broker
    truth, so coverage is high on a bucket whose membership is mostly unchecked
    — which is precisely the state that made the 08-21 plan's headline wrong."""
    r = _row(agg, "reconciler_filled")
    assert r["pnlCoverage"] > 0.5, "money is well measured"
    assert r["labelUnattestedCount"] > 0, "…while the label is not"
