"""Tests for the config-driven Bybit account enumeration used by the
broker-truth fills/funding pullers (rec #7 multi-account rollout).
"""
from __future__ import annotations

import textwrap

import pytest

import scripts.pull_exchange_fills as pf
import scripts.pull_exchange_funding as pfu
from src.runtime.exchange_accounts import (
    BybitFillAccount,
    _secret_env_for,
    live_bybit_fill_accounts,
)

_ACCOUNTS_YAML = textwrap.dedent(
    """
    accounts:
      bybit_1:
        exchange: bybit
        mode: live
        account_class: paper
        market_type: linear
        api_key_env: BYBIT_API_KEY_1
        symbols: [BTCUSDT, ETHUSDT]
      bybit_2:
        exchange: bybit
        mode: live
        account_class: real_money
        market_type: linear
        api_key_env: BYBIT_API_KEY_2
        symbols: [BTCUSDT]
      bybit_portfolio:
        exchange: bybit
        mode: live
        account_class: paper
        market_type: linear
        api_key_env: BYBIT_API_KEY_3
        symbols: [BTCUSDT, XRPUSDT]
      bybit_shelved:
        exchange: bybit
        mode: dry_run
        market_type: linear
        api_key_env: BYBIT_API_KEY_9
      bybit_spot:
        exchange: bybit
        mode: live
        market_type: spot
        api_key_env: MY_KEY
        api_secret_env: MY_EXPLICIT_SECRET
      alpaca_paper:
        exchange: alpaca
        mode: live
    """
).strip()


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML, encoding="utf-8")
    return p


def test_enumerates_only_live_bybit_accounts(accounts_yaml):
    accts = live_bybit_fill_accounts(path=accounts_yaml)
    ids = {a.account_id for a in accts}
    # Only live Bybit accounts; dry_run + non-bybit excluded.
    assert ids == {"bybit_1", "bybit_2", "bybit_portfolio", "bybit_spot"}


def test_credentials_and_category_resolved_per_account(accounts_yaml):
    by_id = {a.account_id: a for a in live_bybit_fill_accounts(path=accounts_yaml)}

    assert by_id["bybit_1"] == BybitFillAccount(
        account_id="bybit_1",
        key_env="BYBIT_API_KEY_1",
        secret_env="BYBIT_API_SECRET_1",
        category="linear",
        symbols=("BTCUSDT", "ETHUSDT"),
    )
    # portfolio uses BYBIT_API_KEY_3 → BYBIT_API_SECRET_3.
    assert by_id["bybit_portfolio"].secret_env == "BYBIT_API_SECRET_3"
    assert by_id["bybit_portfolio"].symbols == ("BTCUSDT", "XRPUSDT")
    # spot market_type resolves category "spot"; explicit api_secret_env wins.
    assert by_id["bybit_spot"].category == "spot"
    assert by_id["bybit_spot"].secret_env == "MY_EXPLICIT_SECRET"


def test_missing_config_returns_empty(tmp_path):
    assert live_bybit_fill_accounts(path=tmp_path / "does_not_exist.yaml") == []


@pytest.mark.parametrize(
    "key_env,expected",
    [
        ("BYBIT_API_KEY_2", "BYBIT_API_SECRET_2"),
        ("BYBIT_API_KEY", "BYBIT_API_SECRET"),
        ("SOME_KEY_1", "SOME_SECRET_1"),
        ("WEIRDNAME", "WEIRDNAME_SECRET"),
    ],
)
def test_secret_env_derivation(key_env, expected):
    assert _secret_env_for(key_env) == expected


def _fake_accounts():
    return [
        BybitFillAccount("acct_ok", "K_OK", "S_OK", "linear", ("BTCUSDT",)),
        BybitFillAccount("acct_missing", "K_MISS", "S_MISS", "linear", ()),
    ]


def test_fills_all_accounts_loop_skips_missing_creds(monkeypatch):
    """--all-bybit-accounts runs the cred-present account and skips the other,
    fail-soft, without touching ccxt (the pull helper is stubbed)."""
    calls = []
    monkeypatch.setattr(pf, "live_bybit_fill_accounts", _fake_accounts)
    monkeypatch.setattr(
        pf, "_pull_one_account",
        lambda **kw: (calls.append(kw["account_id"]) or 3),
    )
    monkeypatch.setenv("K_OK", "key")
    monkeypatch.setenv("S_OK", "secret")
    monkeypatch.delenv("K_MISS", raising=False)
    monkeypatch.delenv("S_MISS", raising=False)

    rc = pf.main(["--all-bybit-accounts"])
    assert rc == 0
    assert calls == ["acct_ok"]  # missing-cred account skipped, not aborted


def test_fills_all_accounts_all_missing_returns_2(monkeypatch):
    monkeypatch.setattr(pf, "live_bybit_fill_accounts", _fake_accounts)
    monkeypatch.setattr(pf, "_pull_one_account", lambda **kw: 0)
    for var in ("K_OK", "S_OK", "K_MISS", "S_MISS"):
        monkeypatch.delenv(var, raising=False)
    assert pf.main(["--all-bybit-accounts"]) == 2


def test_funding_all_accounts_passes_per_account_symbols(monkeypatch):
    """The funding loop hands each account its own declared symbols."""
    seen = {}
    monkeypatch.setattr(pfu, "live_bybit_fill_accounts", _fake_accounts)
    monkeypatch.setattr(
        pfu, "_pull_one_account",
        lambda **kw: (seen.__setitem__(kw["account_id"], kw["symbols"]) or 1),
    )
    monkeypatch.setenv("K_OK", "key")
    monkeypatch.setenv("S_OK", "secret")
    monkeypatch.delenv("K_MISS", raising=False)
    monkeypatch.delenv("S_MISS", raising=False)

    rc = pfu.main(["--all-bybit-accounts"])
    assert rc == 0
    # acct_ok has symbols ("BTCUSDT",) → passed as a list; acct_missing skipped.
    assert seen == {"acct_ok": ["BTCUSDT"]}
