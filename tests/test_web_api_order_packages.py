"""GET /api/bot/order-packages — decision-level view for the dashboard.

Tier-1 read. Returns order_packages enriched with the linked trade's PnL
and the Claude decision score (comms/claude_strategy_scores.jsonl). Per-model
shadow scores are joined client-side from /api/bot/trades/scores, so they are
not asserted here.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import order_packages as op_router
from tests.fixtures.real_schema_db import (
    insert_order_package as _insert_package,
    insert_trade as _insert_trade,
    make_canonical_db,
)


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "trade_journal.db"
    make_canonical_db(path)
    monkeypatch.setattr(op_router, "_DB_PATH", path)
    # Default: no Claude scores file unless a test writes one.
    monkeypatch.setattr(op_router, "_CLAUDE_SCORES",
                        tmp_path / "claude_strategy_scores.jsonl")
    return path


def _trade(db, **over):
    """Insert a live trade, filling the NOT NULL columns the endpoint ignores."""
    fields = {
        "timestamp": "2026-05-20T10:00:00Z", "symbol": "BTCUSDT",
        "direction": "long", "entry_price": 60000.0, "position_size": 0.001,
        "status": "closed", "is_backtest": 0, "is_demo": 0,
    }
    fields.update(over)
    return _insert_trade(db, **fields)


def _write_claude(tmp_path, *rows):
    path = tmp_path / "claude_strategy_scores.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"_meta": {"schema": "v1"}}) + "\n")  # header row
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return path


def test_returns_package_with_pnl_and_claude_score(db, client, tmp_path, monkeypatch):
    tid = _trade(db, pnl=12.5, strategy_name="vwap")
    _insert_package(
        db, order_package_id="op-1", linked_trade_id=tid, strategy_name="vwap",
        symbol="BTCUSDT", direction="long", entry=60000.0, sl=59000.0, tp=62000.0,
        confidence=0.7, created_at="2026-05-20T10:00:00Z", status="closed",
        close_reason="tp",
    )
    monkeypatch.setattr(op_router, "_CLAUDE_SCORES", _write_claude(
        tmp_path,
        {"order_package_id": "op-1", "decision_grade": "B", "decision_grade_score": 0.75,
         "entry_quality": "good", "exit_quality": "ok", "risk_management": "good",
         "executed": True, "rationale": "clean mean-reversion entry", "reviewer": "claude",
         "reviewed_at": "2026-05-21T00:00:00Z"},
    ))

    resp = client.get("/api/bot/order-packages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["claude_log_present"] is True
    row = body["rows"][0]
    assert row["orderPackageId"] == "op-1"
    assert row["strategy"] == "vwap"
    assert row["pnl"] == 12.5
    assert row["linkedTradeId"] == str(tid)
    assert row["confidence"] == 0.7
    assert row["claudeScore"]["grade"] == "B"
    assert row["claudeScore"]["score"] == 0.75
    assert row["claudeScore"]["executed"] is True


def test_claude_score_null_when_unscored(db, client):
    tid = _trade(db, pnl=1.0)
    _insert_package(db, order_package_id="op-2", linked_trade_id=tid,
                    created_at="2026-05-20T10:00:00Z")
    body = client.get("/api/bot/order-packages").json()
    assert body["rows"][0]["claudeScore"] is None
    assert body["claude_log_present"] is False


def test_excludes_backtest_and_demo(db, client):
    bt = _trade(db, is_backtest=1)
    demo = _trade(db, is_demo=1)
    _insert_package(db, order_package_id="op-bt", linked_trade_id=bt,
                    created_at="2026-05-20T10:00:00Z")
    _insert_package(db, order_package_id="op-demo", linked_trade_id=demo,
                    created_at="2026-05-20T10:00:00Z")
    body = client.get("/api/bot/order-packages").json()
    assert body["count"] == 0


def test_strategy_filter(db, client):
    t1 = _trade(db, strategy_name="vwap")
    t2 = _trade(db, timestamp="2026-05-20T11:00:00Z", strategy_name="turtle_soup")
    _insert_package(db, order_package_id="op-v", linked_trade_id=t1,
                    strategy_name="vwap", created_at="2026-05-20T10:00:00Z")
    _insert_package(db, order_package_id="op-t", linked_trade_id=t2,
                    strategy_name="turtle_soup", created_at="2026-05-20T11:00:00Z")
    body = client.get("/api/bot/order-packages?strategy=vwap").json()
    assert body["count"] == 1
    assert body["rows"][0]["strategy"] == "vwap"


def test_unfilled_package_has_null_pnl(db, client):
    # An order package with no linked trade still appears (decision-level).
    _insert_package(db, order_package_id="op-nofill", linked_trade_id=None,
                    status="cancelled", created_at="2026-05-20T10:00:00Z")
    body = client.get("/api/bot/order-packages").json()
    assert body["count"] == 1
    row = body["rows"][0]
    assert row["pnl"] is None
    assert row["linkedTradeId"] is None
