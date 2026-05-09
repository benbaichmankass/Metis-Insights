"""S-051 — diag router auth + happy path."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import diag as diag_router

_TOKEN = "test-diag-token-not-a-real-secret"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")


@pytest.fixture
def client(env):
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def fake_runtime(tmp_path: Path, monkeypatch):
    runtime_logs = tmp_path / "runtime_logs"
    runtime_logs.mkdir()
    db_path = tmp_path / "trade_journal.db"
    audit = runtime_logs / "signal_audit.jsonl"
    status_json = runtime_logs / "status.json"
    heartbeat = runtime_logs / "heartbeat.txt"
    bot_log = tmp_path / "bot.log"

    monkeypatch.setattr(diag_router, "_DB_PATH", db_path)
    monkeypatch.setattr(diag_router, "_RUNTIME_LOGS", runtime_logs)
    monkeypatch.setattr(diag_router, "_AUDIT_LOG", audit)
    monkeypatch.setattr(diag_router, "_HEARTBEAT", heartbeat)
    monkeypatch.setattr(diag_router, "_STATUS_JSON", status_json)
    monkeypatch.setattr(diag_router, "_BOT_LOG", bot_log)
    monkeypatch.setattr(
        diag_router,
        "_LOG_FILES",
        {
            "audit": audit,
            "status": status_json,
            "heartbeat": heartbeat,
            "bot_log": bot_log,
        },
    )
    return {
        "runtime_logs": runtime_logs,
        "db_path": db_path,
        "audit": audit,
        "status_json": status_json,
        "heartbeat": heartbeat,
        "bot_log": bot_log,
    }


def _bearer(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# Auth surface
# ---------------------------------------------------------------------------


def test_503_when_token_unset(monkeypatch, fake_runtime):
    monkeypatch.delenv("DIAG_READ_TOKEN", raising=False)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    client = TestClient(api_main.app, raise_server_exceptions=False)
    resp = client.get("/api/diag/snapshot", headers=_bearer(_TOKEN))
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "diag_disabled"


def test_401_no_authorization_header(client, fake_runtime):
    resp = client.get("/api/diag/snapshot")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_token"


def test_401_non_bearer_scheme(client, fake_runtime):
    resp = client.get(
        "/api/diag/snapshot",
        headers={"Authorization": "Basic abc"},
    )
    assert resp.status_code == 401


def test_401_empty_bearer(client, fake_runtime):
    resp = client.get("/api/diag/snapshot", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_401_wrong_token(client, fake_runtime):
    resp = client.get("/api/diag/snapshot", headers=_bearer("not-the-token"))
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_token"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_snapshot_with_empty_runtime_returns_shape(client, fake_runtime):
    resp = client.get("/api/diag/snapshot", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {
        "captured_at",
        "heartbeat",
        "status",
        "audit_tail",
        "order_packages",
        "trades",
        "vm_health",
        "services",
    }
    assert body["heartbeat"]["present"] is False
    assert body["status"] is None
    assert body["audit_tail"] == []
    assert body["order_packages"] == []
    assert body["trades"] == []


def test_audit_returns_tail(client, fake_runtime):
    fake_runtime["audit"].write_text(
        "\n".join(
            [
                json.dumps({"id": 1, "event": "tick", "result": "ok"}),
                json.dumps({"id": 2, "event": "rejected", "reason": "below_min_balance"}),
                "",
                "{not valid}",
                json.dumps({"id": 3, "event": "tick", "result": "ok"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    resp = client.get("/api/diag/audit?limit=10", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    # Three valid JSON lines; one blank skipped, one malformed skipped.
    assert len(body) == 3
    assert body[0]["id"] == 1
    assert body[1]["reason"] == "below_min_balance"


def test_journal_order_packages_returns_rows_newest_updated_first(client, fake_runtime):
    # Mirror the real schema (database.py): TEXT primary key, updated_at
    # is the chronological ordering field. The endpoint must use
    # datetime(updated_at) DESC — alphabetic ordering of pkg-<hash> ids
    # is essentially random.
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    db.execute(
        "CREATE TABLE order_packages ("
        "order_package_id TEXT PRIMARY KEY, status TEXT, strategy_name TEXT, "
        "updated_at TEXT NOT NULL)"
    )
    db.executemany(
        "INSERT INTO order_packages "
        "(order_package_id, status, strategy_name, updated_at) VALUES (?, ?, ?, ?)",
        [
            ("pkg-aaa", "closed", "vwap", "2026-05-09T01:00:00+00:00"),
            ("pkg-bbb", "open", "vwap", "2026-05-09T03:00:00+00:00"),
            ("pkg-ccc", "closed", "vwap", "2026-05-09T02:00:00+00:00"),
        ],
    )
    db.commit()
    db.close()

    resp = client.get(
        "/api/diag/journal?table=order_packages&limit=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["order_package_id"] for r in body] == ["pkg-bbb", "pkg-ccc", "pkg-aaa"]


def test_journal_trades_returns_rows_in_desc_id_order(client, fake_runtime):
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    db.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT, symbol TEXT)"
    )
    db.executemany(
        "INSERT INTO trades (id, status, symbol) VALUES (?, ?, ?)",
        [(1, "closed", "BTCUSDT"), (2, "open", "BTCUSDT"), (3, "closed", "BTCUSDT")],
    )
    db.commit()
    db.close()

    resp = client.get(
        "/api/diag/journal?table=trades&limit=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [3, 2, 1]


def test_journal_unknown_table_400(client, fake_runtime):
    resp = client.get(
        "/api/diag/journal?table=secrets&limit=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_table"


def test_journalctl_unknown_unit_400(client, fake_runtime):
    resp = client.get(
        "/api/diag/journalctl?unit=arbitrary-attacker-unit",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_unit"


def test_journalctl_allowlisted_unit_returns_shape(client, fake_runtime):
    # The actual journalctl call may fail in the test env (no journal access),
    # but the route should accept the unit and return a structured response.
    resp = client.get(
        "/api/diag/journalctl?unit=ict-bot&lines=10",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["unit"] == "ict-bot.service"
    assert "available" in body
    assert "lines" in body


def test_log_file_unknown_name_400(client, fake_runtime):
    resp = client.get(
        "/api/diag/log_file?name=/etc/passwd",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_log_file"


def test_log_file_allowlisted_returns_tail(client, fake_runtime):
    fake_runtime["bot_log"].write_text(
        "\n".join(f"line-{i}" for i in range(50)) + "\n",
        encoding="utf-8",
    )
    resp = client.get(
        "/api/diag/log_file?name=bot_log&lines=5",
        headers=_bearer(_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["lines"] == [f"line-{i}" for i in range(45, 50)]


def test_status_endpoint(client, fake_runtime):
    fake_runtime["status_json"].write_text(
        json.dumps({"schema_version": 1, "git_sha": "abc"}),
        encoding="utf-8",
    )
    fake_runtime["heartbeat"].write_text("ok", encoding="utf-8")

    resp = client.get("/api/diag/status", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["heartbeat"]["present"] is True
    assert body["status"]["git_sha"] == "abc"


def test_services_returns_one_entry_per_canonical_unit(client, fake_runtime):
    resp = client.get("/api/diag/services", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(diag_router._CANONICAL_UNITS)
    units_returned = [entry["unit"] for entry in body]
    assert units_returned == list(diag_router._CANONICAL_UNITS)


# ---------------------------------------------------------------------------
# /api/diag/db_info — DB metadata for trader-vs-web-api cross-reference
# ---------------------------------------------------------------------------


def test_db_info_missing_db_returns_present_false(client, fake_runtime):
    """No DB file at the configured path → ``exists=False``, empty
    tables list, no row counts. Mirrors ``_journal_select``'s
    early-return-empty contract for the same condition."""
    # fake_runtime points _DB_PATH at a tmp path but we never created
    # the file, so it shouldn't exist.
    resp = client.get("/api/diag/db_info", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["tables"] == []
    assert body["row_counts"] == {}


def test_db_info_returns_inode_size_tables_and_counts(client, fake_runtime):
    """Happy path — populated DB returns inode + size + per-table
    row counts. Operator can compare inode across services to confirm
    they read the same file."""
    db = sqlite3.connect(str(fake_runtime["db_path"]))
    try:
        db.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)"
        )
        db.execute(
            "CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY)"
        )
        db.execute("INSERT INTO trades(id, status) VALUES (1, 'open')")
        db.execute("INSERT INTO trades(id, status) VALUES (2, 'closed')")
        db.execute("INSERT INTO order_packages(order_package_id) VALUES ('pkg-a')")
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/diag/db_info", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["size_bytes"] is not None and body["size_bytes"] > 0
    assert body["inode"] is not None
    assert sorted(body["tables"]) == ["order_packages", "trades"]
    assert body["row_counts"] == {"trades": 2, "order_packages": 1}
    assert body["error_per_table"] == {}
    assert body["load_error"] is None


def test_db_info_401_without_token(client, fake_runtime):
    resp = client.get("/api/diag/db_info")
    assert resp.status_code == 401
