"""S-064 — GET /api/bot/config tests.

Tier-1 read endpoint: no session required. Critical security
property: never echoes API keys, tokens, signing keys, or other
credentials, even if a future strategy stuffs them into params.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import bot_config as bot_config_router


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def fake_configs(tmp_path, monkeypatch):
    """Materialise minimal accounts + strategies YAMLs and a runtime-status
    file, then point the router's module-level paths at them."""
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        yaml.safe_dump({
            "accounts": {
                "bybit_1": {
                    "type": "regular",
                    "exchange": "bybit",
                    "api_key_env": "BYBIT_API_KEY_1",
                    "api_secret_env": "BYBIT_API_SECRET_1",
                    "mode": "live",
                    "market_type": "spot",
                    "strategies": ["turtle_soup"],
                    "risk": {
                        "max_dd_pct": 0.05,
                        "daily_usd": 100,
                        "pos_size": 500,
                    },
                },
                "bybit_2": {
                    "type": "regular",
                    "exchange": "bybit",
                    "api_key_env": "BYBIT_API_KEY_2",
                    "mode": "live",
                    "market_type": "spot-margin",
                    "strategies": ["vwap"],
                },
            },
        }),
        encoding="utf-8",
    )
    strategies_yaml = tmp_path / "strategies.yaml"
    strategies_yaml.write_text(
        yaml.safe_dump({
            "strategies": {
                "turtle_soup": {
                    "enabled": True,
                    "risk_pct": 0.5,
                    "timeframe": "15m",
                    "symbols": ["BTCUSDT"],
                },
                "vwap": {
                    "enabled": True,
                    "risk_pct": 1.0,
                    "timeframe": "5m",
                    "symbols": ["BTCUSDT"],
                    "htf_gate": True,
                },
            },
        }),
        encoding="utf-8",
    )
    runtime_status = tmp_path / "runtime_status.json"
    runtime_status.write_text(
        json.dumps({
            "schema_version": 1,
            "live": {"bybit_1": True, "bybit_2": False},
            "strategies": ["turtle_soup", "vwap"],
            "git_sha": "abc1234",
            "last_tick_utc": "2026-05-09T18:00:00Z",
        }),
        encoding="utf-8",
    )
    halt_flag = tmp_path / "trader_halt.flag"  # absent by default

    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", accounts_yaml)
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", strategies_yaml)
    monkeypatch.setattr(bot_config_router, "_RUNTIME_STATUS_JSON", runtime_status)
    monkeypatch.setattr(bot_config_router, "_HALT_FLAG_PATH", str(halt_flag))
    return {
        "accounts_yaml": accounts_yaml,
        "strategies_yaml": strategies_yaml,
        "runtime_status": runtime_status,
        "halt_flag": halt_flag,
        "tmp_path": tmp_path,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_config_happy_path_returns_full_payload(fake_configs, client):
    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body = resp.json()

    assert "as_of" in body and body["as_of"].endswith("Z")
    assert body["trading_mode"]["halted"] is False
    assert body["trading_mode"]["live_per_account"] == {"bybit_1": True, "bybit_2": False}

    accounts = {a["id"]: a for a in body["accounts"]}
    assert set(accounts) == {"bybit_1", "bybit_2"}
    a1 = accounts["bybit_1"]
    assert a1["exchange"] == "bybit"
    assert a1["market_type"] == "spot"
    assert a1["yaml_mode"] == "live"
    assert a1["strategies"] == ["turtle_soup"]
    assert a1["risk"]["max_dd_pct"] == 0.05
    assert a1["enabled"] is True  # default

    strategies = body["strategies"]
    assert set(strategies) == {"turtle_soup", "vwap"}
    assert strategies["vwap"]["htf_gate"] is True


def test_config_halt_flag_reflected(fake_configs, client):
    fake_configs["halt_flag"].write_text("halted")
    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    assert resp.json()["trading_mode"]["halted"] is True


# ---------------------------------------------------------------------------
# Tier-1 contract — no session required
# ---------------------------------------------------------------------------


def test_config_without_session_returns_200(fake_configs, client):
    """No Authorization header, no auth env vars — still 200."""
    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    assert "accounts" in resp.json()


# ---------------------------------------------------------------------------
# Empty / missing state
# ---------------------------------------------------------------------------


def test_config_missing_runtime_status_yields_empty_live_map(fake_configs, client):
    fake_configs["runtime_status"].unlink()
    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trading_mode"]["live_per_account"] == {}
    # Other sections still populate from YAML.
    assert len(body["accounts"]) == 2
    assert "turtle_soup" in body["strategies"]


def test_config_missing_yamls_returns_empty_sections_not_500(tmp_path, monkeypatch, client):
    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", tmp_path / "missing-a.yaml")
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", tmp_path / "missing-s.yaml")
    monkeypatch.setattr(bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "missing-r.json")
    monkeypatch.setattr(bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "missing.flag"))
    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["accounts"] == []
    assert body["strategies"] == {}
    assert body["trading_mode"]["live_per_account"] == {}
    assert body["trading_mode"]["halted"] is False


