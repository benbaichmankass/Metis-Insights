"""S-013 M2 PR #1 — GET /api/status."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.web.api import auth as auth_module
from src.web.api import main as api_main
from src.web.api.routers import status as status_router


@pytest.fixture
def client():
    return TestClient(api_main.app)


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


def test_status_happy_path_returns_200_with_expected_shape(client, status_file):
    payload = _sample_payload()
    status_file.write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get("/api/status")

    assert resp.status_code == 200
    assert resp.json() == payload


def test_status_returns_503_when_status_file_missing(client, status_file):
    assert not status_file.exists()
    resp = client.get("/api/status")

    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["error"] == "status_unavailable"


def test_status_returns_503_on_corrupt_json(client, status_file):
    status_file.write_text("{not valid json", encoding="utf-8")

    resp = client.get("/api/status")

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "status_unavailable"


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# -- M3 PR #2 regression guard ------------------------------------------------
# require_session is currently a no-op. The two checks below pin the no-op
# semantics so that when M3 PR #2 flips enforcement on, the test author has
# to update them deliberately rather than discovering the change at runtime.


def test_require_session_is_currently_a_passthrough(client, status_file):
    """No-op decorator allows requests through with no Authorization header."""
    status_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")

    # No Authorization header — must still get 200 in M2.
    resp = client.get("/api/status")
    assert resp.status_code == 200


def test_require_session_unit_passthrough_returns_inner_value():
    """Decorate a fake handler and confirm the no-op simply forwards."""
    import asyncio

    sentinel = {"ok": True, "marker": "M2-noop"}

    @auth_module.require_session
    async def _fake_handler() -> dict:
        return sentinel

    assert asyncio.run(_fake_handler()) == sentinel
