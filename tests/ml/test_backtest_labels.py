"""Tests for backtest-augmented labels (S-MLOPT-S7).

Covers the `sim_trade_to_trade_row` mapper + `write_backtest_trades` writer, the
`source`/`include_backtest` extension on `trade_outcomes` + `setup_labels`, and
the source-based `live_holdout` split (train live+backtest, eval REAL only).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ml.datasets.backtest_recorder import (
    sim_trade_to_trade_row,
    write_backtest_trades,
)
from ml.datasets.families.setup_labels import SetupLabelsBuilder
from ml.datasets.families.trade_outcomes import TradeOutcomesBuilder
from ml.experiments.splitters import split_live_holdout

# A full trades-table DDL matching the live schema columns the families read.
_TRADES_DDL = (
    "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
    "symbol TEXT, direction TEXT, entry_price REAL, exit_price REAL, "
    "stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL, "
    "position_size REAL, setup_type TEXT, killzone TEXT, bias TEXT, "
    "entry_reason TEXT, exit_reason TEXT, pnl REAL, pnl_percent REAL, "
    "status TEXT, notes TEXT, is_backtest INT, strategy_name TEXT, "
    "account_id TEXT, is_demo INT, order_package_id INT, created_at TEXT)"
)


def _seed_live(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(_TRADES_DDL)
    # Two REAL (is_backtest=0) closed setup-tagged trades.
    conn.executemany(
        "INSERT INTO trades (timestamp, symbol, direction, setup_type, pnl, "
        "pnl_percent, status, is_backtest, is_demo, strategy_name, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2026-01-01T00:00:00Z", "BTCUSDT", "buy", "fvg", 10.0, 1.0,
             "closed", 0, 0, "ict_scalp", "2026-01-01T00:00:00Z"),
            ("2026-01-02T00:00:00Z", "BTCUSDT", "sell", "sweep", -5.0, -0.5,
             "closed", 0, 0, "ict_scalp", "2026-01-02T00:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()


def _sim_trades():
    return [
        {"strategy": "ict_scalp", "symbol": "BTCUSDT", "direction": "long",
         "entry_ts": "2026-02-01T00:00:00Z", "entry": 100.0, "sl": 99.0,
         "tp": 102.0, "exit_ts": "2026-02-01T05:00:00Z", "exit": 102.0,
         "exit_reason": "tp", "r_multiple": 2.0, "meta": {"setup_type": "fvg"}},
        {"strategy": "ict_scalp", "symbol": "BTCUSDT", "direction": "short",
         "entry_ts": "2026-02-02T00:00:00Z", "entry": 100.0, "sl": 101.0,
         "tp": 98.0, "exit_ts": "2026-02-02T03:00:00Z", "exit": 101.0,
         "exit_reason": "sl", "r_multiple": -1.0, "meta": {}},
        # open trade — no outcome, must be skipped
        {"strategy": "ict_scalp", "symbol": "BTCUSDT", "direction": "long",
         "entry_ts": "2026-02-03T00:00:00Z", "entry": 100.0, "sl": 99.0,
         "tp": 102.0, "exit_ts": None, "exit": None, "r_multiple": None},
    ]


def test_mapper_closed_and_open():
    rows = [sim_trade_to_trade_row(t, run_tag="bt1") for t in _sim_trades()]
    assert rows[2] is None  # open trade skipped
    win, loss = rows[0], rows[1]
    assert win["is_backtest"] == 1 and win["status"] == "closed"
    assert win["direction"] == "buy" and loss["direction"] == "sell"
    assert win["pnl"] == 2.0 and win["pnl_percent"] == 2.0  # risk_pct default 1.0
    assert win["setup_type"] == "fvg"          # from meta
    assert loss["setup_type"] == "ict_scalp"   # falls back to strategy
    assert win["account_id"] == "backtest" and win["notes"] == "bt1"


def test_writer_inserts_only_backtest_rows(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_live(db)
    n = write_backtest_trades(db, _sim_trades(), run_tag="bt1")
    assert n == 2  # 2 closed, 1 open skipped
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE is_backtest=1").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE is_backtest=0").fetchone()[0] == 2
    conn.close()


def test_trade_outcomes_include_backtest(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_live(db)
    write_backtest_trades(db, _sim_trades(), run_tag="bt1")
    b = TradeOutcomesBuilder()
    live_only = list(b.iter_rows(db_path=db))
    both = list(b.iter_rows(db_path=db, include_backtest=True))
    assert len(live_only) == 2 and all(r["source"] == "live" for r in live_only)
    assert len(both) == 4
    assert {r["source"] for r in both} == {"live", "backtest"}
    assert sum(1 for r in both if r["source"] == "backtest") == 2


def test_setup_labels_include_backtest(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_live(db)
    write_backtest_trades(db, _sim_trades(), run_tag="bt1")
    b = SetupLabelsBuilder()
    both = list(b.iter_rows(db_path=db, include_backtest=True))
    assert len(both) == 4  # all 4 have a non-empty setup_type
    bt = [r for r in both if r["source"] == "backtest"]
    assert len(bt) == 2
    # r_multiple recovers the sim R (pnl_percent / risk_pct, risk_pct default 1.0)
    rmults = sorted(r["r_multiple"] for r in bt)
    assert rmults == [-1.0, 2.0]


def test_source_based_live_holdout_split():
    rows = (
        [{"source": "backtest", "won": 1, "ts": f"b{i}"} for i in range(6)]
        + [{"source": "live", "won": 0, "ts": f"l{i}"} for i in range(3)]
    )
    train, ev = split_live_holdout(
        rows, {"live_flag_column": "source", "live_flag_true_value": "live",
               "time_column": "ts"},
    )
    assert len(train) == 6 and all(r["source"] == "backtest" for r in train)
    assert len(ev) == 3 and all(r["source"] == "live" for r in ev)
