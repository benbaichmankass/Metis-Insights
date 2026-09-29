"""score_order_packages.py --emit-delta-only --by-trade (JC-SA-01, 2026-09-29).

The package-keyed pass missed two measured populations (diag journal pull
2026-09-29, closes since 2026-09-08T10:50Z, n=486): 50 fan-out siblings whose
package's linked_trade_id names another account's trade, and 4 ib_paper trades
sitting on a package graded `orphaned` months before they existed. These tests
plant both shapes and prove the by-trade pass grades them and nothing else.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "scripts", "ops", "score_order_packages.py")

_spec = importlib.util.spec_from_file_location("score_order_packages_bt", _SCRIPT)
sop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sop)


def _db(path):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE order_packages (
            order_package_id TEXT, strategy_name TEXT, symbol TEXT,
            direction TEXT, status TEXT, close_reason TEXT,
            linked_trade_id INTEGER, signal_logic TEXT,
            entry REAL, sl REAL, tp REAL, created_at TEXT)"""
    )
    con.execute(
        """CREATE TABLE trades (
            id INTEGER, account_id TEXT, order_package_id TEXT, status TEXT,
            is_backtest INTEGER, closed_at TEXT, pnl REAL, exit_price REAL,
            exit_reason TEXT, position_size REAL)"""
    )
    pk = [
        # fan-out package: linked to trade 1 only, trade 2 is the sibling
        ("pkg-fan", "ict_scalp_5m", "BTCUSDT", "long", "closed", None, 1,
         json.dumps({"confidence": 0.8}), 100.0, 98.0, 104.0, "2026-09-10T00:00:00+00:00"),
        # the stale-orphan shape: package graded long ago with no trade
        ("pkg-orph", "squeeze_breakout_4h", "MES", "short", "closed", None, 4,
         json.dumps({"confidence": 0.6}), 7600.0, 7647.0, 7500.0, "2026-06-02T12:10:41+00:00"),
        ("pkg-old", "vwap", "ETHUSDT", "long", "closed", None, 5,
         json.dumps({"deviation_std": 2.6}), 10.0, 9.0, 12.0, "2026-09-01T00:00:00+00:00"),
    ]
    con.executemany("INSERT INTO order_packages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", pk)
    tr = [
        (1, "bybit_2", "pkg-fan", "closed", 0, "2026-09-10T02:00:00+00:00", 5.0, 102.0, "tp_hit", 1.0),
        (2, "bybit_portfolio", "pkg-fan", "closed", 0, "2026-09-10T02:00:01+00:00", 50.0, 102.0, "tp_hit", 10.0),
        (3, "ib_paper", "pkg-orph", "closed", 0, "2026-09-16T23:52:42+00:00", -60.0, 7650.0, "sl_cross", 1.0),
        (4, "ib_paper", "pkg-orph", "closed", 0, "2026-09-17T01:27:44+00:00", -60.0, 7650.0, "sl_cross", 1.0),
        (5, "bybit_1", "pkg-old", "closed", 0, "2026-09-02T00:00:00+00:00", -1.0, 9.0, "sl_hit", 1.0),
        (6, "bybit_1", "pkg-fan", "open", 0, None, None, None, None, 1.0),
        (7, "bybit_1", "pkg-fan", "closed", 1, "2026-09-11T00:00:00+00:00", 1.0, 101.0, "tp_hit", 1.0),
        (8, "bybit_1", "pkg-missing", "closed", 0, "2026-09-12T00:00:00+00:00", 1.0, 1.0, "tp_hit", 1.0),
    ]
    con.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)", tr)
    con.commit()
    con.close()


def _scores(path):
    rows = [
        {"_meta": "header"},
        # package-keyed grade of the fan-out package: covers trade 1 only
        {"order_package_id": "pkg-fan", "linked_trade_id": 1, "reviewed_at": "2026-09-10T03:00:00+00:00"},
        # the stale orphan row: same package, NO trade
        {"order_package_id": "pkg-orph", "linked_trade_id": None, "status": "orphaned",
         "reviewed_at": "2026-06-23T12:09:29+00:00"},
    ]
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _run(tmp_path, *extra):
    db = str(tmp_path / "j.db")
    sc = str(tmp_path / "s.jsonl")
    _db(db)
    _scores(sc)
    before = open(sc).read()
    out = subprocess.run(
        [sys.executable, _SCRIPT, db, sc, "--emit-delta-only", "--by-trade", *extra],
        capture_output=True, text=True, check=True,
    )
    assert open(sc).read() == before, "by-trade delta mode must never write the score file"
    lines = [json.loads(x) for x in out.stdout.splitlines() if x.strip()]
    return [x for x in lines if not x.get("_delta_summary")], lines[-1]


def test_grades_fanout_sibling_and_stale_orphan_trades(tmp_path):
    rows, summary = _run(tmp_path, "--since", "2026-09-08T00:00:00Z")
    got = {r["linked_trade_id"] for r in rows}
    # 2 = fan-out sibling; 3, 4 = trades on the stale-orphan package.
    # 1 already graded; 5 closed before --since; 6 open; 7 backtest;
    # 8 has no package row (counted, not graded).
    assert got == {2, 3, 4}
    assert summary["keyed_on"] == "linked_trade_id"
    assert summary["emitted"] == 3
    assert summary["no_package"] == 1
    assert summary["by_account"] == {"bybit_portfolio": 1, "ib_paper": 2}
    for r in rows:
        assert r["keyed_on"] == "linked_trade_id"
        assert r["executed"] is True
        assert r["decision_grade"] in "ABCDF"
        assert r["exit_quality"] != "unknown"


def test_rubric_is_unchanged_for_the_trade(tmp_path):
    rows, _ = _run(tmp_path, "--since", "2026-09-08T00:00:00Z")
    sib = next(r for r in rows if r["linked_trade_id"] == 2)
    expect = sop._grade_package({
        "strategy_name": "ict_scalp_5m", "direction": "long", "status": "closed",
        "signal_logic": json.dumps({"confidence": 0.8}), "entry": 100.0, "sl": 98.0,
        "linked_trade_id": 2, "pnl": 50.0, "exit_price": 102.0, "exit_reason": "tp_hit",
    })
    for k, v in expect.items():
        assert sib[k] == v, k


def test_without_since_includes_older_ungraded_closes(tmp_path):
    rows, _ = _run(tmp_path)
    assert {r["linked_trade_id"] for r in rows} == {2, 3, 4, 5}


def test_limit_truncation_is_never_silent(tmp_path):
    rows, summary = _run(tmp_path, "--since", "2026-09-08T00:00:00Z", "--limit", "1")
    assert len(rows) == 1
    assert summary["truncated"] is True and summary["more_available"] == 2


def test_by_trade_requires_delta_mode(tmp_path):
    db = str(tmp_path / "j.db")
    _db(db)
    r = subprocess.run([sys.executable, _SCRIPT, db, str(tmp_path / "x.jsonl"), "--by-trade"],
                       capture_output=True, text=True)
    assert r.returncode == 2
