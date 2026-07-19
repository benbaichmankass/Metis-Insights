"""Tests for the harness-trade recorder bridge (S-MLOPT-S6-FU-2).

Verifies that standalone-harness trade JSONL maps to `is_backtest=1` rows the
`setup_candidates` `backtest_trades_db` source can read back.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.ml.record_harness_trades import harness_row_to_sim_trade, main

# Real-schema trades DDL (position_size NOT NULL) so the recorder's
# schema-adaptive insert is tested against the real constraint, mirroring
# tests/ml/test_backtest_labels.py.
_TRADES_DDL = (
    "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
    "symbol TEXT, direction TEXT, entry_price REAL, exit_price REAL, "
    "stop_loss REAL, take_profit_1 REAL, position_size REAL NOT NULL, "
    "setup_type TEXT, killzone TEXT, bias TEXT, entry_reason TEXT, "
    "exit_reason TEXT, pnl REAL, pnl_percent REAL, status TEXT, notes TEXT, "
    "is_backtest INT, strategy_name TEXT, account_id TEXT, is_demo INT, "
    "created_at TEXT)"
)


def test_mapper_prefers_net_r_and_normalises_direction():
    row = {
        "strategy": "trend_donchian", "entry_time": "2026-01-01T00:00:00+00:00",
        "direction": "long", "gross_r": 2.0, "net_r": 1.7, "confidence": 0.4,
    }
    m = harness_row_to_sim_trade(row, symbol="BTCUSDT")
    assert m is not None
    assert m["r_multiple"] == 1.7          # net_r preferred over gross_r
    assert m["direction"] == "long"
    assert m["strategy"] == "trend_donchian"
    assert m["symbol"] == "BTCUSDT"
    assert m["exit_ts"] == m["entry_ts"]   # exit ts falls back to entry ts


def test_mapper_falls_back_to_gross_r_and_default_strategy():
    row = {"entry_time": "2026-01-02T00:00:00+00:00", "direction": "sell", "gross_r": -0.8}
    m = harness_row_to_sim_trade(row, symbol="MES", default_strategy="fade")
    assert m is not None
    assert m["r_multiple"] == -0.8
    assert m["direction"] == "short"
    assert m["strategy"] == "fade"  # taken from default when row omits it


def test_mapper_explicit_override_wins_over_row_strategy():
    # ml-infra audit 2026-07-19: backtest_squeeze.py hardcodes
    # strategy="squeeze_breakout" in every emitted row while the live book's
    # name is squeeze_breakout_4h. The orchestrators pass the label via
    # `--trades-jsonl PATH=STRATEGY`, so the explicit override must WIN over
    # the row's self-reported name (previously the row field won, making the
    # override a silent no-op and mislabeling pooled rows).
    row = {
        "strategy": "squeeze_breakout", "entry_time": "2026-01-03T00:00:00+00:00",
        "direction": "long", "net_r": 0.5,
    }
    m = harness_row_to_sim_trade(row, symbol="BTCUSDT", default_strategy="squeeze_breakout_4h")
    assert m is not None
    assert m["strategy"] == "squeeze_breakout_4h"


def test_mapper_skips_unlabeled_or_tsless_rows():
    # No realized R -> skip (open / unlabeled trade).
    assert harness_row_to_sim_trade(
        {"entry_time": "2026-01-01T00:00:00+00:00", "direction": "long"},
        symbol="BTCUSDT",
    ) is None
    # No entry ts -> skip (can't be located on a bar series).
    assert harness_row_to_sim_trade(
        {"direction": "long", "net_r": 1.0}, symbol="BTCUSDT",
    ) is None


def test_cli_records_is_backtest_rows(tmp_path: Path, capsys):
    db = tmp_path / "backtest_trades.db"
    conn = sqlite3.connect(str(db))
    conn.execute(_TRADES_DDL)
    conn.commit()
    conn.close()

    trend = tmp_path / "trend.jsonl"
    trend.write_text("\n".join(json.dumps(r) for r in [
        {"strategy": "trend_donchian", "entry_time": "2026-01-01T01:00:00+00:00",
         "direction": "long", "net_r": 1.5},
        {"strategy": "trend_donchian", "entry_time": "2026-01-01T05:00:00+00:00",
         "direction": "short", "net_r": -1.0},
        {"strategy": "trend_donchian", "entry_time": "2026-01-01T09:00:00+00:00",
         "direction": "long"},  # unlabeled -> skipped
    ]) + "\n", encoding="utf-8")
    # A spec without =strategy: strategy comes from the row.
    squeeze = tmp_path / "squeeze.jsonl"
    squeeze.write_text(json.dumps(
        {"strategy": "squeeze", "entry_time": "2026-01-02T00:00:00+00:00",
         "direction": "buy", "net_r": 0.7}
    ) + "\n", encoding="utf-8")

    rc = main([
        "--db", str(db), "--symbol", "BTCUSDT",
        "--trades-jsonl", f"{trend}=trend_donchian",
        "--trades-jsonl", str(squeeze),
        "--run-tag", "test-run",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recorded_is_backtest_rows"] == 3   # 2 trend + 1 squeeze
    assert out["skipped_unlabeled"] == 1

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT direction, pnl, strategy_name, is_backtest, symbol, notes "
        "FROM trades ORDER BY timestamp"
    ).fetchall()
    conn.close()
    assert len(rows) == 3
    assert all(r["is_backtest"] == 1 for r in rows)
    assert all(r["symbol"] == "BTCUSDT" for r in rows)
    assert all(r["notes"] == "test-run" for r in rows)
    # pnl carries realized R (the recorder's R proxy) -> won = pnl > 0.
    by_strat = {(r["strategy_name"], r["direction"]): r["pnl"] for r in rows}
    # WC-3: the recorder now writes canonical long/short (not buy/sell).
    assert by_strat[("trend_donchian", "long")] == 1.5
    assert by_strat[("trend_donchian", "short")] == -1.0
    assert by_strat[("squeeze", "long")] == 0.7
