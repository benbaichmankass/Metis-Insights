"""/api/bot/performance trade-COUNT window (`last_n` / `blocks` -> `recentBlocks`)
and the unknown-window-token error.

R4 / MD-DEMOTE-S2-S1 (operator decision 2026-09-29, "Last 20 + strict test")
reads each Stage-2 leg's last two non-overlapping 20-trade windows from here.
Three properties matter, each tested:

  * absent `last_n`, the time-window payload is byte-identical to before;
  * each block is the SAME `_aggregate` a time window uses, over exactly that
    block's rows, newest block first;
  * an unknown window token is an explicit 400, never a silent all-time read
    (PI-20260929-VOLSKIP-SIGNAL-0006).
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.web.api.routers import performance as P


def _seed(db: Path, n_real: int, n_mirror: int) -> None:
    now = datetime.datetime(2026, 9, 29, 12, 0, 0)

    def iso(i: int) -> str:
        return (now - datetime.timedelta(hours=1000 - i)).strftime("%Y-%m-%dT%H:%M:%S")

    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE trades(id INTEGER PRIMARY KEY, strategy_name TEXT, symbol TEXT,
            pnl REAL, created_at TEXT, timestamp TEXT, closed_at TEXT, status TEXT,
            is_backtest INT, account_class TEXT, is_demo INT, account_id TEXT,
            exit_reason TEXT, reconcile_status TEXT);
        CREATE TABLE order_packages(order_package_id TEXT PRIMARY KEY, linked_trade_id INT, updated_at TEXT);
        """
    )
    rows = []
    tid = 0
    # real money: pnl = i (oldest 1 .. newest n_real), so each block's sum is known
    for i in range(1, n_real + 1):
        tid += 1
        rows.append((tid, "legA", "ADAUSDT", float(i), iso(i), iso(i), iso(i), "closed", 0,
                     "real_money", 0, "bybit_2", "tp"))
    for i in range(1, n_mirror + 1):
        tid += 1
        rows.append((tid, "legA", "ADAUSDT", -1.0, iso(i), iso(i), iso(i), "closed", 0,
                     "paper", 1, "bybit_portfolio", "sl"))
    conn.executemany(
        "INSERT INTO trades(id,strategy_name,symbol,pnl,created_at,timestamp,closed_at,"
        "status,is_backtest,account_class,is_demo,account_id,exit_reason) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "j.db"
    _seed(path, n_real=45, n_mirror=12)
    monkeypatch.setattr(P, "_DB_PATH", path)
    monkeypatch.setattr(P, "_portfolio_paper_account_ids", lambda: ["bybit_portfolio"])
    return path


def test_unknown_window_token_is_an_error_not_a_silent_all_time_read(db):
    with pytest.raises(HTTPException) as e:
        P.get_performance(window="90d")
    assert e.value.status_code == 400
    assert e.value.detail["error"] == "unknown_window"
    assert e.value.detail["accepted"] == sorted(P._WINDOWS)


@pytest.mark.parametrize("window", sorted(P._WINDOWS))
def test_time_window_output_is_unchanged_without_last_n(db, window, monkeypatch):
    # `since` is computed from the clock; pin it so two calls are comparable.
    fixed = {w: P._window_since(w) for w in P._WINDOWS}
    monkeypatch.setattr(P, "_window_since", lambda w: fixed[w])
    a = P.get_performance(window=window)
    b = P.get_performance(window=window, last_n=None)
    assert "recentBlocks" not in a
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def test_recent_blocks_are_the_last_non_overlapping_windows_newest_first(db):
    out = P.get_performance(window="all", last_n=20, blocks=2)
    rb = out["recentBlocks"]
    assert rb["blockSize"] == 20 and rb["blocks"] == 2
    real = rb["real"]["legA"]
    assert real["available"] == 45 and len(real["blocks"]) == 2
    # newest block = trades 26..45 (pnl 26..45), previous = 6..25
    assert real["blocks"][0]["trades"] == 20
    assert real["blocks"][0]["totalPnl"] == pytest.approx(sum(range(26, 46)))
    assert real["blocks"][1]["totalPnl"] == pytest.approx(sum(range(6, 26)))
    # the same _aggregate a time window uses, over exactly that block's rows
    rows = P._query(db, None, demo=False)
    same = P._aggregate(rows[25:45], "all", None)["perStrategy"][0]
    assert real["blocks"][0] == same
    # the time-window fields are untouched by the extra block
    assert {k: v for k, v in out.items() if k != "recentBlocks"} == P.get_performance(window="all")


def test_a_thin_book_reports_its_count_and_fewer_blocks(db):
    mirror = P.get_performance(window="all", last_n=20, blocks=2)["recentBlocks"]["mirror"]["legA"]
    assert mirror["available"] == 12 and mirror["blocks"] == []
