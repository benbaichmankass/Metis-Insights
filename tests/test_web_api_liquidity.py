"""S-064 — GET /api/bot/liquidity tests.

Tier-1 read endpoint. Reads ``runtime_logs/liquidity_state.json``
written by the pipeline (S-064 prereq) and returns the per-symbol
slice the dashboard's Liquidity Maps tab consumes.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.runtime import liquidity_state
from src.web.api import main as api_main


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


def _sample_state() -> dict:
    return {
        "BTCUSDT": {
            "schema_version": 1,
            "symbol": "BTCUSDT",
            "as_of": "2026-05-09T18:00:00Z",
            "equal_highs": [
                {"side": "buy", "price": 80250.5, "touches": 3,
                 "first_touch": "2026-05-08T14:00:00Z",
                 "last_touch": "2026-05-09T16:00:00Z",
                 "swept": False, "sweep_time": None},
                {"side": "buy", "price": 80100.0, "touches": 2,
                 "first_touch": "2026-05-08T10:00:00Z",
                 "last_touch": "2026-05-09T12:00:00Z",
                 "swept": True, "sweep_time": "2026-05-09T15:00:00Z"},
            ],
            "equal_lows": [
                {"side": "sell", "price": 79500.0, "touches": 2,
                 "first_touch": "2026-05-08T08:00:00Z",
                 "last_touch": "2026-05-09T10:00:00Z",
                 "swept": False, "sweep_time": None},
            ],
            "recent_sweeps": [
                {"side": "buy", "price": 80100.0,
                 "swept_at": "2026-05-09T15:00:00Z"},
            ],
        },
        "ETHUSDT": {
            "schema_version": 1,
            "symbol": "ETHUSDT",
            "as_of": "2026-05-09T18:00:00Z",
            "equal_highs": [],
            "equal_lows": [],
            "recent_sweeps": [],
        },
    }


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    target = tmp_path / "liquidity_state.json"
    target.write_text(json.dumps(_sample_state()), encoding="utf-8")
    monkeypatch.setattr(liquidity_state, "LIQUIDITY_STATE_PATH", target)
    return target


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_liquidity_returns_requested_symbol(state_file, client):
    resp = client.get("/api/bot/liquidity?symbol=BTCUSDT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["as_of"] == "2026-05-09T18:00:00Z"
    assert len(body["equal_highs"]) == 2
    assert len(body["equal_lows"]) == 1
    assert len(body["recent_sweeps"]) == 1
    assert body["available_symbols"] == ["BTCUSDT", "ETHUSDT"]


def test_liquidity_defaults_to_first_alphabetical_symbol(state_file, client):
    resp = client.get("/api/bot/liquidity")
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "BTCUSDT"


def test_liquidity_unknown_symbol_returns_empty(state_file, client):
    resp = client.get("/api/bot/liquidity?symbol=UNKNOWNUSDT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "UNKNOWNUSDT"
    assert body["equal_highs"] == []
    assert body["equal_lows"] == []
    assert body["recent_sweeps"] == []


def test_liquidity_empty_per_symbol_state_returns_empty_arrays(state_file, client):
    resp = client.get("/api/bot/liquidity?symbol=ETHUSDT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "ETHUSDT"
    assert body["equal_highs"] == []
    assert body["equal_lows"] == []
    assert body["recent_sweeps"] == []


# ---------------------------------------------------------------------------
# limit / sweeps_limit clamping
# ---------------------------------------------------------------------------


def test_liquidity_limit_caps_returned_zones(state_file, client):
    resp = client.get("/api/bot/liquidity?symbol=BTCUSDT&limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["equal_highs"]) == 1
    assert len(body["equal_lows"]) == 1  # only 1 in fixture, limit=1 keeps it


def test_liquidity_limit_too_low_is_422(state_file, client):
    resp = client.get("/api/bot/liquidity?symbol=BTCUSDT&limit=0")
    assert resp.status_code == 422


def test_liquidity_limit_too_high_is_422(state_file, client):
    resp = client.get("/api/bot/liquidity?symbol=BTCUSDT&limit=101")
    assert resp.status_code == 422


def test_liquidity_sweeps_limit_clamping_independent(state_file, client):
    resp = client.get(
        "/api/bot/liquidity?symbol=BTCUSDT&limit=1&sweeps_limit=1"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["recent_sweeps"]) == 1


# ---------------------------------------------------------------------------
# Tier-1 contract — no session required
# ---------------------------------------------------------------------------


def test_liquidity_without_session_returns_200(state_file, client):
    resp = client.get("/api/bot/liquidity?symbol=BTCUSDT")
    assert resp.status_code == 200
    assert "equal_highs" in resp.json()


# ---------------------------------------------------------------------------
# State file missing / malformed
# ---------------------------------------------------------------------------


def test_liquidity_missing_state_file_returns_empty_payload(tmp_path, monkeypatch, client):
    monkeypatch.setattr(liquidity_state, "LIQUIDITY_STATE_PATH", tmp_path / "nope.json")
    resp = client.get("/api/bot/liquidity?symbol=BTCUSDT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["equal_highs"] == []
    assert body["equal_lows"] == []
    assert body["recent_sweeps"] == []
    assert body["as_of"] is None


def test_liquidity_malformed_state_file_returns_empty_payload(tmp_path, monkeypatch, client):
    target = tmp_path / "broken.json"
    target.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(liquidity_state, "LIQUIDITY_STATE_PATH", target)
    resp = client.get("/api/bot/liquidity?symbol=BTCUSDT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["equal_highs"] == []


def test_liquidity_no_state_no_symbol_returns_empty_with_blank_symbol(tmp_path, monkeypatch, client):
    monkeypatch.setattr(liquidity_state, "LIQUIDITY_STATE_PATH", tmp_path / "nope.json")
    resp = client.get("/api/bot/liquidity")
    assert resp.status_code == 200
    body = resp.json()
    # When neither client nor file supplies a symbol, response is
    # blank-but-shaped so the dashboard doesn't crash.
    assert body["symbol"] == ""
    assert body["equal_highs"] == []
