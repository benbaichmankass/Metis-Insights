"""ORDER-AUDIT-3 / PI-20260927-ECILBBVH-0005: ``account_scope`` must narrow an
account that declares no ``strategies:`` key.

``_dispatch_exclusion_reason`` returned ``None`` ("legacy / no-mapping
account") BEFORE it reached the account_scope check, so such an account
received every package even when the round was scoped to other accounts --
contradicting ``multi_account_execute``'s contract that scope NARROWS ONLY.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage

_RISK = """
    risk:
      max_dd_pct: 0.05
      daily_usd: 100
      pos_size: 500
      risk_pct: 0.01
      min_balance_usd: 50
"""

_YAML = textwrap.dedent("""\
accounts:
  mapped:
    type: regular
    exchange: bybit
    api_key_env: BYBIT_KEY_OK
    strategies: [turtle_soup]
""" + textwrap.indent(textwrap.dedent(_RISK), "    ") + """\
  unmapped:
    type: regular
    exchange: bybit
    api_key_env: BYBIT_KEY_OK
""" + textwrap.indent(textwrap.dedent(_RISK), "    "))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BYBIT_KEY_OK", "test-value")
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    p = tmp_path / "accounts.yaml"
    p.write_text(_YAML)
    with patch("src.units.accounts.execute.execute_pkg",
               side_effect=lambda pkg, account_cfg, **kw: f"dry-{account_cfg['account_id']}") as m:
        yield Coordinator(), str(p), m


def _pkg():
    return OrderPackage(strategy="turtle_soup", symbol="BTCUSDT", direction="long",
                        entry=50_000.0, sl=49_500.0, tp=51_000.0, confidence=1.0,
                        meta={"strategy_name": "turtle_soup", "is_test": True,
                              "test_qty": 0.001})


def _dispatched(m):
    return sorted(c.args[1]["account_id"] for c in m.call_args_list)


def test_scope_narrows_an_account_without_a_strategies_key(env):
    coord, path, m = env
    coord.multi_account_execute(_pkg(), accounts_path=path, dry_run=True,
                                balance_fetcher=lambda _a: 10_000.0,
                                account_scope={"mapped"})
    assert _dispatched(m) == ["mapped"]


def test_unscoped_dispatch_is_unchanged(env):
    """Control: with no scope in play the legacy no-mapping account still gets
    the package, exactly as before."""
    coord, path, m = env
    coord.multi_account_execute(_pkg(), accounts_path=path, dry_run=True,
                                balance_fetcher=lambda _a: 10_000.0)
    assert _dispatched(m) == ["mapped", "unmapped"]


# ── ORDER-AUDIT-3 / PI-20260930-MEK2XMWT-0006 ──────────────────────────────
# accounts_loader (used by the per-account open-package gate) honours
# ACCOUNTS_YAML_PATH; dispatch ignored it and always read config/accounts.yaml.
def test_dispatch_honours_accounts_yaml_path_like_the_gate(env, monkeypatch):
    coord, path, m = env
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", path)
    from src.config.accounts_loader import load_accounts_dict
    assert set(load_accounts_dict()) == {"mapped", "unmapped"}  # the gate's view
    coord.multi_account_execute(_pkg(), dry_run=True,
                                balance_fetcher=lambda _a: 10_000.0)
    assert _dispatched(m) == ["mapped", "unmapped"]


def test_an_explicit_path_still_wins(env, monkeypatch, tmp_path):
    coord, path, m = env
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(tmp_path / "does_not_exist.yaml"))
    coord.multi_account_execute(_pkg(), accounts_path=path, dry_run=True,
                                balance_fetcher=lambda _a: 10_000.0)
    assert _dispatched(m) == ["mapped", "unmapped"]
