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


def test_signal_logic_and_meta_decoded_when_json(db, client):
    """signal_logic + meta (JSON TEXT columns) are decoded to objects so the
    dashboard's trade-detail card can render the bot's recorded reasoning."""
    tid = _trade(db, pnl=1.0)
    _insert_package(
        db, order_package_id="op-meta", linked_trade_id=tid,
        created_at="2026-05-20T10:00:00Z",
        signal_logic=json.dumps({"reason": "sweep + FVG", "bias": "bullish"}),
        meta=json.dumps({"setup_type": "turtle_soup", "killzone": "london"}),
    )
    row = client.get("/api/bot/order-packages").json()["rows"][0]
    assert row["signalLogic"] == {"reason": "sweep + FVG", "bias": "bullish"}
    assert row["meta"] == {"setup_type": "turtle_soup", "killzone": "london"}


def test_model_scores_decoded_when_json(db, client):
    """model_scores (the per-model ML decision captured at signal time) is
    projected as modelScores, decoded to a dict — a cheap SELECT instead of
    recompiling from the shadow-prediction log."""
    tid = _trade(db, pnl=1.0)
    _insert_package(
        db, order_package_id="op-ms", linked_trade_id=tid,
        created_at="2026-05-20T10:00:00Z",
        model_scores=json.dumps({
            "btc-regime-5m-baseline-v1": {"stage": "shadow", "score": 0.62},
            "btc-trade-outcome-v2": {"stage": "advisory", "score": 0.48},
        }),
    )
    row = client.get("/api/bot/order-packages").json()["rows"][0]
    assert row["modelScores"] == {
        "btc-regime-5m-baseline-v1": {"stage": "shadow", "score": 0.62},
        "btc-trade-outcome-v2": {"stage": "advisory", "score": 0.48},
    }


def test_model_scores_null_when_unset(db, client):
    """An order package with no captured scores serializes modelScores=None."""
    tid = _trade(db, pnl=1.0)
    _insert_package(db, order_package_id="op-noms", linked_trade_id=tid,
                    created_at="2026-05-20T10:00:00Z")
    row = client.get("/api/bot/order-packages").json()["rows"][0]
    assert row["modelScores"] is None


def test_signal_logic_plain_text_and_meta_null(db, client):
    """Plain-text signal_logic passes through as a string; an unset meta is
    None (not the literal string 'None')."""
    tid = _trade(db, pnl=1.0)
    _insert_package(
        db, order_package_id="op-txt", linked_trade_id=tid,
        created_at="2026-05-20T10:00:00Z",
        signal_logic="liquidity sweep below PDL",
    )
    row = client.get("/api/bot/order-packages").json()["rows"][0]
    assert row["signalLogic"] == "liquidity sweep below PDL"
    assert row["meta"] is None


# ---------------------------------------------------------------------------
# Trade resolution via the canonical trades.order_package_id fallback.
#
# linked_trade_id is written ONLY for the real-money primary OPEN entry.
# Packages whose fill was a demo leg / intent_reduce leg / orphan-adopt leave
# linked_trade_id NULL — those trades reference the package via the canonical
# trades.order_package_id column. The endpoint must still surface their PnL /
# status / accountClass while preserving one-row-per-package + paper filters.
# ---------------------------------------------------------------------------


def test_resolves_trade_via_order_package_id_when_linked_trade_id_null(db, client):
    """(a) linked_trade_id NULL but a real-money closed trade references the
    package via trades.order_package_id → the row now shows that trade's pnl /
    tradeStatus / accountClass='real_money' (previously null)."""
    _trade(
        db, pnl=33.0, status="closed", account_class="real_money", is_demo=0,
        order_package_id="op-fallback", strategy_name="vwap",
    )
    _insert_package(
        db, order_package_id="op-fallback", linked_trade_id=None,
        strategy_name="vwap", created_at="2026-05-20T10:00:00Z", status="closed",
    )
    body = client.get("/api/bot/order-packages").json()
    assert body["count"] == 1
    row = body["rows"][0]
    # linkedTradeId reflects the package's OWN declared link (still NULL) —
    # its documented meaning is unchanged. The PnL/status/accountClass come
    # from the resolved fallback trade.
    assert row["linkedTradeId"] is None
    assert row["pnl"] == 33.0
    assert row["tradeStatus"] == "closed"
    assert row["accountClass"] == "real_money"
    assert row["isDemo"] is False


