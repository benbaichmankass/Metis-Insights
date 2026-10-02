"""ORDER-AUDIT-2 item 3 (AUD-20260927-CA-A04-monitor-dry-run-gate-reads-absent-attribute).

``order_monitor._build_account_client`` set ``cfg["mode"]`` from
``getattr(acc, "mode", "live")`` — but ``TradingAccount`` has no ``.mode``; the
resolved state is ``acc.dry_run``. Every account resolved to "live", so the
monitor's dry_run short-circuit on close / modify could never fire, and a
``mode: dry_run`` account holding an open row would have had real close orders
sent. These tests build the cfg from a REAL ``TradingAccount`` loaded from YAML
— the earlier tests used MagicMock accounts, which fabricate ``.mode`` and so
could not see the bug.
"""
from __future__ import annotations

import textwrap

import pytest

import src.runtime.order_monitor as om
from src.units.accounts import load_accounts

_YAML = textwrap.dedent("""\
    accounts:
      acct_dry:
        type: regular
        exchange: bybit
        api_key_env: OA2_KEY
        mode: dry_run      # dry-run-guard: allow — test fixture
        market_type: linear
        strategies: [vwap]
        risk: {risk_pct: 0.01}
      acct_live:
        type: regular
        exchange: bybit
        api_key_env: OA2_KEY
        mode: live
        market_type: linear
        strategies: [vwap]
        risk: {risk_pct: 0.01}
""")


@pytest.fixture()
def real_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("OA2_KEY", "k")
    p = tmp_path / "accounts.yaml"
    p.write_text(_YAML)
    accs = load_accounts(str(p))
    import src.units.accounts as _ua
    monkeypatch.setattr(_ua, "load_accounts", lambda *a, **k: accs)
    return {a.name: a for a in accs}


def test_real_trading_account_has_no_mode_attribute(real_accounts):
    # The premise of the bug — if this ever changes, revisit _account_mode.
    assert not hasattr(real_accounts["acct_dry"], "mode")
    assert real_accounts["acct_dry"].dry_run is True
    assert real_accounts["acct_live"].dry_run is False


def test_account_mode_reads_dry_run(real_accounts):
    assert om._account_mode(real_accounts["acct_dry"]) == "dry_run"
    assert om._account_mode(real_accounts["acct_live"]) == "live"


def test_build_account_client_cfg_mode_from_real_account(real_accounts, monkeypatch):
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "bybit_client_for", lambda cfg: object())
    _, cfg_dry = om._build_account_client("acct_dry")
    _, cfg_live = om._build_account_client("acct_live")
    assert cfg_dry["mode"] == "dry_run"
    assert cfg_live["mode"] == "live"


def test_close_on_dry_run_account_sends_nothing(real_accounts, monkeypatch):
    import src.units.accounts.clients as _clients
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_clients, "bybit_client_for", lambda cfg: object())
    sent = []
    monkeypatch.setattr(_exec, "close_open_position",
                        lambda *a, **k: sent.append((a, k)) or {"ok": True})
    trade = {"id": 1, "account_id": "acct_dry", "symbol": "BTCUSDT",
             "direction": "long", "position_size": 0.01}
    res = om._send_close_to_exchange(trade)
    assert res.get("skipped") == "dry_run"
    assert sent == []
    # Positive control: the live account DOES reach close_open_position.
    om._send_close_to_exchange({**trade, "account_id": "acct_live"})
    assert len(sent) == 1
