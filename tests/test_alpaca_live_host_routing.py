"""Regression: a LIVE Alpaca account's ``alpaca_env`` must survive loading.

BL-20260628-ALPACA-LIVE-HOST. ``config/accounts.yaml`` declares
``alpaca_env: live`` on the real-money ``alpaca_live`` account so its client
dials ``api.alpaca.markets``. Three independent account-dict builders feed the
Alpaca client factory:

  * ``src.units.accounts.load_accounts``      → TradingAccount (order entry +
                                                 the order_monitor close path)
  * ``src.units.ui.data_loaders._load_yaml_accounts`` → the balance / open-
                                                 positions READ path

Before the fix NONE of them carried ``alpaca_env`` through, so
``alpaca_client_for`` fell back to ``os.environ.get("ALPACA_ENV", "paper")`` and
sent the account's LIVE key to the PAPER host → Alpaca ``"request is not
authorized"``. The account was inert (no balance, no fills) from the
2026-06-26 live flip and key rotation could never fix a wrong-host request.

These tests pin the field's survival through both loaders so the plumbing can
never silently regress again.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


_ALPACA_LIVE_YAML = textwrap.dedent(
    """\
    accounts:
      alpaca_live:
        exchange: alpaca
        account_class: real_money
        mode: live
        alpaca_env: live
        api_key_env: ALPACA_API_KEY_ID_LIVE
        api_secret_env: ALPACA_API_SECRET_KEY_LIVE
        strategies: [spy_trend_long_1d]
        symbols: [SPY]
        risk:
          risk_pct: 0.02
      alpaca_paper:
        exchange: alpaca
        account_class: paper
        mode: live
        strategies: [spy_trend_long_1d]
        symbols: [SPY]
        risk:
          risk_pct: 0.01
    """
)


@pytest.fixture()
def accounts_yaml(tmp_path: Path) -> str:
    path = tmp_path / "accounts.yaml"
    path.write_text(_ALPACA_LIVE_YAML, encoding="utf-8")
    return str(path)


def test_production_loader_carries_alpaca_env(accounts_yaml: str):
    """load_accounts() (order entry + close path) must set alpaca_env=live."""
    from src.units.accounts import load_accounts

    by_name = {a.name: a for a in load_accounts(accounts_yaml)}
    assert by_name["alpaca_live"].alpaca_env == "live"
    # The paper account declares no alpaca_env → None → defaults to the
    # paper host, which is correct for it.
    assert by_name["alpaca_paper"].alpaca_env is None


def test_read_path_loader_carries_alpaca_env(accounts_yaml: str, monkeypatch):
    """_load_yaml_accounts() (balance / positions read path) must carry it."""
    import src.units.ui.data_loaders as dl

    monkeypatch.setattr(dl, "ACCOUNTS_YAML_PATH", Path(accounts_yaml), raising=False)
    monkeypatch.setattr(dl, "_DEFAULT_ACCOUNTS_YAML", accounts_yaml, raising=False)
    from src.config import accounts_loader as al
    monkeypatch.setattr(al, "DEFAULT_ACCOUNTS_YAML", Path(accounts_yaml), raising=False)

    by_id = {a["account_id"]: a for a in dl._load_yaml_accounts()}
    assert by_id["alpaca_live"].get("alpaca_env") == "live"
    # The live key env-name must also survive (it always did — the host
    # selector was the dropped half of the pair).
    assert by_id["alpaca_live"].get("api_key_env") == "ALPACA_API_KEY_ID_LIVE"


def test_reconciler_loader_carries_alpaca_env(accounts_yaml: str, monkeypatch):
    """order_monitor._load_account_cfgs_for_reconcile() (the 4th account-dict
    builder — positions reconciler) must carry alpaca_env too. Missing it was
    why ``alpaca positions: request is not authorized`` persisted after #4916.
    """
    from src.config import accounts_loader as al
    monkeypatch.setattr(al, "DEFAULT_ACCOUNTS_YAML", Path(accounts_yaml), raising=False)
    import src.runtime.order_monitor as om

    cfgs = om._load_account_cfgs_for_reconcile()
    assert cfgs["alpaca_live"].get("alpaca_env") == "live"


def test_alpaca_client_dials_live_host_for_live_account(accounts_yaml: str, monkeypatch):
    """End-to-end: a live-env account builds a client pointed at the live host."""
    monkeypatch.setenv("ALPACA_API_KEY_ID_LIVE", "AKTESTLIVEKEY")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY_LIVE", "secret")
    # Ensure no global override is masking the bug under test.
    monkeypatch.delenv("ALPACA_ENV", raising=False)

    from src.units.accounts import load_accounts
    from src.units.accounts.clients import alpaca_client_for

    acct = {a.name: a for a in load_accounts(accounts_yaml)}["alpaca_live"]
    cfg = {"alpaca_env": acct.alpaca_env, "base_url": getattr(acct, "base_url", None),
           "api_key_env": acct.api_key_env, "api_secret_env": "ALPACA_API_SECRET_KEY_LIVE"}
    client = alpaca_client_for(cfg)
    assert client is not None
    assert client.base_url == "https://api.alpaca.markets"


# ---------------------------------------------------------------------------
# BL-20260701-ALPACA-LIVE-SECRET-ENV — the companion secret env-var NAME must
# survive loading AND be forwarded into the execution/close account_cfg. If it
# is dropped, alpaca_client_for pairs the LIVE key with the SHARED PAPER secret
# (ALPACA_API_SECRET_KEY) → Alpaca 401 "unauthorized" on every ORDER while the
# balance READ path (built from the raw YAML, which has both) still succeeds.
# ---------------------------------------------------------------------------


def test_production_loader_carries_api_secret_env(accounts_yaml: str):
    """load_accounts() must persist the account's own secret env-var name."""
    from src.units.accounts import load_accounts

    by_name = {a.name: a for a in load_accounts(accounts_yaml)}
    assert by_name["alpaca_live"].api_secret_env == "ALPACA_API_SECRET_KEY_LIVE"
    # The paper account declares none → None → alpaca_client_for falls back to
    # the shared ALPACA_API_SECRET_KEY, which is correct for it.
    assert by_name["alpaca_paper"].api_secret_env is None


