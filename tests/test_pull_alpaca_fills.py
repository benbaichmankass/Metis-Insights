"""FIX-CA-31 (CA-B08-pull-alpaca-fills-collapses-api-failure-as-empty).

Before this fix, ``_fetch_page`` returned ``[]`` identically for a genuine
empty page AND for any Alpaca API failure (auth error, rate limit, 5xx, or
a network exception — ``AlpacaClient._request`` never raises), and the
``--all-alpaca-accounts`` loop counted every attempted account as ``ran``
regardless of outcome, so a 100%-failing account read as a clean pull.

Mirrors ``tests/test_exchange_fills_alpaca.py``'s coverage pattern for the
puller's fail-soft/fail-loud loop, and the ``ok/failed/skipped`` shape
already proven in ``scripts/pull_exchange_fills.py``'s Bybit sibling.
"""
from __future__ import annotations

import pytest

import scripts.pull_alpaca_fills as pa
from src.runtime.exchange_accounts import AlpacaFillAccount
from src.units.accounts.alpaca_client import AlpacaClient


# ---------------------------------------------------------------------------
# _fetch_page (inside _pull_one_account): API failure vs genuine empty page
# ---------------------------------------------------------------------------


def test_pull_one_account_raises_on_api_failure(monkeypatch, tmp_path):
    """A non-zero retCode must raise, distinguishably from an empty page."""
    monkeypatch.setattr(
        AlpacaClient, "account_activities",
        lambda self, **kw: {"retCode": -1, "retMsg": "network error: boom"},
    )
    with pytest.raises(pa.AlpacaFillsPageUnavailable):
        pa._pull_one_account(
            account_id="alpaca_paper", api_key="k", api_secret="s", env="paper",
            days=7, fills_path=tmp_path / "fills.sqlite",
        )


def test_pull_one_account_genuine_empty_page_returns_zero(monkeypatch, tmp_path):
    """A genuine empty page (retCode 0, no rows) stays a clean, non-raising 0."""
    monkeypatch.setattr(
        AlpacaClient, "account_activities",
        lambda self, **kw: {"retCode": 0, "result": []},
    )
    inserted = pa._pull_one_account(
        account_id="alpaca_paper", api_key="k", api_secret="s", env="paper",
        days=7, fills_path=tmp_path / "fills.sqlite",
    )
    assert inserted == 0


# ---------------------------------------------------------------------------
# --all-alpaca-accounts loop: a failed account must not read as clean coverage
# ---------------------------------------------------------------------------


def _fake_accounts():
    return [AlpacaFillAccount("alpaca_ok", "K_OK", "S_OK", "paper")]


def test_all_alpaca_loop_reports_failed_account_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(pa, "live_alpaca_fill_accounts", _fake_accounts)
    monkeypatch.setenv("K_OK", "k")
    monkeypatch.setenv("S_OK", "s")
    monkeypatch.setattr(
        AlpacaClient, "account_activities",
        lambda self, **kw: {"retCode": -1, "retMsg": "auth error"},
    )
    rc = pa.main(["--all-alpaca-accounts", "--fills-db", str(tmp_path / "fills.sqlite")])
    # Must fail on current main (reproduced: both cases currently return 0).
    assert rc == 1


def test_all_alpaca_loop_genuine_empty_page_stays_zero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(pa, "live_alpaca_fill_accounts", _fake_accounts)
    monkeypatch.setenv("K_OK", "k")
    monkeypatch.setenv("S_OK", "s")
    monkeypatch.setattr(
        AlpacaClient, "account_activities",
        lambda self, **kw: {"retCode": 0, "result": []},
    )
    rc = pa.main(["--all-alpaca-accounts", "--fills-db", str(tmp_path / "fills.sqlite")])
    assert rc == 0


def test_all_alpaca_loop_skips_missing_creds(monkeypatch, tmp_path):
    """Regression guard: a missing-cred account is still SKIPPED, not FAILED."""
    ran = []
    monkeypatch.setattr(pa, "live_alpaca_fill_accounts", _fake_accounts)
    monkeypatch.setattr(
        pa, "_pull_one_account",
        lambda **kw: (ran.append(kw["account_id"]) or 2),
    )
    monkeypatch.delenv("K_OK", raising=False)
    monkeypatch.delenv("S_OK", raising=False)
    assert pa.main(["--all-alpaca-accounts"]) == 2  # nothing ran (skipped, not failed)
    assert ran == []


def test_all_alpaca_all_missing_returns_2(monkeypatch):
    monkeypatch.setattr(pa, "live_alpaca_fill_accounts", _fake_accounts)
    monkeypatch.setattr(pa, "_pull_one_account", lambda **kw: 0)
    for var in ("K_OK", "S_OK"):
        monkeypatch.delenv(var, raising=False)
    assert pa.main(["--all-alpaca-accounts"]) == 2
