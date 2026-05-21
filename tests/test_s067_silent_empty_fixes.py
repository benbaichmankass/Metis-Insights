"""S-067 CP-2 — regression tests for the three trust-corroding silent-empty
sites converted to loud failures in this PR.

Generalises the PR #627 / #629 root-cause class: a structural DB or
resource failure must be observable on the wire, not silently rendered
as ``0``, ``[]``, or fabricated default measurements.

Sites covered (per ``docs/audits/silent-empty-2026-05-10.md`` § 1):
  1. ``src/web/api/routers/dashboard.py::_pnl_stats`` — was returning
     ``(0.0, 0.0, 0, 0.0)`` on any DB error. Now narrows to
     ``sqlite3.Error``, logs, re-raises; ``get_stats`` returns 503.
  2. ``src/web/api/routers/diag.py::_journal_select`` — was returning
     ``[]`` on any sqlite3.Error. Now logs and raises
     ``HTTPException(503)`` with ``error=journal_unavailable``.
  3. ``src/web/api/routers/diag.py::_vm_health`` — was returning
     ``{\"cpu\": 0.0, ...}`` on psutil failure. Now mirrors
     ``dashboard.py::_vm_health``: returns ``None`` per field.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import dashboard as dashboard_router
from src.web.api.routers import diag as diag_router

_DIAG_TOKEN = "test-diag-token-not-a-real-secret"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def diag_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror tests/test_web_api_diag.py — diag router needs the token
    and require_session needs valid auth env to avoid the
    auth_unavailable 500 path."""
    monkeypatch.setenv("DIAG_READ_TOKEN", _DIAG_TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")


@pytest.fixture
def client(diag_env: None) -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def isolated_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint dashboard router file paths so the existing on-disk
    audit log + DB don't leak into assertions. Returns the tmp dir."""
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


@pytest.fixture
def isolated_diag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime_logs = tmp_path / "runtime_logs"
    runtime_logs.mkdir()
    db_path = tmp_path / "trade_journal.db"
    audit = runtime_logs / "signal_audit.jsonl"
    status_json = runtime_logs / "status.json"
    heartbeat = runtime_logs / "heartbeat.txt"
    bot_log = tmp_path / "bot.log"
    monkeypatch.setattr(diag_router, "_DB_PATH", db_path)
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
    return tmp_path


def _bearer(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def _make_broken_trades_db(path: Path) -> None:
    """Materialise a ``trades`` table missing the ``is_backtest`` column.

    The dashboard's ``_pnl_stats`` SELECT references ``is_backtest`` in
    its WHERE clause — a missing column raises
    ``sqlite3.OperationalError`` which is exactly the failure mode
    PR #627 surfaced for ``/positions``. Pre-S-067, that error was
    swallowed and the endpoint reported zero P&L.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            position_size REAL NOT NULL,
            pnl REAL,
            status TEXT DEFAULT 'open',
            account_id TEXT NOT NULL DEFAULT 'live',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Site #1 — dashboard.py::_pnl_stats
# ---------------------------------------------------------------------------


def test_stats_503_on_db_schema_mismatch(
    client: TestClient, isolated_dashboard: Path
) -> None:
    """DB exists but schema is broken → 503 ``stats_unavailable``.

    Pre-S-067 this returned 200 with ``pnl24h: 0, totalPnL: 0,
    openTrades: 0, winRate: 0`` — visually identical to a real
    \"no trades today\" day on the dashboard.
    """
    db = isolated_dashboard / "trade_journal.db"
    _make_broken_trades_db(db)
    resp = client.get("/api/bot/stats")
    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["error"] == "stats_unavailable"
    assert "OperationalError" in body["detail"]["reason"]


def test_stats_returns_zeroes_when_db_does_not_exist(
    client: TestClient, isolated_dashboard: Path
) -> None:
    """Legitimate \"DB hasn't been created yet\" branch is preserved —
    a fresh install must not 503 on the first call."""
    # isolated_dashboard fixture sets _DB_PATH to a path it never
    # creates — the file genuinely doesn't exist.
    assert not (isolated_dashboard / "trade_journal.db").exists()
    resp = client.get("/api/bot/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pnl24h"] == 0
    assert body["totalPnL"] == 0
    assert body["openTrades"] == 0
    assert body["winRate"] == 0


# ---------------------------------------------------------------------------
# Site #2 — diag.py::_journal_select
# ---------------------------------------------------------------------------


def test_journal_503_on_db_with_no_tables(
    client: TestClient, isolated_diag: Path
) -> None:
    """DB file exists but has no ``trades`` table → 503
    ``journal_unavailable``.

    Pre-S-067 returned 200 ``[]``, indistinguishable from \"table
    empty\". This was the exact bug PR #624's ``/db_info`` was added
    to *work around*; this test guards the actual fix.
    """
    db_path = isolated_diag / "trade_journal.db"
    # Create an empty DB file with no tables.
    conn = sqlite3.connect(str(db_path))
    conn.close()
    resp = client.get(
        "/api/diag/journal?table=trades&limit=10",
        headers=_bearer(_DIAG_TOKEN),
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["error"] == "journal_unavailable"
    assert body["detail"]["table"] == "trades"
    assert "OperationalError" in body["detail"]["reason"]


def test_journal_returns_empty_when_db_does_not_exist(
    client: TestClient, isolated_diag: Path
) -> None:
    """DB file genuinely missing → 200 ``[]``. Distinct from the
    schema-mismatch 503 path above."""
    assert not (isolated_diag / "trade_journal.db").exists()
    resp = client.get(
        "/api/diag/journal?table=trades&limit=10",
        headers=_bearer(_DIAG_TOKEN),
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Site #3 — diag.py::_vm_health
# ---------------------------------------------------------------------------


def test_diag_vm_health_returns_none_per_field_on_psutil_failure(
    client: TestClient, isolated_diag: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """psutil raise → ``{cpu: None, memory: None, disk: None}`` on the
    wire (mirrors ``dashboard.py::_vm_health`` post-S-061). Pre-S-067
    surfaced as ``0.0`` per field, which an operator looking at
    /diag/snapshot couldn't distinguish from a real zero reading.
    """
    import psutil

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("psutil sample failed (synthetic)")

    monkeypatch.setattr(psutil, "cpu_percent", boom)

    resp = client.get("/api/diag/snapshot", headers=_bearer(_DIAG_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["vm_health"] == {"cpu": None, "memory": None, "disk": None}
