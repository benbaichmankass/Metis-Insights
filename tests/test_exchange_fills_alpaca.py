"""Tests for the Alpaca fills adapter (broker-truth cost coverage rec #7):
the activity→row mapper, cursor pagination, the config-driven account
enumeration, and the puller's fail-soft loop.
"""
from __future__ import annotations

import textwrap
from datetime import datetime, timezone

import pytest

from src.runtime.exchange_accounts import AlpacaFillAccount, live_alpaca_fill_accounts
from src.runtime.exchange_fills_alpaca import (
    alpaca_fill_activity_to_row,
    fetch_alpaca_fills,
)

import scripts.pull_alpaca_fills as pa


def _fill(**over):
    base = {
        "id": "exec-1",
        "activity_type": "FILL",
        "transaction_time": "2026-07-28T14:30:00Z",
        "type": "fill",
        "price": "374.5",
        "qty": "10",
        "side": "buy",
        "symbol": "SPY",
        "order_id": "ord-1",
    }
    base.update(over)
    return base


# ---- mapper ---------------------------------------------------------------

def test_maps_valid_fill():
    row = alpaca_fill_activity_to_row(_fill(), "alpaca_paper")
    assert row == {
        "exec_id": "exec-1",
        "account_id": "alpaca_paper",
        "symbol": "SPY",
        "side": "buy",
        "price": 374.5,
        "qty": 10.0,
        "fee": 0.0,
        "fee_currency": "USD",
        "exec_time": "2026-07-28T14:30:00Z",
        "order_id": "ord-1",
        "is_maker": 0,
    }


def test_sell_short_normalises_to_sell():
    assert alpaca_fill_activity_to_row(_fill(side="sell_short"), "a")["side"] == "sell"
    assert alpaca_fill_activity_to_row(_fill(side="sell"), "a")["side"] == "sell"


@pytest.mark.parametrize("bad", [
    {"activity_type": "FEE"},        # not a FILL
    {"id": None},                     # missing exec id
    {"symbol": None},                 # missing symbol
    {"side": "weird"},                # unmappable side
    {"price": "notanumber"},          # unparseable price
    {"qty": "0"},                     # non-positive qty
    {"transaction_time": None},       # missing time
])
def test_bad_activity_skipped(bad):
    assert alpaca_fill_activity_to_row(_fill(**bad), "a") is None


# ---- pagination -----------------------------------------------------------

def test_fetch_paginates_and_stops_on_short_page():
    # Two full pages of 2 then a short page → cursor follows the last id.
    pages = {
        None: [_fill(id="a"), _fill(id="b")],
        "b": [_fill(id="c"), _fill(id="d")],
        "d": [_fill(id="e")],  # short page (< page_size) → stop
    }
    seen_tokens = []

    def fetch_page(*, after, page_token):
        seen_tokens.append(page_token)
        return pages.get(page_token, [])

    rows = fetch_alpaca_fills(
        fetch_page, account_id="alpaca_paper", days=7, page_size=2,
        now=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    assert [r["exec_id"] for r in rows] == ["a", "b", "c", "d", "e"]
    assert seen_tokens == [None, "b", "d"]  # followed the last-id cursor


def test_fetch_after_bound_is_rfc3339_z():
    captured = {}

    def fetch_page(*, after, page_token):
        captured["after"] = after
        return []

    fetch_alpaca_fills(
        fetch_page, account_id="a", days=7,
        now=datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert captured["after"] == "2026-07-22T12:00:00Z"


def test_fetch_max_pages_backstop():
    calls = {"n": 0}

    def fetch_page(*, after, page_token):
        calls["n"] += 1
        return [_fill(id=f"x{calls['n']}"), _fill(id=f"y{calls['n']}")]  # never short

    fetch_alpaca_fills(
        fetch_page, account_id="a", days=7, page_size=2, max_pages=3,
        now=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    assert calls["n"] == 3  # stopped at the backstop, not an infinite loop


# ---- enumeration ----------------------------------------------------------

_ACCOUNTS_YAML = textwrap.dedent(
    """
    accounts:
      alpaca_paper:
        exchange: alpaca
        mode: live
      alpaca_portfolio:
        exchange: alpaca
        mode: live
        alpaca_env: paper
        api_key_env: ALPACA_API_KEY_PAPER_PORTFOLIO
        api_secret_env: ALPACA_API_SECRET_KEY_PAPER_PORTFOLIO
      alpaca_options_paper:
        exchange: alpaca
        mode: live
        api_key_env: ALPACA_API_KEY_ID_OPTIONS
        api_secret_env: ALPACA_API_SECRET_KEY_OPTIONS
      alpaca_live:
        exchange: alpaca
        mode: dry_run
        alpaca_env: live
      bybit_1:
        exchange: bybit
        mode: live
    """
).strip()


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML, encoding="utf-8")
    return p


def test_enumerates_live_alpaca_accounts(accounts_yaml):
    accts = {a.account_id: a for a in live_alpaca_fill_accounts(path=accounts_yaml)}
    assert set(accts) == {"alpaca_paper", "alpaca_portfolio", "alpaca_options_paper"}
    # default creds when unspecified
    assert accts["alpaca_paper"] == AlpacaFillAccount(
        "alpaca_paper", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "paper",
    )
    # explicit per-account creds
    assert accts["alpaca_portfolio"].key_env == "ALPACA_API_KEY_PAPER_PORTFOLIO"
    assert accts["alpaca_portfolio"].secret_env == "ALPACA_API_SECRET_KEY_PAPER_PORTFOLIO"
    assert accts["alpaca_options_paper"].env == "paper"  # unspecified → paper


# ---- puller fail-soft loop ------------------------------------------------

def _fake_accounts():
    return [
        AlpacaFillAccount("alpaca_ok", "K_OK", "S_OK", "paper"),
        AlpacaFillAccount("alpaca_missing", "K_MISS", "S_MISS", "paper"),
    ]


def test_all_alpaca_loop_skips_missing_creds(monkeypatch):
    ran = []
    monkeypatch.setattr(pa, "live_alpaca_fill_accounts", _fake_accounts)
    monkeypatch.setattr(pa, "_pull_one_account", lambda **kw: (ran.append(kw["account_id"]) or 2))
    monkeypatch.setenv("K_OK", "k")
    monkeypatch.setenv("S_OK", "s")
    monkeypatch.delenv("K_MISS", raising=False)
    monkeypatch.delenv("S_MISS", raising=False)

    assert pa.main(["--all-alpaca-accounts"]) == 0
    assert ran == ["alpaca_ok"]


def test_all_alpaca_all_missing_returns_2(monkeypatch):
    monkeypatch.setattr(pa, "live_alpaca_fill_accounts", _fake_accounts)
    monkeypatch.setattr(pa, "_pull_one_account", lambda **kw: 0)
    for var in ("K_OK", "S_OK", "K_MISS", "S_MISS"):
        monkeypatch.delenv(var, raising=False)
    assert pa.main(["--all-alpaca-accounts"]) == 2
