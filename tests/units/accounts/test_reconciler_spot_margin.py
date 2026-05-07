"""S-047 T5 (D7) — reconciler spot-margin awareness.

Pins the contract for ``account_open_positions`` on
``market_type: spot-margin`` accounts. The BUG-042 reconciler in
``src/runtime/order_monitor.py::_reconcile_open_trades`` matches
DB-open trades against the list this function returns; before T5,
all spot accounts (cash AND margin) returned ``[]`` → reconciler
orphaned every live spot-margin trade on every tick.

T5 teaches the helper to distinguish:

  - **cash spot** (``market_type: spot``): keeps returning ``[]``.
    Coin holdings sit as ``walletBalance`` paid for with cash USDT;
    no exchange-side "position" exists.
  - **spot margin** (``market_type: spot-margin``): synthesise
    positions from ``walletBalance`` (long side) + ``borrowAmount``
    (short side) per coin, excluding USDT itself. Each
    ``(symbol, side)`` pair the reconciler reads matches the same
    pair on the DB-open trade row.

Linear/inverse paths are unaffected — they still hit
``client.get_positions(category=…)``.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

# pipeline.py needs matplotlib at import time via signal_notifications.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()


# ---------------------------------------------------------------------------
# Fixtures — fake Bybit client
# ---------------------------------------------------------------------------


class _FakeBybitClient:
    """Replays a fixed ``get_wallet_balance`` response. Captures any
    ``get_positions`` call so tests can assert spot-margin doesn't
    accidentally hit the perp endpoint.
    """

    def __init__(self, *, wallet_response: dict):
        self._wallet_response = wallet_response
        self.get_positions_calls: list = []

    def get_wallet_balance(self, **kwargs):
        return self._wallet_response

    def get_positions(self, **kwargs):  # noqa: D401
        self.get_positions_calls.append(kwargs)
        return {"result": {"list": []}}


def _wallet_with(coins: list[dict]) -> dict:
    return {"result": {"list": [{"coin": coins}]}}


def _spot_margin_account(name: str = "bybit_2") -> dict:
    """Account dict shape ``account_open_positions`` consumes."""
    return {
        "account_id": name,
        "exchange": "bybit",
        "market_type": "spot-margin",
        "api_key_env": "BYBIT_API_KEY_2",
        "api_secret_env": "BYBIT_API_SECRET_2",
    }


def _cash_spot_account(name: str = "bybit_1") -> dict:
    return {
        "account_id": name,
        "exchange": "bybit",
        "market_type": "spot",
        "api_key_env": "BYBIT_API_KEY",
        "api_secret_env": "BYBIT_API_SECRET",
    }


# ---------------------------------------------------------------------------
# Spot-margin synthesis — the contract introduced in T5
# ---------------------------------------------------------------------------


class TestSpotMarginPositionSynthesis:
    """``account_open_positions`` on a spot-margin account returns the
    list synthesised from wallet borrows + holdings."""

    def test_btc_borrow_emits_short_position(self, monkeypatch):
        """``BTC.borrowAmount > 0`` → exchange-side BTCUSDT short."""
        from src.units.accounts import clients
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {
                "coin": "USDT", "walletBalance": "177", "locked": "0",
                "borrowAmount": "0", "usdValue": "177",
            },
            {
                "coin": "BTC", "walletBalance": "0", "locked": "0",
                "borrowAmount": "0.001", "usdValue": "50",
            },
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        positions = clients.account_open_positions(_spot_margin_account())
        assert isinstance(positions, list)
        assert {"symbol": "BTCUSDT", "side": "short"} in [
            {"symbol": p["symbol"], "side": p["side"]} for p in positions
        ]
        short = next(p for p in positions if p["side"] == "short")
        assert short["size"] == pytest.approx(0.001)

    def test_btc_wallet_emits_long_position(self, monkeypatch):
        """``BTC.walletBalance > 0`` → exchange-side BTCUSDT long.
        Wallet base-coin holdings could stem from a manual deposit OR
        a leveraged buy still open; the reconciler treats both as
        "still live, do not orphan".
        """
        from src.units.accounts import clients
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {
                "coin": "USDT", "walletBalance": "100", "locked": "0",
                "borrowAmount": "0", "usdValue": "100",
            },
            {
                "coin": "BTC", "walletBalance": "0.005", "locked": "0",
                "borrowAmount": "0", "usdValue": "250",
            },
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        positions = clients.account_open_positions(_spot_margin_account())
        long = next(p for p in positions if p["side"] == "long")
        assert long["symbol"] == "BTCUSDT"
        assert long["size"] == pytest.approx(0.005)

    def test_simultaneous_borrow_and_wallet_emits_both_sides(self, monkeypatch):
        """A wallet snapshot can carry both sides simultaneously
        (long that's been opened on top of an earlier short being
        unwound, mid-flip). Both are emitted; the reconciler matches
        DB rows independently.
        """
        from src.units.accounts import clients
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {"coin": "USDT", "walletBalance": "50", "locked": "0"},
            {
                "coin": "BTC", "walletBalance": "0.002",
                "borrowAmount": "0.001",
            },
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        positions = clients.account_open_positions(_spot_margin_account())
        sides = sorted(p["side"] for p in positions if p["symbol"] == "BTCUSDT")
        assert sides == ["long", "short"]

    def test_multiple_base_coins_each_gets_own_symbol(self, monkeypatch):
        from src.units.accounts import clients
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {"coin": "USDT", "walletBalance": "100"},
            {"coin": "BTC", "borrowAmount": "0.001"},
            {"coin": "ETH", "borrowAmount": "0.5"},
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        positions = clients.account_open_positions(_spot_margin_account())
        symbols = sorted(p["symbol"] for p in positions)
        assert symbols == ["BTCUSDT", "ETHUSDT"]

    def test_empty_wallet_returns_empty_list(self, monkeypatch):
        """Wallet with only USDT and no borrows on any base coin
        returns ``[]`` — fully flat exchange-side."""
        from src.units.accounts import clients
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {"coin": "USDT", "walletBalance": "100"},
            {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0"},
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        positions = clients.account_open_positions(_spot_margin_account())
        assert positions == []

    def test_usdt_excluded_from_synthesis(self, monkeypatch):
        """USDT is the quote coin — no ``USDTUSDT`` phantom row.
        Even if USDT has both walletBalance and borrowAmount > 0
        (leveraged long across the account), no synthetic position
        is emitted for it; the long is captured via base-coin
        holdings.
        """
        from src.units.accounts import clients
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {
                "coin": "USDT", "walletBalance": "200",
                "borrowAmount": "150",
            },
            {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0"},
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        positions = clients.account_open_positions(_spot_margin_account())
        for p in positions:
            assert p["symbol"] != "USDTUSDT"

    def test_get_positions_endpoint_not_called(self, monkeypatch):
        """Spot-margin must NOT hit the perp ``/v5/position/list``
        endpoint — that returns garbage for spot accounts and
        contributed to the pre-T5 phantom-positions warning.
        """
        from src.units.accounts import clients
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {"coin": "BTC", "borrowAmount": "0.001"},
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        clients.account_open_positions(_spot_margin_account())
        assert client.get_positions_calls == []

    def test_wallet_read_failure_returns_empty(self, monkeypatch):
        """Best-effort: any exception from ``get_wallet_balance`` is
        logged and returns ``[]`` (not None — None is reserved for
        the upstream creds-missing path so the reconciler can
        distinguish "could not read" from "exchange flat", and the
        synthesis-time failure looks like flat to the caller).
        """
        from src.units.accounts import clients

        class _Boom:
            def get_wallet_balance(self, **_):
                raise RuntimeError("network down")

        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: _Boom(),
        )
        positions = clients.account_open_positions(_spot_margin_account())
        assert positions == []


# ---------------------------------------------------------------------------
# Cash-spot regression — pre-T5 behaviour preserved
# ---------------------------------------------------------------------------


class TestCashSpotUnchanged:
    """Cash-spot accounts (``market_type: spot``, including the
    operator's ``bybit_1``) keep returning ``[]`` byte-for-byte.
    Wallet balances on cash spot are NOT positions — they are
    free coin holdings the operator funded with cash USDT.
    """

    def test_cash_spot_with_btc_holdings_returns_empty(self, monkeypatch):
        from src.units.accounts import clients
        # Wallet has BTC AND USDT, but this is cash-spot — no borrow,
        # no synthetic positions.
        client = _FakeBybitClient(wallet_response=_wallet_with([
            {"coin": "USDT", "walletBalance": "1000"},
            {"coin": "BTC", "walletBalance": "0.05"},
        ]))
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: client,
        )
        positions = clients.account_open_positions(_cash_spot_account())
        assert positions == []

    def test_cash_spot_does_not_call_wallet_endpoint(self, monkeypatch):
        """Cash spot returns [] WITHOUT making any wallet read —
        important because the call would slow the reconciler tick
        for accounts that have no exchange-side positions to find.
        """
        from src.units.accounts import clients

        wallet_calls = []

        class _SpyClient:
            def get_wallet_balance(self, **kw):
                wallet_calls.append(kw)
                return {"result": {"list": []}}

            def get_positions(self, **kw):
                return {"result": {"list": []}}

        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: _SpyClient(),
        )
        clients.account_open_positions(_cash_spot_account())
        assert wallet_calls == []


# ---------------------------------------------------------------------------
# Linear / failure paths — unchanged from BUG-042 PR1 contract
# ---------------------------------------------------------------------------


class TestRegressionUnchanged:
    def test_missing_creds_returns_none(self, monkeypatch):
        """``bybit_client_for`` returning None → None (existing
        contract — distinguishes "could not read" from "flat")."""
        from src.units.accounts import clients
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _account: None,
        )
        assert clients.account_open_positions(_spot_margin_account()) is None
        assert clients.account_open_positions(_cash_spot_account()) is None

    def test_non_dict_account_returns_none(self):
        from src.units.accounts import clients
        assert clients.account_open_positions(None) is None
        assert clients.account_open_positions("not a dict") is None

    def test_unsupported_exchange_returns_none(self):
        from src.units.accounts import clients
        assert clients.account_open_positions({
            "exchange": "kraken", "market_type": "spot-margin",
        }) is None
