"""PR-3 / M2: account_state.yaml gate overrides accounts.yaml dry/live.

Contracts under test:

1. account_state_dry_run() returns True when yaml says dry_run: true.
2. account_state_dry_run() returns False when yaml says dry_run: false.
3. account_state_dry_run() returns None when account is absent.
4. account_state_dry_run() returns None when file is missing.
5. multi_account_execute(): account_state.yaml dry overrides accounts.yaml live.
   — even if accounts.yaml says ``mode: live``, a state-file dry_run: true
   forces the dispatch into dry mode (execute_pkg called with dry_run=True).
6. multi_account_execute(): account_state.yaml live does NOT force live over
   accounts.yaml dry — state file can only increase dryness, never decrease.
"""
from __future__ import annotations

import os
import textwrap
from unittest.mock import patch, MagicMock

import pytest
import yaml

from src.runtime.orders import account_state_dry_run
from src.core.coordinator import Coordinator, OrderPackage


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _yaml_state(accounts: dict) -> str:
    return yaml.dump({"accounts": accounts})


def _make_pkg() -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=80_000.0,
        sl=79_000.0,
        tp=81_000.0,
    )


_LIVE_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_live:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY
        mode: live
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 200
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 10
""")

_DRY_ACCOUNTS_YAML = _LIVE_ACCOUNTS_YAML.replace("mode: live", "mode: dry_run")


# ────────────────────────────────────────────────────────────────────
# Unit tests: account_state_dry_run()
# ────────────────────────────────────────────────────────────────────

def test_state_dry_returns_true(tmp_path):
    state_file = tmp_path / "account_state.yaml"
    state_file.write_text(_yaml_state({"bybit_1": {"dry_run": True}}))
    with patch.dict(os.environ, {"ACCOUNT_STATE_PATH": str(state_file)}):
        assert account_state_dry_run("bybit_1") is True


def test_state_dry_returns_false(tmp_path):
    state_file = tmp_path / "account_state.yaml"
    state_file.write_text(_yaml_state({"bybit_2": {"dry_run": False}}))
    with patch.dict(os.environ, {"ACCOUNT_STATE_PATH": str(state_file)}):
        assert account_state_dry_run("bybit_2") is False


def test_state_absent_account_returns_none(tmp_path):
    state_file = tmp_path / "account_state.yaml"
    state_file.write_text(_yaml_state({"bybit_1": {"dry_run": True}}))
    with patch.dict(os.environ, {"ACCOUNT_STATE_PATH": str(state_file)}):
        assert account_state_dry_run("bybit_2") is None


def test_state_missing_file_returns_none(tmp_path):
    with patch.dict(os.environ, {"ACCOUNT_STATE_PATH": str(tmp_path / "nonexistent.yaml")}):
        assert account_state_dry_run("bybit_1") is None


# ────────────────────────────────────────────────────────────────────
# Integration: gate wired into multi_account_execute
# ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def live_coord(tmp_path):
    """Coordinator loaded from a LIVE accounts.yaml."""
    accts_file = tmp_path / "accounts.yaml"
    accts_file.write_text(_LIVE_ACCOUNTS_YAML)
    return Coordinator(accounts_yaml=str(accts_file))


@pytest.fixture()
def dry_coord(tmp_path):
    """Coordinator loaded from a DRY accounts.yaml."""
    accts_file = tmp_path / "accounts.yaml"
    accts_file.write_text(_DRY_ACCOUNTS_YAML)
    return Coordinator(accounts_yaml=str(accts_file))


def _make_balance_stub(value: float = 10_000.0):
    stub = MagicMock()
    stub.get_wallet_balance.return_value = {
        "result": {"list": [{"coin": [{"usdValue": str(value)}]}]}
    }
    return stub


def test_state_dry_overrides_accounts_live(tmp_path, live_coord):
    """account_state.yaml dry_run:true must force dry even when accounts.yaml is live."""
    state_file = tmp_path / "account_state.yaml"
    state_file.write_text(_yaml_state({"bybit_live": {"dry_run": True}}))

    with (
        patch.dict(os.environ, {"ACCOUNT_STATE_PATH": str(state_file)}),
        patch("src.core.coordinator.bybit_client_for", return_value=_make_balance_stub()),
        patch("src.units.accounts.execute.execute_pkg") as mock_exec,
    ):
        live_coord.multi_account_execute(_make_pkg())

    assert mock_exec.called
    _, kwargs = mock_exec.call_args
    assert kwargs.get("dry_run") is True, (
        "execute_pkg must be called with dry_run=True when account_state.yaml overrides"
    )


def test_state_live_cannot_force_live_over_accounts_dry(tmp_path, dry_coord):
    """account_state.yaml dry_run:false must NOT override accounts.yaml dry_run."""
    state_file = tmp_path / "account_state.yaml"
    state_file.write_text(_yaml_state({"bybit_live": {"dry_run": False}}))

    with (
        patch.dict(os.environ, {"ACCOUNT_STATE_PATH": str(state_file)}),
        patch("src.core.coordinator.bybit_client_for", return_value=_make_balance_stub()),
        patch("src.units.accounts.execute.execute_pkg") as mock_exec,
    ):
        dry_coord.multi_account_execute(_make_pkg())

    assert mock_exec.called
    _, kwargs = mock_exec.call_args
    assert kwargs.get("dry_run") is True, (
        "execute_pkg must remain dry_run=True; state file cannot force live over accounts.yaml dry"
    )
