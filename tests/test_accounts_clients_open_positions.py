"""BUG-042 PR 1 — foundation: ``account_open_positions`` lifted from
``src/units/ui/data_loaders.py`` to ``src/units/accounts/clients.py``.

Per CLAUDE.md § "Architecture rules" § 3, per-account exchange-state
reads are the accounts unit's responsibility. The UI unit previously
owned this helper, which forced every consumer (Telegram bot,
coordinator, monitor loop) to reach across the unit boundary. The
lift fixes the wrong-direction import and gives the upcoming BUG-042
reconciler a direct dependency on the accounts unit.

Five contracts under test:

1. Behaviour-preserving lift — bybit happy path returns the same
   shape the legacy helper produced.
2. Missing creds (``bybit_client_for`` returns ``None``) → ``None``.
3. Exchange SDK exception → ``None`` + warning + ``report_api_failure``
   call (in-line, since the orig path swallowed exceptions).
4. Non-bybit / non-binance exchange → ``None``.
5. Legacy UI delegate (``data_loaders.account_open_positions``) still
   works and returns the new implementation's output.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.units.accounts.clients import account_open_positions
from src.units.ui.data_loaders import (
    account_open_positions as legacy_account_open_positions,
)


@pytest.fixture
def bybit_account():
    # ``market_type: linear`` preserves the pre-2026-05-06 perp-position
    # behaviour these tests pin: ``account_open_positions`` queries the
    # v5 ``/position/list`` endpoint with ``category="linear"``. Spot
    # accounts return ``[]`` without a network call (covered by
    # ``test_spot_category_routing.py``).
    return {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "market_type": "linear",
    }


# ---------------------------------------------------------------------------
# 1. Behaviour-preserving lift — bybit happy path
# ---------------------------------------------------------------------------


class TestBybitHappyPath:
    """The lifted implementation must produce the same shape the
    pre-lift ``data_loaders`` version did.
    """

    def test_returns_size_filtered_positions_with_canonical_keys(
        self, bybit_account,
    ):
        # Stub the bybit client so the test runs without exchange
        # creds. ``get_positions`` mirrors the live response shape:
        # ``{"result": {"list": [{...}, ...]}}``.
        fake_resp = {
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.005",
                        "avgPrice": "78250.4",
                        "unrealisedPnl": "1.23",
                    },
                    {
                        # zero-size row → must be filtered out
                        "symbol": "ETHUSDT",
                        "side": "Sell",
                        "size": "0",
                        "avgPrice": "3200.0",
                        "unrealisedPnl": "0",
                    },
                    {
                        "symbol": "SOLUSDT",
                        "side": "Sell",
                        "size": "12",
                        "avgPrice": "150.0",
                        "unrealisedPnl": "-0.4",
                    },
                ],
            },
        }

        class _FakeClient:
            def get_positions(self, **kw):
                return fake_resp

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_FakeClient(),
        ):
            out = account_open_positions(bybit_account)

        assert out is not None
        assert len(out) == 2  # zero-size filtered
        btc, sol = out
        assert btc == {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": 0.005,
            "entry_price": 78250.4,
            "unrealised_pnl": 1.23,
        }
        assert sol["symbol"] == "SOLUSDT"
        assert sol["size"] == 12.0


# ---------------------------------------------------------------------------
# 2. Missing-creds path
# ---------------------------------------------------------------------------


def test_missing_creds_returns_none(bybit_account):
    """When ``bybit_client_for`` returns ``None`` (creds missing) the
    helper must return ``None`` — distinguishable from ``[]`` which
    would mean "no open positions".
    """
    with patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=None,
    ):
        out = account_open_positions(bybit_account)

    assert out is None


# ---------------------------------------------------------------------------
# 3. Exception path — must not raise
# ---------------------------------------------------------------------------


def test_exchange_exception_returns_none_and_reports(bybit_account):
    """The original implementation logged + reported via
    ``report_api_failure`` and returned ``None``. The lift must
    preserve both halves of that contract.
    """

    class _BoomClient:
        def get_positions(self, **kw):
            raise RuntimeError("simulated bybit 5xx")

    with patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=_BoomClient(),
    ), patch(
        "src.runtime.api_reporting.report_api_failure",
    ) as report_mock:
        out = account_open_positions(bybit_account)

    assert out is None
    report_mock.assert_called_once()
    kw = report_mock.call_args.kwargs
    assert kw["exchange"] == "bybit"
    assert kw["op"] == "get_positions"
    assert kw["account_id"] == "bybit_2"
    assert "RuntimeError" in kw["error"]


# ---------------------------------------------------------------------------
# 4. Unsupported exchange / bad arg
# ---------------------------------------------------------------------------


def test_unsupported_exchange_returns_none():
    out = account_open_positions({
        "account_id": "x1",
        "exchange": "not_a_real_exchange",
    })
    assert out is None


def test_non_dict_argument_returns_none():
    assert account_open_positions(None) is None
    assert account_open_positions("bybit_2") is None
    assert account_open_positions(42) is None


# ---------------------------------------------------------------------------
# 5. Legacy UI delegate
# ---------------------------------------------------------------------------


class TestLegacyUiDelegate:
    """``data_loaders.account_open_positions`` must keep working —
    existing callers (Telegram bot ``/balance``, dashboards) import
    from there.
    """

    def test_legacy_delegate_returns_lifted_implementations_output(
        self, bybit_account,
    ):
        fake_resp = {
            "result": {
                "list": [{
                    "symbol": "BTCUSDT", "side": "Buy",
                    "size": "0.01", "avgPrice": "80000",
                    "unrealisedPnl": "0",
                }],
            },
        }

        class _FakeClient:
            def get_positions(self, **kw):
                return fake_resp

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_FakeClient(),
        ):
            out = legacy_account_open_positions(bybit_account)

        assert out is not None
        assert len(out) == 1
        assert out[0]["symbol"] == "BTCUSDT"
        assert out[0]["size"] == 0.01
