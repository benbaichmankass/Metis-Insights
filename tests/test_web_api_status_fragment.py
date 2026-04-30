"""S-014 M3 PR #1 — GET /ui/fragments/status."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import auth as auth_module
from src.web.api import main as api_main
from src.web.api.routers import status as status_router
from src.web.api.routers import status_fragment as status_fragment_router

_ALLOWED_EMAIL = "ben.baichmankass@gmail.com"
_PASSWORD_HASH = hashlib.sha256(b"correct horse battery staple").hexdigest()
_SIGNING_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("ALLOWED_EMAIL", _ALLOWED_EMAIL)
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", _PASSWORD_HASH)


@pytest.fixture
def client(env):
    return TestClient(api_main.app, raise_server_exceptions=False)


def _bearer(email: str = _ALLOWED_EMAIL) -> dict:
    return {"Authorization": f"Bearer {auth_module.issue_token(email)}"}


@pytest.fixture
def status_path(tmp_path: Path, monkeypatch):
    p = tmp_path / "runtime_status.json"
    monkeypatch.setattr(status_router, "STATUS_PATH", p)
    monkeypatch.setattr(status_fragment_router, "STATUS_PATH", p)
    return p


def _write_status(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_fragment_renders_html_with_expected_fields(status_path, client):
    _write_status(status_path, {
        "schema_version": 1, "bot_uptime_s": 90061, "git_sha": "abc1234",
        "strategies": ["vwap", "turtle_soup"],
        "live": {"bybit_1": True, "bybit_2": False},
        "last_tick_utc": "2026-04-30T12:34:56Z",
    })
    resp = client.get("/ui/fragments/status", headers=_bearer())
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    for needle in ("1d 1h 1m", "abc1234", "vwap", "turtle_soup",
                   "bybit_1", "bybit_2", "2026-04-30T12:34:56Z",
                   'class="pill live"', 'class="pill dry"'):
        assert needle in body, needle


def test_fragment_handles_minutes_only_uptime_and_empty_sets(status_path, client):
    _write_status(status_path, {
        "schema_version": 1, "bot_uptime_s": 360, "git_sha": "deadbee",
        "strategies": [], "live": {}, "last_tick_utc": "2026-04-30T12:34:56Z",
    })
    resp = client.get("/ui/fragments/status", headers=_bearer())
    assert resp.status_code == 200
    assert "6m" in resp.text
    assert "none enabled" in resp.text
    assert "none configured" in resp.text


def test_format_uptime_branches():
    f = status_fragment_router._format_uptime
    assert f(0) == "0m"
    assert f(60) == "1m"
    assert f(3601) == "1h 0m"
    assert f(90061) == "1d 1h 1m"
    assert f(-5) == "0m"


def test_fragment_503_when_status_file_missing(tmp_path, monkeypatch, client):
    """Missing/never-written status file must render the neutral
    'not yet available' stub rather than blow up the home page."""
    missing = tmp_path / "absent.json"
    monkeypatch.setattr(status_router, "STATUS_PATH", missing)
    monkeypatch.setattr(status_fragment_router, "STATUS_PATH", missing)
    resp = client.get("/ui/fragments/status", headers=_bearer())
    assert resp.status_code == 503
    assert "Runtime status not yet available" in resp.text


def test_fragment_without_token_returns_401(status_path, client):
    resp = client.get("/ui/fragments/status")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_session"


def test_fragment_off_allowlist_returns_403(status_path, client):
    resp = client.get(
        "/ui/fragments/status", headers=_bearer("attacker@example.com")
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "email_not_allowlisted"


