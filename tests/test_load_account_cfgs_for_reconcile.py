"""Regression test for src.runtime.order_monitor._load_account_cfgs_for_reconcile.

Anchors the 2026-05-27 fix: the loader previously dropped the IB connection
fields (`ib_host` / `ib_port` / `ib_account` / `ib_client_id`) from the
returned account_cfg dict, so every monitor-reconciler pass on `ib_paper`
hit `ib_read_client_for(account)` → `ib_client_for(...)` → `account.get(
"ib_port")` = None → "no ib_port set" warning + return None + reconciler
"skip account entirely". Net effect: IB accounts were invisible to all four
reconciler call sites (orphan trade detection on MES never ran) and the
warning spammed each 60s monitor tick. Mirrors the dict shape coordinator
already builds for execution-time `ib_client_for(account_cfg)` at
`coordinator.py::multi_account_execute`.
"""
from __future__ import annotations

import pytest

from src.runtime.order_monitor import _load_account_cfgs_for_reconcile


_BYBIT_CFG = {
    "exchange": "bybit",
    "api_key_env": "BYBIT_API_KEY_1",
    "api_secret_env": "BYBIT_API_SECRET_1",
    "mode": "live",
    "market_type": "linear",
    "demo": False,
}

_IB_CFG = {
    "exchange": "interactive_brokers",
    "mode": "live",
    "market_type": "futures",
    "ib_host": "127.0.0.1",
    "ib_port": 4002,
    "ib_account": "DUQ325724",
    "ib_client_id": 497,
}


@pytest.fixture
def patched_yaml(monkeypatch):
    """Stub ``load_accounts_dict`` so the loader runs without a real YAML."""
    def _stub(*_a, **_kw):
        return {"bybit_1": _BYBIT_CFG, "ib_paper": _IB_CFG}
    monkeypatch.setattr(
        "src.config.accounts_loader.load_accounts_dict", _stub
    )
    return _stub


def test_forwards_ib_connection_fields(patched_yaml):
    out = _load_account_cfgs_for_reconcile()
    ib = out["ib_paper"]
    assert ib["ib_host"] == "127.0.0.1"
    assert ib["ib_port"] == 4002
    assert ib["ib_account"] == "DUQ325724"
    assert ib["ib_client_id"] == 497


def test_bybit_unaffected_no_ib_fields_leak(patched_yaml):
    out = _load_account_cfgs_for_reconcile()
    by = out["bybit_1"]
    assert by["exchange"] == "bybit"
    assert by["api_key_env"] == "BYBIT_API_KEY_1"
    # IB fields default to None on a Bybit account (cfg.get returns None);
    # downstream ib_client_for is gated by exchange != "interactive_brokers"
    # so the None values are inert there.
    assert by.get("ib_port") is None


def test_disabled_account_excluded(monkeypatch):
    monkeypatch.setattr(
        "src.config.accounts_loader.load_accounts_dict",
        lambda *_a, **_kw: {
            "ib_paper": {**_IB_CFG, "enabled": False},
            "bybit_1": _BYBIT_CFG,
        },
    )
    out = _load_account_cfgs_for_reconcile()
    assert "ib_paper" not in out
    assert "bybit_1" in out
