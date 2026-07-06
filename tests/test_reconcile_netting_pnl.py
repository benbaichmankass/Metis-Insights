"""Tests for the netting-aware PnL reconciliation tool
(``scripts/ops/reconcile_netting_pnl.py``, operator-chosen validate-aggregate +
re-tag model).

Covers the pure pieces (no live DB): Bybit UM CSV → per-contract exchange truth;
journal-leg aggregation + orphan split; the per-symbol reconcile verdict
(within-tol → re-tag eligible, divergence → left untouched, no-truth → skip); and
the re-tag writeback against an in-memory sqlite journal.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.ops.reconcile_netting_pnl import (
    aggregate_journal_legs,
    apply_retag,
    parse_bybit_um_csv,
    reconcile,
)

_CSV = """Currency,Contract,Type,Direction,Quantity,Position,Filled Price,Funding,Fee Paid,Cash Flow,Change,Wallet Balance,Action,OrderId,TradeId,Time
USDT,ADAUSDT,TRADE,SELL,389,0,0.80,0,-0.15,9.43,9.28,300,CLOSE,o1,t1,2026-07-01 10:00:00.000
USDT,ADAUSDT,TRADE,BUY,389,389,0.78,0,-0.13,0,-0.13,290,OPEN,o2,t2,2026-06-01 10:00:00.000
USDT,ADAUSDT,SETTLEMENT,BUY,--,389,--,0.0006,0,0,0.0006,290,SETTLEMENT,o3,t3,2026-06-15 08:00:00.000
USDT,BTCUSDT,TRADE,SELL,0.005,0,62356,0,-0.17,-0.07,-0.24,280,CLOSE,o4,t4,2026-07-06 12:00:00.000
USDT,BTCUSDT,TRADE,BUY,0.005,0.005,62370,0,-0.17,0,-0.17,270,OPEN,o5,t5,2026-07-05 12:00:00.000
USDT,XRPUSDT,TRADE,SELL,30.8,95.1,1.11,0,-0.02,0.49,0.47,304,CLOSE,o6,t6,2026-07-06 13:02:00.000
USDT,ETHUSDT,SETTLEMENT,BUY,--,0.12,--,-0.0107,0,0,-0.0107,297,SETTLEMENT,o7,t7,2026-07-06 08:00:00.000
USDT,ETHUSDT,TRANSFER_IN,--,--,--,--,--,--,--,--,297,--,o8,t8,2026-04-15 11:26:47.349
"""


@pytest.fixture()
def csv_path(tmp_path):
    p = tmp_path / "bybit_um.csv"
    p.write_text(_CSV)
    return str(p)


# --- exchange truth ---------------------------------------------------------

def test_parse_csv_per_contract_truth(csv_path):
    truth = parse_bybit_um_csv(csv_path)
    assert set(truth) == {"ADAUSDT", "BTCUSDT", "XRPUSDT", "ETHUSDT"}

    ada = truth["ADAUSDT"]
    assert ada.gross_pnl == pytest.approx(9.43)   # only TRADE Cash Flow
    assert ada.fees == pytest.approx(-0.28)        # -0.15 + -0.13
    assert ada.funding == pytest.approx(0.0006)    # settlement row
    assert ada.close_count == 1 and ada.open_count == 1

    btc = truth["BTCUSDT"]
    assert btc.gross_pnl == pytest.approx(-0.07)
    assert btc.close_count == 1

    # ETH: no TRADE rows (only settlement + transfer) → zero realised, funding only.
    eth = truth["ETHUSDT"]
    assert eth.gross_pnl == pytest.approx(0.0)
    assert eth.funding == pytest.approx(-0.0107)
    assert eth.close_count == 0


def test_transfer_rows_do_not_contribute_pnl(csv_path):
    truth = parse_bybit_um_csv(csv_path)
    # The ETHUSDT TRANSFER_IN row must not add realised PnL.
    assert truth["ETHUSDT"].gross_pnl == pytest.approx(0.0)


# --- journal legs -----------------------------------------------------------

def _mem_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, "
        "status TEXT, pnl REAL, reconcile_status TEXT, setup_type TEXT, strategy_name TEXT)"
    )
    conn.executemany(
        "INSERT INTO trades (id, account_id, symbol, status, pnl, reconcile_status, setup_type, strategy_name) "
        "VALUES (:id,:account_id,:symbol,:status,:pnl,:reconcile_status,:setup_type,:strategy_name)",
        rows,
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn):
    return list(conn.execute(
        "SELECT id, account_id, symbol, status, pnl, reconcile_status, setup_type, strategy_name FROM trades"
    ))


def test_aggregate_splits_orphans_by_status_and_reconcile_status():
    conn = _mem_db([
        # two ADA legs that together should reconcile to +9.43
        {"id": 1, "account_id": "bybit_2", "symbol": "ADAUSDT", "status": "orphaned",
         "pnl": 5.0, "reconcile_status": "unreconciled", "setup_type": "x", "strategy_name": "a"},
        {"id": 2, "account_id": "bybit_2", "symbol": "ADAUSDT", "status": "closed",
         "pnl": 4.43, "reconcile_status": "unreconciled", "setup_type": "x", "strategy_name": "b"},
        # a healthy already-reconciled leg (not an orphan)
        {"id": 3, "account_id": "bybit_2", "symbol": "ADAUSDT", "status": "closed",
         "pnl": 0.0, "reconcile_status": "reconciled", "setup_type": "x", "strategy_name": "c"},
        # an orphan with NULL pnl
        {"id": 4, "account_id": "bybit_2", "symbol": "BTCUSDT", "status": "orphaned",
         "pnl": None, "reconcile_status": None, "setup_type": "x", "strategy_name": "d"},
    ])
    legs = aggregate_journal_legs(_rows(conn))

    ada = legs["ADAUSDT"]
    assert sorted(ada.leg_ids) == [1, 2, 3]
    assert sorted(ada.orphan_leg_ids) == [1, 2]   # id 3 is reconciled → not an orphan
    assert ada.pnl_sum == pytest.approx(9.43)     # 5.0 + 4.43 + 0.0

    btc = legs["BTCUSDT"]
    assert btc.orphan_leg_ids == [4]              # status='orphaned' counts even with recon NULL
    assert btc.orphan_null_pnl == 1


# --- reconcile verdicts -----------------------------------------------------

def test_reconcile_within_tol_marks_retag_eligible(csv_path):
    truth = parse_bybit_um_csv(csv_path)
    conn = _mem_db([
        {"id": 1, "account_id": "bybit_2", "symbol": "ADAUSDT", "status": "orphaned",
         "pnl": 9.30, "reconcile_status": "unreconciled", "setup_type": "x", "strategy_name": "a"},
    ])
    legs = aggregate_journal_legs(_rows(conn))
    res = {r.symbol: r for r in reconcile(truth, legs, tol=0.50)}
    # journal 9.30 vs exchange gross 9.43 → delta -0.13, within ±0.50 → OK.
    assert res["ADAUSDT"].within_tol is True
    assert res["ADAUSDT"].orphan_leg_ids == [1]


def test_reconcile_divergence_left_untouched(csv_path):
    truth = parse_bybit_um_csv(csv_path)
    conn = _mem_db([
        {"id": 1, "account_id": "bybit_2", "symbol": "ADAUSDT", "status": "orphaned",
         "pnl": 2.00, "reconcile_status": "unreconciled", "setup_type": "x", "strategy_name": "a"},
    ])
    legs = aggregate_journal_legs(_rows(conn))
    res = {r.symbol: r for r in reconcile(truth, legs, tol=0.50)}
    # 2.00 vs 9.43 → delta -7.43 → diverges.
    assert res["ADAUSDT"].within_tol is False


def test_reconcile_no_exchange_truth_is_skip():
    truth = {}
    conn = _mem_db([
        {"id": 1, "account_id": "bybit_2", "symbol": "SOLUSDT", "status": "orphaned",
         "pnl": 1.0, "reconcile_status": "unreconciled", "setup_type": "x", "strategy_name": "a"},
    ])
    legs = aggregate_journal_legs(_rows(conn))
    res = {r.symbol: r for r in reconcile(truth, legs, tol=0.50)}
    assert res["SOLUSDT"].exchange_gross is None
    assert res["SOLUSDT"].within_tol is False   # cannot validate → never auto-retag


# --- writeback --------------------------------------------------------------

def test_apply_retag_only_touches_given_ids():
    # apply_retag opens its own connection by path, so exercise the SQL on a
    # temp file (not an :memory: db, which wouldn't survive the reconnect).
    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    disk = sqlite3.connect(path)
    disk.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, "
        "status TEXT, pnl REAL, reconcile_status TEXT, setup_type TEXT, strategy_name TEXT)"
    )
    disk.executemany(
        "INSERT INTO trades (id, symbol, reconcile_status) VALUES (?,?,?)",
        [(1, "ADAUSDT", "unreconciled"), (2, "BTCUSDT", "unreconciled")],
    )
    disk.commit()
    disk.close()

    changed = apply_retag(path, [1])
    assert changed == 1

    check = sqlite3.connect(path)
    got = dict(check.execute("SELECT id, reconcile_status FROM trades").fetchall())
    check.close()
    os.unlink(path)
    assert got[1] == "reconciled"
    assert got[2] == "unreconciled"   # untouched


def test_apply_retag_empty_is_noop():
    assert apply_retag("/nonexistent.db", []) == 0
