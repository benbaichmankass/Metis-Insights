"""GET /api/bot/accounts/balances tests.

Tier-1 read backed by ``$RUNTIME_LOGS_DIR/balance_snapshots.json`` (the
balances the trader already tracks via the hourly report). The endpoint
must never open an exchange connection — it only reflects the persisted
snapshot.

Pins:
  * Envelope shape (present, as_of, age_seconds, balances).
  * Per-account passthrough (balance + ts).
  * Newest ts drives as_of.
  * Best-effort: missing / malformed snapshot → present:false, empty.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path / "runtime_logs"))
    monkeypatch.setenv("ICT_REPO_ROOT", str(tmp_path))
    from src.web.api import main as api_main

    return TestClient(api_main.app, raise_server_exceptions=False)


def _write_snapshot(tmp_path: Path, data: dict) -> None:
    d = tmp_path / "runtime_logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "balance_snapshots.json").write_text(json.dumps(data), encoding="utf-8")


class TestHappyPath:
    def test_passthrough_and_envelope(self, tmp_path, client):
        _write_snapshot(tmp_path, {
            "bybit_1": {"balance": 1234.56, "ts": "2026-05-23T17:00:00+00:00"},
            "bybit_2": {"balance": 500.0, "ts": "2026-05-23T18:00:00+00:00"},
        })
        resp = client.get("/api/bot/accounts/balances")
        assert resp.status_code == 200
        body = resp.json()
        assert body["present"] is True
        assert body["balances"]["bybit_1"]["balance"] == 1234.56
        assert body["balances"]["bybit_2"]["ts"] == "2026-05-23T18:00:00+00:00"
        # as_of tracks the NEWEST per-account ts.
        assert body["as_of"] == "2026-05-23T18:00:00Z"
        assert isinstance(body["age_seconds"], (int, float))

    def test_null_balance_passes_through(self, tmp_path, client):
        _write_snapshot(tmp_path, {"ib_paper": {"balance": None, "ts": None}})
        body = client.get("/api/bot/accounts/balances").json()
        assert body["present"] is True
        assert body["balances"]["ib_paper"]["balance"] is None


class TestBestEffort:
    def test_missing_snapshot(self, client):
        body = client.get("/api/bot/accounts/balances").json()
        assert body == {"present": False, "as_of": None, "age_seconds": None, "balances": {}}

    def test_malformed_snapshot(self, tmp_path, client):
        d = tmp_path / "runtime_logs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "balance_snapshots.json").write_text("{not json", encoding="utf-8")
        body = client.get("/api/bot/accounts/balances").json()
        assert body["present"] is False
        assert body["balances"] == {}

    def test_non_dict_entries_skipped(self, tmp_path, client):
        _write_snapshot(tmp_path, {"bybit_1": "oops", "bybit_2": {"balance": 1.0, "ts": None}})
        body = client.get("/api/bot/accounts/balances").json()
        assert "bybit_1" not in body["balances"]
        assert body["balances"]["bybit_2"]["balance"] == 1.0
