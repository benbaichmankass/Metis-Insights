"""BL-20260611-006 — OANDA/Alpaca balance wiring for the risk gate.

Before this fix, ``account_balance_with_diagnostic`` returned
``"unsupported"`` for the two M15 exchanges and ``execute._fetch_balance``
fell through to ``0.0`` — so the coordinator's live-balance cache stayed
empty, the sizer saw ``gate_balance=0.00`` (a non-positive balance,
which the only balance gate refuses), and every gold/ETF signal was
refused (trade #2536, xauusd_trend_1h's first executable signal,
2026-06-11 18:01Z).
"""
from __future__ import annotations

import pytest

from src.units.accounts.execute import _fetch_balance
from src.units.ui.data_loaders import account_balance_with_diagnostic


class _StubBrokerClient:
    """Stand-in for OandaClient / AlpacaClient: balance() -> float | None."""

    def __init__(self, bal):
        self._bal = bal

    def balance(self):
        return self._bal


# ---------------------------------------------------------------------------
# account_balance_with_diagnostic (coordinator live-balance cache path)
# ---------------------------------------------------------------------------


class TestDiagnosticBranches:
    def _patch_factory(self, monkeypatch, name, value):
        import src.units.accounts.clients as clients
        monkeypatch.setattr(clients, name, lambda account: value)

    @pytest.mark.parametrize("ex,factory", [
        ("oanda", "oanda_client_for"),
        ("alpaca", "alpaca_client_for"),
    ])
    def test_ok_balance_flows_through(self, monkeypatch, ex, factory):
        self._patch_factory(monkeypatch, factory, _StubBrokerClient(99876.54))
        diag = account_balance_with_diagnostic(
            {"exchange": ex, "account_id": f"{ex}_acct"})
        assert diag["status"] == "ok"
        assert diag["total_usdt"] == pytest.approx(99876.54)

    @pytest.mark.parametrize("ex,factory", [
        ("oanda", "oanda_client_for"),
        ("alpaca", "alpaca_client_for"),
    ])
    def test_missing_creds_named(self, monkeypatch, ex, factory):
        self._patch_factory(monkeypatch, factory, None)
        diag = account_balance_with_diagnostic(
            {"exchange": ex, "account_id": f"{ex}_acct"})
        assert diag["status"] == "missing_creds"
        assert "not in process env" in diag["error"]

    def test_none_balance_is_api_error(self, monkeypatch):
        self._patch_factory(
            monkeypatch, "oanda_client_for", _StubBrokerClient(None))
        diag = account_balance_with_diagnostic(
            {"exchange": "oanda", "account_id": "oanda_practice"})
        assert diag["status"] == "api_error"
        assert "returned None" in diag["error"]

    def test_no_generic_cred_check_false_negative(self, monkeypatch):
        """The M15 branches dispatch BEFORE credentials_check (fixed env
        names, no api_key_env) — an account dict without api_key_env must
        not produce the 'no api_key_env configured' false negative."""
        self._patch_factory(
            monkeypatch, "oanda_client_for", _StubBrokerClient(100000.0))
        diag = account_balance_with_diagnostic(
            {"exchange": "oanda", "account_id": "oanda_practice"})
        assert diag["status"] == "ok"


# ---------------------------------------------------------------------------
# execute._fetch_balance (execute_pkg standalone path)
# ---------------------------------------------------------------------------


class TestFetchBalanceBranches:
    def test_oanda_balance(self):
        bal = _fetch_balance(
            _StubBrokerClient(100000.0), {"exchange": "oanda"})
        assert bal == pytest.approx(100000.0)

    def test_alpaca_balance(self):
        bal = _fetch_balance(
            _StubBrokerClient(25000.5), {"exchange": "alpaca"})
        assert bal == pytest.approx(25000.5)

    def test_none_balance_defaults_zero(self):
        assert _fetch_balance(
            _StubBrokerClient(None), {"exchange": "oanda"}) == 0.0

    def test_none_client_defaults_zero(self):
        assert _fetch_balance(None, {"exchange": "alpaca"}) == 0.0