def test_resolved_trade_prefers_open_leg_then_newest(db, client):
    """When multiple non-backtest legs reference the package via
    order_package_id, the deterministic rule (status='open' DESC, id DESC)
    picks the OPEN leg — matching what linked_trade_id would have pointed at."""
    # An earlier closed leg + a later open leg, both linked by order_package_id.
    _trade(db, pnl=10.0, status="closed", order_package_id="op-multi",
           account_class="real_money")
    _trade(db, pnl=None, status="open", order_package_id="op-multi",
           account_class="real_money")
    _insert_package(db, order_package_id="op-multi", linked_trade_id=None,
                    created_at="2026-05-20T10:00:00Z", status="open")
    body = client.get("/api/bot/order-packages").json()
    assert body["count"] == 1
    row = body["rows"][0]
    assert row["tradeStatus"] == "open"
    assert row["pnl"] is None  # the open leg has no realised PnL yet


def test_linked_trade_id_still_resolves_exactly_that_trade(db, client):
    """(b) When linked_trade_id IS set, resolution is unchanged — it resolves
    to that exact trade even if other order_package_id-linked legs exist."""
    primary = _trade(db, pnl=7.5, status="closed", strategy_name="vwap",
                     account_class="real_money")
    # A second leg referencing the same package by order_package_id that must
    # NOT win over the explicit linked_trade_id.
    _trade(db, pnl=99.0, status="open", order_package_id="op-linked",
           account_class="real_money")
    _insert_package(db, order_package_id="op-linked", linked_trade_id=primary,
                    strategy_name="vwap", created_at="2026-05-20T10:00:00Z",
                    status="closed")
    body = client.get("/api/bot/order-packages").json()
    assert body["count"] == 1
    row = body["rows"][0]
    assert row["linkedTradeId"] == str(primary)
    assert row["pnl"] == 7.5
    assert row["tradeStatus"] == "closed"


def test_fallback_paper_only_trade_excluded_then_included(db, client):
    """(c) A package whose only order_package_id-linked trade is PAPER is
    excluded by default and included with include_paper=true."""
    _trade(db, pnl=5.0, status="closed", is_demo=1, account_class="paper",
           order_package_id="op-paper")
    _insert_package(db, order_package_id="op-paper", linked_trade_id=None,
                    created_at="2026-05-20T10:00:00Z", status="closed")

    default_body = client.get("/api/bot/order-packages").json()
    assert default_body["count"] == 0  # resolved trade is paper → excluded

    incl_body = client.get("/api/bot/order-packages?include_paper=true").json()
    assert incl_body["count"] == 1
    row = incl_body["rows"][0]
    assert row["accountClass"] == "paper"
    assert row["isDemo"] is True
    assert row["pnl"] == 5.0


def test_fallback_package_with_no_trade_still_returned(db, client):
    """(d) A package with NO trade at all (truly unexecuted) still appears with
    null pnl — the NULL-tolerant backtest + not-paper predicates pass."""
    _insert_package(db, order_package_id="op-empty", linked_trade_id=None,
                    status="cancelled", created_at="2026-05-20T10:00:00Z")
    body = client.get("/api/bot/order-packages").json()
    assert body["count"] == 1
    row = body["rows"][0]
    assert row["pnl"] is None
    assert row["tradeStatus"] is None
    assert row["linkedTradeId"] is None
