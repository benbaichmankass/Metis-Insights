"""Tests for the prop manual-bridge inbound ingest (P2) + reconciliation (P3).

Covers the journal store, the ingest orchestrator (fill/close + account-status),
ticket↔fill reconciliation, rule-distance, and the REST router — all against an
isolated ``trade_journal.db``. The notification emitter is stubbed so no FCM /
Telegram I/O happens.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return db


@pytest.fixture
def no_notify(monkeypatch: pytest.MonkeyPatch) -> list:
    """Stub the prop fill emitter; record calls instead of doing I/O."""
    calls: list = []
    from src.prop import breakout_notify

    def _fake(fill, **kwargs):
        calls.append(fill)
        return {"push": True, "telegram": True}

    monkeypatch.setattr(breakout_notify, "emit_prop_fill", _fake)
    return calls


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


# ── journal store ─────────────────────────────────────────────────────

def test_journal_ticket_and_fill_roundtrip(isolated_db: Path) -> None:
    from src.prop import prop_journal

    prop_journal.record_ticket({
        "ticket_id": "prop-manual-abc123",
        "account_id": "breakout_1",
        "strategy": "trend_donchian_sol",
        "symbol": "SOLUSDT",
        "direction": "long",
        "entry": 73.32, "sl": 71.65, "tp": 80.57, "qty": 14.97,
        "message": "🟢 BUY SOLUSDT @ 73.32 SL 71.65 TP 80.57",
    })
    tickets = prop_journal.list_tickets(account_id="breakout_1")
    assert len(tickets) == 1
    assert tickets[0]["ticket_id"] == "prop-manual-abc123"
    assert tickets[0]["status"] == "emitted"
    # The rendered message round-trips so the dashboard can show it verbatim.
    assert tickets[0]["message"] == "🟢 BUY SOLUSDT @ 73.32 SL 71.65 TP 80.57"

    fid = prop_journal.insert_fill({
        "account_id": "breakout_1", "ticket_id": "prop-manual-abc123",
        "symbol": "SOLUSDT", "direction": "long", "status": "closed",
        "exit_price": 80.57, "pnl": 108.75,
    })
    assert fid > 0
    fills = prop_journal.list_fills(account_id="breakout_1")
    assert len(fills) == 1 and fills[0]["pnl"] == 108.75


def test_outbound_tickets_project_over_order_packages(isolated_db: Path) -> None:
    """Regression for the wiring bug: a prop ticket lives in order_packages
    (the canonical store). The 'tickets sent' view must surface it even when
    the prop_tickets sidecar has NO matching row (e.g. emitted before the
    sidecar existed) — i.e. don't read a parallel empty table."""
    from src.units.db.database import Database
    from src.prop import prop_journal

    db = Database()  # creates order_packages + the rest of the schema
    db.insert_order_package({
        "order_package_id": "pkg-test-sol",
        "strategy_name": "trend_donchian_sol",  # a breakout_1 prop strategy
        "symbol": "SOLUSDT", "direction": "long",
        "entry": 73.0, "sl": 71.0, "tp": 80.0, "status": "orphaned",
    })
    rows = prop_journal.list_outbound_tickets(account_id="breakout_1")
    by_id = {r["order_package_id"]: r for r in rows}
    assert "pkg-test-sol" in by_id, "canonical order_package must appear with no sidecar"
    r = by_id["pkg-test-sol"]
    assert r["symbol"] == "SOLUSDT"
    assert r["status"] == "orphaned"   # falls back to the order-package status
    assert r["message"] is None        # no sidecar message yet — still visible


def test_tables_absent_reads_are_empty(isolated_db: Path) -> None:
    from src.prop import prop_journal

    # Nothing written yet — reads must be graceful, not raise.
    assert prop_journal.list_fills() == []
    assert prop_journal.list_tickets() == []
    assert prop_journal.latest_account_status("breakout_1") is None


# ── ingest: fill / close ──────────────────────────────────────────────

def test_ingest_close_links_ticket_and_notifies(
    isolated_db: Path, no_notify: list
) -> None:
    from src.prop import prop_journal, prop_report

    prop_journal.record_ticket({
        "ticket_id": "prop-manual-xyz", "account_id": "breakout_1",
        "symbol": "SOLUSDT", "direction": "long", "entry": 73.0,
    })
    out = prop_report.ingest_report({
        "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
        "status": "closed", "exit_price": 80.5, "pnl": 100.0, "reason": "tp",
    })
    assert out["ok"] and out["kind"] == "fill"
    # Reconciliation linked the fill to the open ticket by symbol+direction.
    assert out["ticket_id"] == "prop-manual-xyz"
    # The close fired exactly one notification.
    assert len(no_notify) == 1 and no_notify[0]["status"] == "closed"
    # The ticket advanced to closed.
    assert prop_journal.list_tickets()[0]["status"] == "closed"


def test_ingest_placed_advances_to_placed_not_filled(
    isolated_db: Path, no_notify: list
) -> None:
    """A `placed` report (limit order on the terminal, not yet filled) must
    advance the ticket to `placed` — NOT `filled` — and fire no notification,
    because there is no live position yet. Then an `open`/`filled` report on the
    same ticket promotes it to `filled` and fires the fill notification."""
    from src.prop import prop_journal, prop_report

    prop_journal.record_ticket({
        "ticket_id": "prop-manual-placed", "account_id": "breakout_1",
        "symbol": "SOLUSDT", "direction": "long", "entry": 73.0,
    })
    out = prop_report.ingest_report({
        "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
        "status": "placed", "entry_price": 73.0, "qty": 1.0,
    })
    assert out["ok"] and out["status"] == "placed"
    # Ticket is at `placed`, NOT `filled`.
    assert prop_journal.list_tickets()[0]["status"] == "placed"
    # No fill notification for a working order.
    assert no_notify == []

    # The limit later trips → the operator reports the fill → ticket → filled.
    prop_report.ingest_report({
        "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
        "status": "open", "entry_price": 73.0, "qty": 1.0,
    })
    assert prop_journal.list_tickets()[0]["status"] == "filled"
    assert len(no_notify) == 1 and no_notify[0]["status"] == "open"


