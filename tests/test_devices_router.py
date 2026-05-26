"""Tests for the device-token router (M12 S1).

Covers the four endpoints (`POST /register`, `GET /`, `DELETE /{id}`,
`PATCH /{id}/subscriptions`) against an isolated `trade_journal.db`.

Key invariants:

- Token is unique → re-registering an existing token UPDATES rather
  than creating a duplicate row.
- Response never echoes the full FCM token (only its last-8 suffix).
- ``DASHBOARD_API_TOKEN`` gates list / delete / patch when set;
  register stays open so a fresh app can register on first launch.
- Subscription value validation (list / dict / null only).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main


@pytest.fixture
def isolated_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the canonical-db resolver at a temp file so each test gets
    a clean DB. The router calls ``Database()`` lazily, which creates
    the schema (including the new ``device_tokens`` table) on first
    touch."""
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    # Reset the module-level path cache if one exists (it doesn't, but
    # defence-in-depth in case a sibling test caches).
    return db


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /register
# ---------------------------------------------------------------------------


def test_register_creates_new_device(client: TestClient, isolated_db: Path) -> None:
    body = {
        "token": "f" * 160,
        "platform": "android",
        "label": "Operator's Pixel 8",
    }
    resp = client.post("/api/bot/devices/register", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["is_new"] is True
    assert out["platform"] == "android"
    assert out["label"] == "Operator's Pixel 8"
    assert out["subscriptions"] is None  # default-permissive
    assert out["token_suffix"] == "f" * 8
    # Full token must never round-trip.
    assert "token" not in out or "f" * 160 not in str(out)


def test_register_existing_token_updates_in_place(
    client: TestClient, isolated_db: Path
) -> None:
    body = {"token": "t" * 50, "label": "first label"}
    first = client.post("/api/bot/devices/register", json=body).json()

    body2 = {"token": "t" * 50, "label": "new label"}
    second = client.post("/api/bot/devices/register", json=body2).json()

    assert second["id"] == first["id"]
    assert second["is_new"] is False
    assert second["label"] == "new label"


def test_register_rejects_missing_token(
    client: TestClient, isolated_db: Path
) -> None:
    resp = client.post("/api/bot/devices/register", json={"label": "x"})
    assert resp.status_code == 400


def test_register_rejects_bad_platform(
    client: TestClient, isolated_db: Path
) -> None:
    resp = client.post(
        "/api/bot/devices/register",
        json={"token": "t", "platform": "windows-phone"},
    )
    assert resp.status_code == 400


def test_register_accepts_list_subscriptions(
    client: TestClient, isolated_db: Path
) -> None:
    resp = client.post(
        "/api/bot/devices/register",
        json={
            "token": "t",
            "subscriptions": ["trade_closed", "watchdog_alert"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["subscriptions"] == ["trade_closed", "watchdog_alert"]


def test_register_rejects_non_string_subscriptions(
    client: TestClient, isolated_db: Path
) -> None:
    resp = client.post(
        "/api/bot/devices/register",
        json={"token": "t", "subscriptions": [1, 2, 3]},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_list_devices_returns_empty_when_no_registrations(
    client: TestClient, isolated_db: Path
) -> None:
    resp = client.get("/api/bot/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"count": 0, "devices": []}


def test_list_devices_does_not_expose_full_token(
    client: TestClient, isolated_db: Path
) -> None:
    client.post(
        "/api/bot/devices/register",
        json={"token": "ABCDEFGHIJKL_secret_token_payload"},
    )
    resp = client.get("/api/bot/devices")
    body = resp.json()
    assert body["count"] == 1
    device = body["devices"][0]
    assert device["token_suffix"] == "n_payload"[-8:]  # last 8 chars
    # Full token must never appear anywhere in the serialized response.
    assert "ABCDEFGHIJKL_secret_token_payload" not in str(body)


def test_list_devices_enforces_dashboard_token_when_set(
    client: TestClient, isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "s3cr3t")
    resp = client.get("/api/bot/devices")
    assert resp.status_code == 401
    resp = client.get(
        "/api/bot/devices", headers={"Authorization": "Bearer s3cr3t"}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------


def test_delete_existing_device(client: TestClient, isolated_db: Path) -> None:
    reg = client.post(
        "/api/bot/devices/register", json={"token": "x"}
    ).json()
    resp = client.delete(f"/api/bot/devices/{reg['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    # Confirm gone.
    listing = client.get("/api/bot/devices").json()
    assert listing["count"] == 0


def test_delete_missing_device_returns_404(
    client: TestClient, isolated_db: Path
) -> None:
    resp = client.delete("/api/bot/devices/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /{id}/subscriptions
# ---------------------------------------------------------------------------


def test_patch_subscriptions_replaces_value(
    client: TestClient, isolated_db: Path
) -> None:
    reg = client.post(
        "/api/bot/devices/register",
        json={"token": "x", "subscriptions": ["trade_closed"]},
    ).json()
    resp = client.patch(
        f"/api/bot/devices/{reg['id']}/subscriptions",
        json={"subscriptions": ["signals", "watchdog_alert"]},
    )
    assert resp.status_code == 200
    assert resp.json()["subscriptions"] == ["signals", "watchdog_alert"]


def test_patch_subscriptions_to_null_means_subscribe_all(
    client: TestClient, isolated_db: Path
) -> None:
    reg = client.post(
        "/api/bot/devices/register",
        json={"token": "x", "subscriptions": ["trade_closed"]},
    ).json()
    resp = client.patch(
        f"/api/bot/devices/{reg['id']}/subscriptions",
        json={"subscriptions": None},
    )
    assert resp.status_code == 200
    assert resp.json()["subscriptions"] is None


def test_patch_missing_subscriptions_field_returns_400(
    client: TestClient, isolated_db: Path
) -> None:
    reg = client.post(
        "/api/bot/devices/register", json={"token": "x"}
    ).json()
    resp = client.patch(
        f"/api/bot/devices/{reg['id']}/subscriptions",
        json={"other_field": "value"},
    )
    assert resp.status_code == 400
