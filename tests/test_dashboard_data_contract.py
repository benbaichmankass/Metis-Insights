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