def test_ingest_fill_requires_symbol(isolated_db: Path, no_notify: list) -> None:
    from src.prop import prop_report

    with pytest.raises(ValueError):
        prop_report.ingest_report({"account_id": "breakout_1", "status": "closed"})


def test_ingest_requires_account(isolated_db: Path, no_notify: list) -> None:
    from src.prop import prop_report

    with pytest.raises(ValueError):
        prop_report.ingest_report({"symbol": "SOLUSDT", "status": "closed"})


# ── ingest: account status + rule distance ────────────────────────────

def test_ingest_account_status_rule_distance(isolated_db: Path) -> None:
    from src.prop import prop_report

    out = prop_report.ingest_report({
        "kind": "account_status", "account_id": "breakout_1",
        "balance": 5000.0, "equity": 4950.0,
        "realized_today": -30.0, "unrealized": -20.0,
    })
    assert out["ok"] and out["kind"] == "account_status"
    rd = out["rule_distance"]
    # $5k account, 3% daily = $150, 6% static DD floor = $4700.
    assert rd["static_dd_floor_usd"] == pytest.approx(4700.0)
    assert rd["distance_to_dd_floor_usd"] == pytest.approx(250.0)  # 4950 - 4700
    assert rd["daily_loss_limit_usd"] == pytest.approx(150.0)
    # day P&L = -50 → used 50 → distance 100.
    assert rd["distance_to_daily_loss_usd"] == pytest.approx(100.0)


# ── reconciliation: un-acted tickets ──────────────────────────────────

def test_unacted_ticket_detection(isolated_db: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from src.prop import prop_journal, prop_reconcile

    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    prop_journal.record_ticket({
        "ticket_id": "stale", "account_id": "breakout_1", "symbol": "SOLUSDT",
        "direction": "long", "valid_until": past, "status": "emitted",
    })
    prop_journal.record_ticket({
        "ticket_id": "fresh", "account_id": "breakout_1", "symbol": "ETHUSDT",
        "direction": "long", "valid_until": future, "status": "emitted",
    })
    unacted = prop_reconcile.find_unacted_tickets(account_id="breakout_1")
    ids = {t["ticket_id"] for t in unacted}
    assert "stale" in ids       # past validity, never filled
    assert "fresh" not in ids   # still within its window


def test_unacted_ticket_fill_match_is_account_scoped(isolated_db: Path) -> None:
    """A fill on account A must NOT mask an unacted ticket on account B with the
    SAME symbol+direction (the multi-account isolation invariant).

    Regression for S-AUDIT-F F1: the (symbol, direction) acted-key was built
    without the account, so on the global scan (account_id=None) a single
    account's fill suppressed every other account's same-symbol unacted ticket.
    """
    from datetime import datetime, timedelta, timezone

    from src.prop import prop_journal, prop_reconcile

    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    # Account B emitted a ticket that was never acted on (the drift we must catch).
    prop_journal.record_ticket({
        "ticket_id": "b-stale", "account_id": "breakout_2", "symbol": "SOLUSDT",
        "direction": "long", "valid_until": past, "status": "emitted",
    })
    # Account A filled the SAME symbol+direction (its own, unlinked ticket id).
    prop_journal.insert_fill({
        "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
        "status": "open", "entry_price": 80.0, "qty": 1.0,
    })

    # Global scan (the expiry-prompt path) must still surface account B's ticket.
    unacted = prop_reconcile.find_unacted_tickets(account_id=None)
    ids = {t["ticket_id"] for t in unacted}
    assert "b-stale" in ids, (
        "account A's fill masked account B's unacted same-symbol ticket — the "
        "acted-key is not account-scoped"
    )


# ── REST router ───────────────────────────────────────────────────────

def test_router_post_report_and_reads(
    client: TestClient, isolated_db: Path, no_notify: list
) -> None:
    r = client.post("/api/bot/prop/report", json={
        "account_id": "breakout_1", "symbol": "SOLUSDT", "direction": "long",
        "status": "closed", "pnl": 42.0, "exit_price": 80.0,
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    fills = client.get("/api/bot/prop/fills?account_id=breakout_1").json()
    assert fills["count"] == 1 and fills["fills"][0]["pnl"] == 42.0

    status = client.get("/api/bot/prop/status?account_id=breakout_1").json()
    assert status["account_id"] == "breakout_1"
    assert "rule_distance" in status

    recon = client.get("/api/bot/prop/reconcile?account_id=breakout_1").json()
    assert "summary" in recon and recon["summary"]["fills_total"] == 1


def test_router_post_rejects_bad_body(client: TestClient, isolated_db: Path) -> None:
    r = client.post("/api/bot/prop/report", json={"status": "closed"})  # no account
    assert r.status_code == 400


def test_router_post_token_gated(
    client: TestClient, isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "secret")
    r = client.post("/api/bot/prop/report", json={
        "account_id": "breakout_1", "symbol": "SOLUSDT", "status": "closed",
    })
    assert r.status_code == 401
    r2 = client.post(
        "/api/bot/prop/report",
        headers={"Authorization": "Bearer secret"},
        json={"account_id": "breakout_1", "symbol": "SOLUSDT", "status": "skipped"},
    )
    assert r2.status_code == 200, r2.text