def test_live_account_client_uses_live_secret_not_paper(accounts_yaml: str, monkeypatch):
    """End-to-end: a client built the way the coordinator/monitor build it —
    forwarding ``api_secret_env`` off the account — must resolve the LIVE
    secret, never the shared paper secret. This is the exact reads-OK /
    orders-unauthorized bug.
    """
    monkeypatch.setenv("ALPACA_API_KEY_ID_LIVE", "AKLIVEKEY")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY_LIVE", "LIVE_SECRET_VALUE")
    # The shared paper secret — DISTINCT — must NOT be what the live client uses.
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "PAPER_SECRET_VALUE")
    monkeypatch.delenv("ALPACA_ENV", raising=False)

    from src.units.accounts import load_accounts
    from src.units.accounts.clients import alpaca_client_for

    acct = {a.name: a for a in load_accounts(accounts_yaml)}["alpaca_live"]
    # Build the cfg exactly as coordinator.multi_account_execute /
    # order_monitor now do — forward api_secret_env off the account object.
    cfg = {
        "exchange": acct.exchange,
        "alpaca_env": acct.alpaca_env,
        "api_key_env": acct.api_key_env,
        "api_secret_env": getattr(acct, "api_secret_env", None),
    }
    client = alpaca_client_for(cfg)
    assert client is not None
    assert client.api_key == "AKLIVEKEY"
    assert client.api_secret == "LIVE_SECRET_VALUE", (
        "live account must pair its live KEY with its live SECRET — pairing the "
        "shared paper secret is the 401 'unauthorized' bug"
    )


def test_coordinator_account_cfg_forwards_api_secret_env():
    """The coordinator's execution account_cfg dict must include api_secret_env
    (a static guard against the field being dropped from the dict again).
    """
    import inspect
    from src.core import coordinator

    src = inspect.getsource(coordinator.Coordinator.multi_account_execute)
    assert '"api_secret_env"' in src, (
        "coordinator account_cfg must forward api_secret_env "
        "(BL-20260701-ALPACA-LIVE-SECRET-ENV)"
    )
