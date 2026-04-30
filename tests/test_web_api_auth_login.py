"""S-013 M3 PR #1 — POST /api/auth/login + JWT helpers."""
from __future__ import annotations

import hashlib
import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from src.web.api import auth as auth_module
from src.web.api import main as api_main


_ALLOWED_EMAIL = "ben.baichmankass@gmail.com"
_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hashlib.sha256(_PASSWORD.encode("utf-8")).hexdigest()
_SIGNING_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("ALLOWED_EMAIL", _ALLOWED_EMAIL)
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", _PASSWORD_HASH)


# ---------------------------------------------------------------------------
# /api/auth/login route behaviour
# ---------------------------------------------------------------------------


def test_login_returns_token_for_valid_creds(client, env):
    resp = client.post(
        "/api/auth/login",
        json={"email": _ALLOWED_EMAIL, "password": _PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    decoded = pyjwt.decode(body["access_token"], _SIGNING_KEY, algorithms=["HS256"])
    assert decoded["email"] == _ALLOWED_EMAIL
    assert decoded["exp"] - decoded["iat"] == 3600


def test_login_rejects_non_allowlisted_email_with_403(client, env):
    resp = client.post(
        "/api/auth/login",
        json={"email": "someone.else@example.com", "password": _PASSWORD},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "email_not_allowlisted"


def test_login_rejects_wrong_password_with_401(client, env):
    resp = client.post(
        "/api/auth/login",
        json={"email": _ALLOWED_EMAIL, "password": "guess"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_credentials"


def test_login_email_match_is_case_insensitive(client, env):
    resp = client.post(
        "/api/auth/login",
        json={"email": _ALLOWED_EMAIL.upper(), "password": _PASSWORD},
    )
    assert resp.status_code == 200


def test_login_validation_error_when_payload_malformed(client, env):
    resp = client.post("/api/auth/login", json={"email": "not-an-email"})
    assert resp.status_code == 422


@pytest.mark.parametrize("missing", ["JWT_SIGNING_KEY", "ALLOWED_EMAIL", "WEBAPP_PASSWORD_SHA256"])
def test_login_returns_500_when_any_secret_missing_without_leaking_name(
    client, env, monkeypatch, missing
):
    monkeypatch.delenv(missing, raising=False)
    resp = client.post(
        "/api/auth/login",
        json={"email": _ALLOWED_EMAIL, "password": _PASSWORD},
    )
    assert resp.status_code == 500
    body = resp.text
    # Must not echo the env-var name or the value of any secret.
    assert "JWT_SIGNING_KEY" not in body
    assert "ALLOWED_EMAIL" not in body
    assert "WEBAPP_PASSWORD_SHA256" not in body
    assert _SIGNING_KEY not in body
    assert _PASSWORD_HASH not in body
    assert _PASSWORD not in body
    assert resp.json()["detail"]["error"] == "auth_unavailable"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def test_issue_and_decode_token_round_trip(env):
    token = auth_module.issue_token(_ALLOWED_EMAIL)
    claims = auth_module.decode_token(token)
    assert claims is not None
    assert claims["email"] == _ALLOWED_EMAIL
    assert claims["exp"] - claims["iat"] == 3600


def test_decode_token_returns_none_for_tampered_signature(env):
    token = auth_module.issue_token(_ALLOWED_EMAIL)
    head, payload, _sig = token.split(".")
    tampered = f"{head}.{payload}.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert auth_module.decode_token(tampered) is None


def test_decode_token_returns_none_for_expired_token(env, monkeypatch):
    past = int(time.time()) - 7200  # 2 hours ago
    expired = auth_module.issue_token(_ALLOWED_EMAIL, now=past)
    assert auth_module.decode_token(expired) is None


def test_decode_token_rejects_alg_none(env):
    # Hand-craft an alg=none token; PyJWT must refuse it because we pin HS256.
    none_token = pyjwt.encode(
        {"email": _ALLOWED_EMAIL, "iat": int(time.time()), "exp": int(time.time()) + 3600},
        key="",
        algorithm="none",
    )
    assert auth_module.decode_token(none_token) is None


def test_decode_token_returns_none_when_signing_key_missing(monkeypatch):
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    assert auth_module.decode_token("anything.at.all") is None


def test_verify_password_constant_time_compare(env):
    assert auth_module.verify_password(_PASSWORD, _PASSWORD_HASH) is True
    assert auth_module.verify_password("wrong", _PASSWORD_HASH) is False
    assert auth_module.verify_password("", _PASSWORD_HASH) is False


# ---------------------------------------------------------------------------
# Cross-PR contract (M3 PR #2): /api/auth/login is in PUBLIC_ROUTES so the
# operator can mint a token without already having one; /api/status and
# /api/pnl are default-deny.
# ---------------------------------------------------------------------------


def test_auth_login_is_in_public_routes(env):
    assert "/api/auth/login" in auth_module.PUBLIC_ROUTES
    assert "/api/health" in auth_module.PUBLIC_ROUTES
    # Default-deny: protected routes must NOT be in PUBLIC_ROUTES.
    assert "/api/status" not in auth_module.PUBLIC_ROUTES
    assert "/api/pnl" not in auth_module.PUBLIC_ROUTES


def test_login_then_status_round_trip(client, env, tmp_path, monkeypatch):
    """End-to-end: log in, then call /api/status with the issued token."""
    from src.web.api.routers import status as status_router

    payload_path = tmp_path / "rs.json"
    payload_path.write_text('{"schema_version": 1}', encoding="utf-8")
    monkeypatch.setattr(status_router, "STATUS_PATH", payload_path)

    # Step 1: log in.
    login = client.post(
        "/api/auth/login",
        json={"email": _ALLOWED_EMAIL, "password": _PASSWORD},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    # Step 2: status now reachable with that token.
    resp = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    # Sanity: still 401 without it.
    resp_anon = client.get("/api/status")
    assert resp_anon.status_code == 401