def test_config_malformed_yaml_returns_empty_sections_not_500(tmp_path, monkeypatch, client):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: valid: yaml: at: all:")
    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", bad)
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", bad)
    monkeypatch.setattr(bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "absent.json")
    monkeypatch.setattr(bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "absent.flag"))
    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["accounts"] == []
    assert body["strategies"] == {}


# ---------------------------------------------------------------------------
# Secret redaction — the security-critical contract
# ---------------------------------------------------------------------------


def test_config_never_echoes_api_key_env_field(fake_configs, client):
    resp = client.get("/api/bot/config")
    body = resp.json()
    body_text = json.dumps(body)
    # Field name must not appear; the value (env-var name) must not leak.
    assert "api_key_env" not in body_text
    assert "api_secret_env" not in body_text
    assert "BYBIT_API_KEY_1" not in body_text
    assert "BYBIT_API_KEY_2" not in body_text
    assert "BYBIT_API_SECRET_1" not in body_text


def test_config_redacts_secret_keys_in_strategy_params(tmp_path, monkeypatch, client):
    """Defensive redaction: even if a future strategy stuffs a credential
    into its YAML params, the endpoint must never echo it."""
    sneaky = tmp_path / "strategies.yaml"
    sneaky.write_text(
        yaml.safe_dump({
            "strategies": {
                "compromised": {
                    "enabled": True,
                    "risk_pct": 1.0,
                    # Every flavour of secret-bearing key name. The
                    # endpoint must drop ALL of these.
                    "api_key": "AKIA-LEAKED-1",
                    "api_secret": "leaked-2",
                    "BYBIT_TOKEN": "leaked-3",
                    "password": "leaked-4",
                    "passwd": "leaked-5",
                    "signing_key": "leaked-6",
                    "credential_blob": "leaked-7",
                    "user_hash": "leaked-8",
                    "nested": {
                        "inner_secret": "leaked-9",
                        "another_api_key": "leaked-10",
                        "safe_field": "ok",
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", tmp_path / "absent-a.yaml")
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", sneaky)
    monkeypatch.setattr(bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "absent-r.json")
    monkeypatch.setattr(bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "absent.flag"))

    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body_text = json.dumps(resp.json())

    # None of the leaked values should be in the response body.
    for needle in [
        "AKIA-LEAKED-1", "leaked-2", "leaked-3", "leaked-4",
        "leaked-5", "leaked-6", "leaked-7", "leaked-8",
        "leaked-9", "leaked-10",
    ]:
        assert needle not in body_text, f"secret value leaked: {needle}"

    # The non-secret peer in the nested dict must survive — proves the
    # redactor isn't just nuking the whole subtree.
    assert "safe_field" in body_text
    assert resp.json()["strategies"]["compromised"]["nested"]["safe_field"] == "ok"
    assert resp.json()["strategies"]["compromised"]["enabled"] is True


def test_config_account_allowlist_filters_unknown_fields(tmp_path, monkeypatch, client):
    """Forward-defence: a future YAML field doesn't auto-leak."""
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        yaml.safe_dump({
            "accounts": {
                "bybit_x": {
                    "type": "regular",
                    "exchange": "bybit",
                    "mode": "live",
                    "market_type": "spot",
                    # New field added in some future PR — must NOT
                    # auto-flow through to the API. Allowlist is
                    # explicit; new fields require a code change.
                    "secret_runtime_token_xyz": "DO_NOT_LEAK",
                    "internal_signing_key": "ALSO_DO_NOT_LEAK",
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_config_router, "_ACCOUNTS_YAML", accounts_yaml)
    monkeypatch.setattr(bot_config_router, "_STRATEGIES_YAML", tmp_path / "absent-s.yaml")
    monkeypatch.setattr(bot_config_router, "_RUNTIME_STATUS_JSON", tmp_path / "absent-r.json")
    monkeypatch.setattr(bot_config_router, "_HALT_FLAG_PATH", str(tmp_path / "absent.flag"))

    resp = client.get("/api/bot/config")
    assert resp.status_code == 200
    body_text = json.dumps(resp.json())
    assert "DO_NOT_LEAK" not in body_text
    assert "ALSO_DO_NOT_LEAK" not in body_text
    assert "secret_runtime_token_xyz" not in body_text


# ---------------------------------------------------------------------------
# build_config pure-ish function (test-friendly entry point)
# ---------------------------------------------------------------------------


def test_build_config_uses_fixed_now_for_as_of(fake_configs):
    payload = bot_config_router.build_config(
        accounts_yaml=fake_configs["accounts_yaml"],
        strategies_yaml=fake_configs["strategies_yaml"],
        runtime_status_json=fake_configs["runtime_status"],
        halt_flag_path=str(fake_configs["halt_flag"]),
        now_utc=datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert payload["as_of"] == "2026-05-09T12:00:00Z"
