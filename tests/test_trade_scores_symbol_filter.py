"""Symbol-filter behaviour for `/api/bot/trades/scores` (2026-05-19).

The trade↔shadow-record join filters by the trade's `symbol` when
the shadow record carries `feature_row.symbol`. This stops a
BTCUSDT prediction from being credited to an ETHUSDT trade just
because their open windows happened to overlap.

Records without `feature_row` (legacy log lines written before
2026-05-19) fall back to the original timestamp-only join — no
regression for old data sitting in the log.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from src.web.api import main as api_main  # noqa: E402
from src.web.api.routers import trade_scores as trade_scores_router  # noqa: E402


def _seed_db(db_path: Path, trades: list[dict]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                status TEXT,
                timestamp TEXT,
                notes TEXT,
                is_backtest INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS order_packages (
                id INTEGER PRIMARY KEY,
                linked_trade_id INTEGER,
                updated_at TEXT
            );
            """
        )
        for t in trades:
            conn.execute(
                "INSERT INTO trades (id, symbol, status, timestamp, notes, is_backtest) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (t["id"], t["symbol"], t["status"], t["opened_at"], json.dumps({})),
            )
            if t.get("closed_at"):
                conn.execute(
                    "INSERT INTO order_packages (linked_trade_id, updated_at) VALUES (?, ?)",
                    (t["id"], t["closed_at"]),
                )
        conn.commit()
    finally:
        conn.close()


def _write_shadow_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _record(
    *,
    ts: str,
    model_id: str,
    score: float,
    symbol: str | None = None,
    strategy_name: str | None = None,
) -> dict:
    rec: dict = {
        "predicted_at_utc": ts,
        "model_id": model_id,
        "stage": "shadow",
        "score": score,
        "row_keys": ["direction", "strategy_name", "symbol"],
    }
    if symbol is not None or strategy_name is not None:
        rec["feature_row"] = {}
        if strategy_name is not None:
            rec["feature_row"]["strategy_name"] = strategy_name
        if symbol is not None:
            rec["feature_row"]["symbol"] = symbol
    return rec


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "trade_journal.db"
    log = tmp_path / "shadow_predictions.jsonl"
    monkeypatch.setattr(trade_scores_router, "_DB_PATH", db)
    monkeypatch.setattr(trade_scores_router, "_SHADOW_LOG", log)
    return TestClient(api_main.app), db, log


def test_records_with_matching_symbol_are_joined(client):
    tc, db, log = client
    opened = "2026-05-19T04:00:00+00:00"
    closed = "2026-05-19T05:00:00+00:00"
    _seed_db(db, [
        {"id": 1, "symbol": "BTCUSDT", "status": "closed",
         "opened_at": opened, "closed_at": closed},
    ])
    _write_shadow_log(log, [
        _record(ts="2026-05-19T04:30:00+00:00", model_id="m-a", score=0.7,
                symbol="BTCUSDT", strategy_name="vwap"),
        _record(ts="2026-05-19T04:45:00+00:00", model_id="m-b", score=0.3,
                symbol="BTCUSDT", strategy_name="turtle_soup"),
    ])
    r = tc.get("/api/bot/trades/scores?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert len(body["trades"]) == 1
    trade = body["trades"][0]
    assert trade["symbol"] == "BTCUSDT"
    assert {s["model_id"] for s in trade["scores"]} == {"m-a", "m-b"}


def test_records_with_different_symbol_are_excluded(client):
    tc, db, log = client
    opened = "2026-05-19T04:00:00+00:00"
    closed = "2026-05-19T05:00:00+00:00"
    _seed_db(db, [
        {"id": 1, "symbol": "BTCUSDT", "status": "closed",
         "opened_at": opened, "closed_at": closed},
    ])
    _write_shadow_log(log, [
        _record(ts="2026-05-19T04:30:00+00:00", model_id="m-a", score=0.7,
                symbol="BTCUSDT"),
        _record(ts="2026-05-19T04:35:00+00:00", model_id="m-b", score=0.4,
                symbol="ETHUSDT"),  # wrong symbol — must be excluded
    ])
    r = tc.get("/api/bot/trades/scores?limit=10")
    body = r.json()
    trade = body["trades"][0]
    model_ids = {s["model_id"] for s in trade["scores"]}
    assert model_ids == {"m-a"}, (
        "ETHUSDT record must not join to a BTCUSDT trade"
    )


def test_legacy_records_without_feature_row_still_join(client):
    # Records written before 2026-05-19 don't carry `feature_row`.
    # The endpoint must fall back to the timestamp-window-only join
    # for those records — no regression for data already on disk.
    tc, db, log = client
    opened = "2026-05-19T04:00:00+00:00"
    closed = "2026-05-19T05:00:00+00:00"
    _seed_db(db, [
        {"id": 1, "symbol": "BTCUSDT", "status": "closed",
         "opened_at": opened, "closed_at": closed},
    ])
    _write_shadow_log(log, [
        _record(ts="2026-05-19T04:30:00+00:00", model_id="m-legacy", score=0.6),
    ])
    r = tc.get("/api/bot/trades/scores?limit=10")
    body = r.json()
    trade = body["trades"][0]
    assert {s["model_id"] for s in trade["scores"]} == {"m-legacy"}


def test_overlapping_trades_dont_cross_pollinate(client):
    # Two concurrent trades on different symbols. Each should land
    # only its own symbol's shadow records.
    tc, db, log = client
    opened = "2026-05-19T04:00:00+00:00"
    closed = "2026-05-19T05:00:00+00:00"
    _seed_db(db, [
        {"id": 1, "symbol": "BTCUSDT", "status": "closed",
         "opened_at": opened, "closed_at": closed},
        {"id": 2, "symbol": "ETHUSDT", "status": "closed",
         "opened_at": opened, "closed_at": closed},
    ])
    _write_shadow_log(log, [
        _record(ts="2026-05-19T04:10:00+00:00", model_id="m-btc", score=0.7,
                symbol="BTCUSDT"),
        _record(ts="2026-05-19T04:20:00+00:00", model_id="m-eth", score=0.3,
                symbol="ETHUSDT"),
    ])
    r = tc.get("/api/bot/trades/scores?limit=10")
    body = r.json()
    by_symbol = {t["symbol"]: t for t in body["trades"]}
    assert {s["model_id"] for s in by_symbol["BTCUSDT"]["scores"]} == {"m-btc"}
    assert {s["model_id"] for s in by_symbol["ETHUSDT"]["scores"]} == {"m-eth"}
