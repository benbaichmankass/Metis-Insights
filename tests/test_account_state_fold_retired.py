"""JC-CA-06 (operator decision 2026-09-29, "Retire it"): the
config/account_state.yaml dry-only fold is gone from effective_dry.

Contracts under test:

1. The fold's reader no longer exists (``src.runtime.orders`` has no
   ``account_state_dry_run``) and the coordinator does not reference it.
2. A state file declaring ``dry_run: true`` for a ``mode: live`` account no
   longer changes anything — execute_pkg is called with dry_run=False.
3. The two remaining execution gates still force dry:
   a. accounts.yaml ``mode: dry_run`` (RiskBreach account_mode_dry_run,
      execute_pkg never reached);
   b. strategies.yaml ``execution: shadow`` (execute_pkg called with
      dry_run=True on a live account).

Replaces tests/test_account_state_gate.py (PR-3 / M2), which asserted the
fold that JC-CA-06 retired.
"""
from __future__ import annotations

import inspect
import os
import textwrap
from unittest.mock import patch, MagicMock

import pytest
import yaml

import src.runtime.orders as orders
from src.core import coordinator as coordinator_mod
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
# multi_account_execute: effective_dry has no account_state input
# ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def live_coord(tmp_path, monkeypatch):
    """Coordinator loaded from a LIVE accounts.yaml."""
    # _LIVE_ACCOUNTS_YAML uses api_key_env: BYBIT_KEY. Without the env var
    # the account is marked configured=False and dropped by dispatch.
    monkeypatch.setenv("BYBIT_KEY", "test-key")
    accts_file = tmp_path / "accounts.yaml"
    accts_file.write_text(_LIVE_ACCOUNTS_YAML)
    # Coordinator.__init__ uses accounts_path, not accounts_yaml.
    return Coordinator(accounts_path=str(accts_file))


@pytest.fixture()
def dry_coord(tmp_path, monkeypatch):
    """Coordinator loaded from a DRY accounts.yaml."""
    monkeypatch.setenv("BYBIT_KEY", "test-key")
    accts_file = tmp_path / "accounts.yaml"
    accts_file.write_text(_DRY_ACCOUNTS_YAML)
    return Coordinator(accounts_path=str(accts_file))


def _make_balance_stub(value: float = 10_000.0):
    stub = MagicMock()
    stub.get_wallet_balance.return_value = {
        "result": {"list": [{"coin": [{"usdValue": str(value)}]}]}
    }
    return stub



def _run(coord, *, state_file=None, shadow=False):
    env = {"ACCOUNT_STATE_PATH": str(state_file)} if state_file else {}
    with (
        patch.dict(os.environ, env),
        # bybit_client_for is imported inside multi_account_execute; patch at source.
        patch("src.units.accounts.clients.bybit_client_for",
              return_value=_make_balance_stub()),
        patch("src.strategy_registry.execution_mode",
              return_value="shadow" if shadow else "live"),
        patch("src.units.accounts.execute.execute_pkg") as mock_exec,
    ):
        results = coord.multi_account_execute(
            _make_pkg(),
            # multi_account_execute uses its own ``accounts_path`` arg, not
            # self._accounts_path; pass it explicitly so the right YAML is read.
            accounts_path=coord._accounts_path,
            balance_fetcher=lambda _a: 10_000.0,
        )
    return mock_exec, results


def test_fold_reader_is_gone():
    assert not hasattr(orders, "account_state_dry_run")
    assert not hasattr(orders, "_load_account_state")
    src = inspect.getsource(coordinator_mod)
    assert "account_state_dry_run" not in src


def test_state_file_dry_no_longer_forces_live_account_dry(tmp_path, live_coord):
    """Positive control for the retirement: the exact input that used to
    force dry (dry_run: true for this account) is now ignored."""
    state_file = tmp_path / "account_state.yaml"
    state_file.write_text(_yaml_state({"bybit_live": {"dry_run": True}}))

    mock_exec, _ = _run(live_coord, state_file=state_file)

    assert mock_exec.called
    _, kwargs = mock_exec.call_args
    assert kwargs.get("dry_run") is False


def test_accounts_yaml_dry_run_still_forces_dry(dry_coord):
    """Gate 1 — accounts.yaml mode: dry_run. The RiskManager refuses with
    account_mode_dry_run before execute_pkg is reached."""
    mock_exec, results = _run(dry_coord)

    assert not mock_exec.called
    assert len(results) == 1
    assert results[0]["trade_id"] is None
    assert "account_mode_dry_run" in (results[0]["error"] or "")


def test_strategy_execution_shadow_still_forces_dry(live_coord):
    """Gate 2 — strategies.yaml execution: shadow, on a live account."""
    mock_exec, _ = _run(live_coord, shadow=True)

    assert mock_exec.called
    _, kwargs = mock_exec.call_args
    assert kwargs.get("dry_run") is True
