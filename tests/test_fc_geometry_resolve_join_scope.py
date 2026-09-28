"""The fc-geometry real arm must join the SAME strategy's trade, not the
nearest same-symbol close.

FIX-CA-27 / `CA-B02-fc-geometry-join-ignores-strategy` (code audit
2026-09-27). `_join_real_r` matched the soak row to the closed-trades table by
(symbol, nearest open time within 900s) only. Many strategies share a symbol
(9 on BTCUSDT in config/strategies.yaml), so two legs opening within the window
made the real arm of the live-vs-counterfactual comparison pick whichever
opened closest in time — possibly another strategy's trade.
"""
from __future__ import annotations

import sqlite3

from scripts.ml.fc_geometry_resolve import _join_real_r

WHEN = 1_780_000_000.0  # epoch seconds


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE trades (symbol TEXT, status TEXT, is_backtest INTEGER, "
        "entry_price REAL, stop_loss REAL, position_size REAL, pnl REAL, "
        "timestamp TEXT, created_at TEXT, strategy_name TEXT, account_id TEXT)")
    rows = [
        # Strategy B opened 60s from the soak row: NEAREST in time. R = -1.
        ("BTCUSDT", "closed", 0, 100.0, 99.0, 1.0, -1.0, _iso(WHEN + 60),
         None, "strat_b", "bybit_2"),
        # Strategy A opened 600s away — the soak row's own trade. R = +2.
        ("BTCUSDT", "closed", 0, 100.0, 99.0, 1.0, 2.0, _iso(WHEN + 600),
         None, "strat_a", "bybit_2"),
        # Strategy A on ANOTHER account, 30s away. R = +5.
        ("BTCUSDT", "closed", 0, 100.0, 99.0, 1.0, 5.0, _iso(WHEN + 30),
         None, "strat_a", "bybit_1"),
    ]
    con.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con


def test_positive_control_unscoped_join_picks_nearest_in_time():
    """Without scope the nearest close wins — the pre-fix behaviour, kept for
    soak rows that carry no strategy, and the reason the scoped case matters."""
    assert _join_real_r(_db(), "BTCUSDT", WHEN) == 5.0


def test_join_returns_the_same_strategy_and_account_row():
    got = _join_real_r(_db(), "BTCUSDT", WHEN,
                       strategy="strat_a", account_id="bybit_2")
    assert got == 2.0


def test_strategy_scope_alone_excludes_other_strategies():
    got = _join_real_r(_db(), "BTCUSDT", WHEN, strategy="strat_b")
    assert got == -1.0


def test_no_same_strategy_trade_in_window_is_unmatched_not_borrowed():
    assert _join_real_r(_db(), "BTCUSDT", WHEN, strategy="strat_c") is None
