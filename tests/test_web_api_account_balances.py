"""GET /api/bot/accounts/balances tests.

Tier-1 read. **DB-authoritative (WC-5):** the canonical source is
``trade_journal.db::balance_snapshots`` (latest row per account); the legacy
``$RUNTIME_LOGS_DIR/balance_snapshots.json`` is a degraded fallback for the
window before the table is populated. The endpoint must never open an
exchange connection — it only reflects the persisted snapshot.

Pins:
  * Envelope shape (present, source, as_of, age_seconds, balances).
  * DB wins over JSON when the table has rows (``source == "db"``).
  * JSON fallback when the table is empty/absent (``source == "json_fallback"``).
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
    # Isolate the canonical DB to tmp so DB-authoritative reads + _seed_db
    # writes hit a per-test journal, never the real repo-root trade_journal.db.
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    from src.web.api import main as api_main

    return TestClient(api_main.app, raise_server_exceptions=False)


def _write_snapshot(tmp_path: Path, data: dict) -> None:
    d = tmp_path / "runtime_logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "balance_snapshots.json").write_text(json.dumps(data), encoding="utf-8")


def _seed_db(account_id: str, **kwargs) -> None:
    """Append a balance_snapshots row via the canonical writer. Resolves the
    same default DB path the endpoint reads (ICT_REPO_ROOT-scoped in tests)."""
    from src.units.db.database import Database

    db = Database()
    db.create_tables()
    db.insert_balance_snapshot(account_id, **kwargs)


class TestDbAuthoritative:
    def test_db_serves_and_wins_over_json(self, tmp_path, client):
        # JSON says one thing, DB says another — DB must win.
        _write_snapshot(tmp_path, {
            "bybit_1": {"balance": 1.0, "ts": "2020-01-01T00:00:00+00:00"},
        })
        _seed_db("bybit_1", balance=1234.56, delta_1h=3.2, open_positions=1,
                 api_ok=True, ts="2026-05-23T18:00:00+00:00")
        body = client.get("/api/bot/accounts/balances").json()
        assert body["source"] == "db"
        assert body["present"] is True
        assert body["balances"]["bybit_1"]["balance"] == 1234.56
        assert body["balances"]["bybit_1"]["delta_1h"] == 3.2
        assert body["balances"]["bybit_1"]["open_positions"] == 1
        assert body["balances"]["bybit_1"]["api_ok"] is True
        assert body["as_of"] == "2026-05-23T18:00:00Z"

    def test_latest_row_per_account(self, tmp_path, client):
        _seed_db("bybit_2", balance=100.0, ts="2026-05-23T17:00:00+00:00")
        _seed_db("bybit_2", balance=250.0, ts="2026-05-23T18:00:00+00:00")
        body = client.get("/api/bot/accounts/balances").json()
        assert body["source"] == "db"
        # Newest ts wins for the account.
        assert body["balances"]["bybit_2"]["balance"] == 250.0

    def test_api_ok_false_row_surfaces(self, tmp_path, client):
        _seed_db("ib_paper", balance=None, api_ok=False,
                 ts="2026-05-23T18:00:00+00:00")
        body = client.get("/api/bot/accounts/balances").json()
        assert body["source"] == "db"
        assert body["balances"]["ib_paper"]["balance"] is None
        assert body["balances"]["ib_paper"]["api_ok"] is False


class TestJsonFallback:
    def test_passthrough_and_envelope(self, tmp_path, client):
        _write_snapshot(tmp_path, {
            "bybit_1": {"balance": 1234.56, "ts": "2026-05-23T17:00:00+00:00"},
            "bybit_2": {"balance": 500.0, "ts": "2026-05-23T18:00:00+00:00"},
        })
        resp = client.get("/api/bot/accounts/balances")
        assert resp.status_code == 200
        body = resp.json()
        assert body["present"] is True
        assert body["source"] == "json_fallback"
        assert body["balances"]["bybit_1"]["balance"] == 1234.56
        assert body["balances"]["bybit_2"]["ts"] == "2026-05-23T18:00:00+00:00"
        # as_of tracks the NEWEST per-account ts.
        assert body["as_of"] == "2026-05-23T18:00:00Z"
        assert isinstance(body["age_seconds"], (int, float))

    def test_null_balance_passes_through(self, tmp_path, client):
        _write_snapshot(tmp_path, {"ib_paper": {"balance": None, "ts": None}})
        body = client.get("/api/bot/accounts/balances").json()
        assert body["present"] is True
        assert body["source"] == "json_fallback"
        assert body["balances"]["ib_paper"]["balance"] is None


class TestBestEffort:
    def test_missing_snapshot(self, client):
        body = client.get("/api/bot/accounts/balances").json()
        assert body == {
            "present": False, "source": "json_fallback",
            "as_of": None, "age_seconds": None, "balances": {},
        }

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
