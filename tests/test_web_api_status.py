"""S-013 M2 PR #1 + M3 PR #2 — GET /api/status."""
from __future__ import annotations

import hashlib
import json
import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from src.web.api import auth as auth_module
from src.web.api import main as api_main
from src.web.api.routers import status as status_router


_ALLOWED_EMAIL = "ben.baichmankass@gmail.com"
_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hashlib.sha256(_PASSWORD.encode("utf-8")).hexdigest()
_SIGNING_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("ALLOWED_EMAIL", _ALLOWED_EMAIL)
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", _PASSWORD_HASH)


@pytest.fixture
def client(env):
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def status_file(tmp_path, monkeypatch):
    path = tmp_path / "runtime_status.json"
    monkeypatch.setattr(status_router, "STATUS_PATH", path)
    return path


def _sample_payload(overrides=None):
    base = {
        "schema_version": 1,
        "bot_uptime_s": 3725,
        "live": {"bybit_1": False, "bybit_2": True},
        "strategies": ["turtle_soup", "vwap"],
        "git_sha": "abc1234",
        "last_tick_utc": "2026-04-30T12:34:56Z",
    }
    if overrides:
        base.update(overrides)
    return base


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _valid_token(email: str = _ALLOWED_EMAIL) -> str:
    return auth_module.issue_token(email)


# ---------------------------------------------------------------------------
# Happy path (with valid bearer token).
# ---------------------------------------------------------------------------


def test_status_with_valid_token_returns_200_with_expected_shape(client, status_file):
    payload = _sample_payload()
    status_file.write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get("/api/status", headers=_bearer(_valid_token()))

    assert resp.status_code == 200
    assert resp.json() == payload


def test_status_returns_503_when_status_file_missing(client, status_file):
    assert not status_file.exists()
    resp = client.get("/api/status", headers=_bearer(_valid_token()))

    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["error"] == "status_unavailable"


def test_status_returns_503_on_corrupt_json(client, status_file):
    status_file.write_text("{not valid json", encoding="utf-8")

    resp = client.get("/api/status", headers=_bearer(_valid_token()))

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "status_unavailable"


def test_health_endpoint_is_public(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# require_session enforcement (M3 PR #2).
# ---------------------------------------------------------------------------


def test_status_without_authorization_header_returns_401(client, status_file):
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    resp = client.get("/api/status")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_session"


def test_status_with_non_bearer_scheme_returns_401(client, status_file):
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    resp = client.get("/api/status", headers={"Authorization": "Basic abc123"})
    assert resp.status_code == 401


def test_status_with_empty_bearer_token_returns_401(client, status_file):
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    resp = client.get("/api/status", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_status_with_garbage_token_returns_401(client, status_file):
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    resp = client.get("/api/status", headers=_bearer("not.a.jwt"))
    assert resp.status_code == 401


def test_status_with_expired_token_returns_401(client, status_file):
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    expired = auth_module.issue_token(_ALLOWED_EMAIL, now=int(time.time()) - 7200)
    resp = client.get("/api/status", headers=_bearer(expired))
    assert resp.status_code == 401


def test_status_with_alg_none_token_returns_401(client, status_file):
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    none_token = pyjwt.encode(
        {"email": _ALLOWED_EMAIL, "iat": int(time.time()), "exp": int(time.time()) + 3600},
        key="",
        algorithm="none",
    )
    resp = client.get("/api/status", headers=_bearer(none_token))
    assert resp.status_code == 401


def test_status_with_wrong_signature_returns_401(client, status_file):
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    forged = pyjwt.encode(
        {"email": _ALLOWED_EMAIL, "iat": int(time.time()), "exp": int(time.time()) + 3600},
        key="not-the-real-signing-key",
        algorithm="HS256",
    )
    resp = client.get("/api/status", headers=_bearer(forged))
    assert resp.status_code == 401


def test_status_with_off_allowlist_email_in_valid_token_returns_403(client, status_file):
    """Token signed with the right key but for an email no longer on the allowlist."""
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    off_allowlist = auth_module.issue_token("attacker@example.com")
    resp = client.get("/api/status", headers=_bearer(off_allowlist))
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "email_not_allowlisted"


def test_status_returns_500_when_signing_key_missing(client, status_file, monkeypatch):
    """Operator with a real-looking token but server can't decode → 401, not crash."""
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    token = _valid_token()
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    resp = client.get("/api/status", headers=_bearer(token))
    # decode_token returns None because signing key missing → 401 invalid_session.
    assert resp.status_code == 401


def test_status_returns_500_when_allowed_email_missing(client, status_file, monkeypatch):
    """Token decodes fine, but server can't check allowlist → 500 generic."""
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    token = _valid_token()
    monkeypatch.delenv("ALLOWED_EMAIL", raising=False)
    resp = client.get("/api/status", headers=_bearer(token))
    assert resp.status_code == 500
    body = resp.text
    assert "ALLOWED_EMAIL" not in body
    assert resp.json()["detail"]["error"] == "auth_unavailable"
