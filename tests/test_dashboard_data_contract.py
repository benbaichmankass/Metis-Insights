"""S-061 — /api/bot/{stats,signals} null-on-missing contract.

Tracks ict-trading-bot#556. The dashboard side relies on `null` to mean
"writer didn't provide this field" — a fall-through to 0/"unknown" hides
real outages (e.g. the psutil snapshot crashed) and renders cosmetic
"unknown — conf 0.00" rows that look like real ICT signals.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import dashboard as dashboard_router


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the dashboard router at empty/known files so the existing
    on-disk audit log + DB don't leak into assertions."""
    audit = tmp_path / "signal_audit.jsonl"
    audit.touch()
    heartbeat = tmp_path / "heartbeat.txt"
    heartbeat.touch()
    bot_log = tmp_path / "bot.log"
    bot_log.touch()
    db = tmp_path / "trade_journal.db"
    monkeypatch.setattr(dashboard_router, "_AUDIT_LOG", audit)
    monkeypatch.setattr(dashboard_router, "_HEARTBEAT", heartbeat)
    monkeypatch.setattr(dashboard_router, "_BOT_LOG", bot_log)
    monkeypatch.setattr(dashboard_router, "_DB_PATH", db)
    return tmp_path


def test_vm_health_returns_none_per_field_when_psutil_fails(
    client: TestClient, isolate_paths: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """psutil ImportError / runtime failure must serialize as null per
    field — never a fabricated 0.0. The dashboard renders `—` on null
    but renders a real `0%` for a measured 0, so they must be distinct."""

    def _boom() -> dict[str, float | None]:
        # Re-implement the failure branch directly: monkeypatch the
        # helper to simulate psutil-missing.
        return {"cpu": None, "memory": None, "disk": None}

    monkeypatch.setattr(dashboard_router, "_vm_health", _boom)
    resp = client.get("/api/bot/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["vmHealth"] == {"cpu": None, "memory": None, "disk": None}


def test_get_signals_passes_through_null_for_missing_pattern_and_confidence(
    client: TestClient, isolate_paths: Path
) -> None:
    """Audit row written without pattern/confidence/price should
    serialize to null on the wire — not 'unknown' / 0 / 0."""
    audit = isolate_paths / "signal_audit.jsonl"
    # Two rows: one fully-populated, one missing pattern+confidence+price.
    rows = [
        {
            "event": "pipeline_result",
            "ts": "2026-05-09T10:00:00Z",
            "symbol": "BTCUSDT",
            "side": "buy",
            "signal_type": "fvg_bullish",
            "confidence": 0.82,
            "entry": 80000.0,
        },
        {
            "event": "pipeline_result",
            "ts": "2026-05-09T10:01:00Z",
            "symbol": "ETHUSDT",
            "side": "sell",
            # No pattern, no signal_type, no confidence, no price.
        },
    ]
    audit.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    resp = client.get("/api/bot/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2

    full = body[0]
    assert full["pattern"] == "fvg_bullish"
    assert full["confidence"] == 0.82
    assert full["price"] == 80000.0

    sparse = body[1]
    assert sparse["pattern"] is None
    assert sparse["confidence"] is None
    assert sparse["price"] is None


def test_get_signals_does_not_fabricate_confidence_zero(
    client: TestClient, isolate_paths: Path
) -> None:
    """Regression: the prior contract returned 0 for both 'really 0' and
    'missing'. A real 0.0 confidence reading must round-trip as 0.0, not
    silently collapse to None."""
    audit = isolate_paths / "signal_audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "ts": "2026-05-09T10:00:00Z",
                "symbol": "BTCUSDT",
                "side": "buy",
                "signal_type": "ob_bullish",
                "confidence": 0.0,
                "entry": 80000.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    resp = client.get("/api/bot/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["confidence"] == 0.0


# ---------------------------------------------------------------------------
# /api/bot/positions — schema regression guard
#
# The endpoint historically queried ``side`` and ``qty`` columns that
# don't exist on the canonical ``trades`` schema (the columns are
# ``direction`` and ``position_size``). The error was silently swallowed
# by a blanket ``except Exception``, so the dashboard's PositionsPanel
# rendered "No open positions" regardless of how many trades were live.
# These tests run the endpoint's real SQL against the canonical schema
# so any future drift fails loudly.
# ---------------------------------------------------------------------------


# S-067 follow-up #1: schema construction + insert helpers moved to
# tests/fixtures/real_schema_db.py so other endpoint tests can reuse
# the same canonical-schema materialisation. Re-exported under their
# legacy names below for backward compatibility — the original helpers
# only created the subset relevant to /positions; the shared fixture
# now creates the full schema (which is a strict superset, so all
# existing assertions still hold).
from tests.fixtures.real_schema_db import (  # noqa: E402
    insert_trade as _insert_trade,
    make_canonical_db as _make_canonical_trades_db,
)


def test_positions_returns_open_trade_against_canonical_schema(
    client: TestClient, isolate_paths: Path
) -> None:
    """Regression for the side/qty schema mismatch — the endpoint must
    return an open trade when the DB matches the canonical schema."""
    db = isolate_paths / "trade_journal.db"
    _make_canonical_trades_db(db)
    _insert_trade(
        db,
        timestamp="2026-05-09T10:00:00Z",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-09T10:00:00Z",
    )
    resp = client.get("/api/bot/positions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0] == {
        "id": "1",
        "account": "bybit_2",
        "symbol": "BTCUSDT",
        "side": "buy",  # direction='long' normalises to wire-side 'buy'
        "qty": 0.001,
        "entryPrice": 60000.0,
        "unrealizedPnl": 0.0,
        "openedAt": "2026-05-09T10:00:00Z",
    }


@pytest.mark.parametrize(
    "direction,expected_side",
    [("long", "buy"), ("short", "sell"), ("buy", "buy"), ("sell", "sell")],
)
def test_positions_normalises_side(
    client: TestClient, isolate_paths: Path, direction: str, expected_side: str
) -> None:
    db = isolate_paths / "trade_journal.db"
    _make_canonical_trades_db(db)
    _insert_trade(
        db, timestamp="2026-05-09T10:00:00Z", symbol="BTCUSDT",
        direction=direction, entry_price=60000.0, position_size=0.001,
        status="open", is_backtest=0, account_id="bybit_2",
    )
    resp = client.get("/api/bot/positions")
    assert resp.json()[0]["side"] == expected_side


@pytest.mark.parametrize(
    "writer_field",
    ["price", "entry_price", "entry"],
)
def test_signals_price_fallback_chain(
    client: TestClient, isolate_paths: Path, writer_field: str
) -> None:
    """The pipeline writes the entry price under different field names
    depending on the call site (src/runtime/pipeline.py:218, :524, :1142).
    The /signals reader must surface the price regardless of which alias
    the writer chose. Regression for the bug originally flagged in #627."""
    audit = isolate_paths / "signal_audit.jsonl"
    row = {
        "event": "pipeline_result",
        "ts": "2026-05-09T10:00:00Z",
        "symbol": "BTCUSDT",
        "side": "buy",
        "signal_type": "fvg_bullish",
        "confidence": 0.82,
        writer_field: 80000.0,
    }
    audit.write_text(json.dumps(row) + "\n", encoding="utf-8")
    resp = client.get("/api/bot/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["price"] == 80000.0


def test_positions_excludes_closed_and_backtest_trades(
    client: TestClient, isolate_paths: Path
) -> None:
    db = isolate_paths / "trade_journal.db"
    _make_canonical_trades_db(db)
    # Closed live trade — excluded.
    _insert_trade(
        db, timestamp="2026-05-09T09:00:00Z", symbol="BTCUSDT",
        direction="long", entry_price=60000.0, position_size=0.001,
        status="closed", is_backtest=0, account_id="bybit_2",
    )
    # Open backtest trade — excluded.
    _insert_trade(
        db, timestamp="2026-05-09T09:30:00Z", symbol="ETHUSDT",
        direction="short", entry_price=3000.0, position_size=0.1,
        status="open", is_backtest=1, account_id="backtest",
    )
    resp = client.get("/api/bot/positions")
    assert resp.json() == []
